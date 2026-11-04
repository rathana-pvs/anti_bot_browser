"""Template-driven browser half of Facebook Live; FFmpeg stays in the service.

Live failure evidence retains the full browser frame, including visible stream
credentials. OCR and clipboard payloads are not copied into diagnostic JSON.
"""
from pathlib import Path
import time
import random
from urllib.parse import urlsplit

from engine.brain_runtime import CapabilityResult, WorkflowInterpreter
from engine.container_client import ContainerClient
from engine.live_brain import resolve_live_brain
from engine.runtime_paths import automation_dir
from engine.vision import VisionEngine
from modules.live_post_details import LivePostDetails
from modules.live_input import LiveInput
from modules.live_evidence import LiveFailureEvidence
from modules.live_comment import LiveComment
from live_account_status import LiveAccountBlocked
from modules.live_stream_key import LiveStreamKey
from modules.live_producer import LiveProducerTemplates, control_label, enabled_color, label, phrase


class FacebookLiveTask:
    def __init__(self, profile_id, brain_package=None):
        self.client = ContainerClient(profile_id)
        self.failure_evidence = LiveFailureEvidence(profile_id)
        self.vision = VisionEngine(self.client)
        self.input_cancelled = False
        self.live_input = LiveInput(self, profile_id)
        self.package = brain_package or resolve_live_brain(Path(__file__).resolve().parents[1], automation_dir())
        self.layouts = LiveProducerTemplates(self.package)
        self.template_id = None
        self.state = 'unknown'
        self.publish_clicked = False

    def report(self, event):
        callback = getattr(self, 'on_event', None)
        if callback:
            callback(event)

    def observe_failure_evidence(self, image, items, source, checks=None):
        evidence = getattr(self, 'failure_evidence', None)
        if evidence is not None:
            try:
                evidence.observe(image, items, source, checks)
            except Exception:
                # Diagnostics must not affect input/publication decisions or
                # attach an older screenshot to a newer failed observation.
                evidence.observation = None

    def inspect(self):
        """Observe publication state without navigation or publication clicks."""
        frame = self.capture('inspect')
        self.state = ('live' if self.layouts.permits(frame, 'verify_publication', state='live')
                      else 'ended' if self.layouts.permits(frame, 'verify_publication', state='ended')
                      else 'unconfirmed')

    def reject_checkpoint(self, action):
        image = self.client.screenshot()
        h, w = image.shape[:2]
        items = self.vision.read_text(image, region=(int(.3*w), int(.2*h), int(.4*w), int(.6*h)), min_confidence=.65)
        code = 'account_locked' if (phrase(items, 'locked your account') or phrase(items, 'unlock your account')) else 'account_checkpoint'
        self.observe_failure_evidence(image, [], action, {'account_block': code})
        raise LiveAccountBlocked(code)

    def capture(self, action):
        # Screenshots/guards stay passive while a comment editor owns focus.
        image = self.client.screenshot()
        h, w = image.shape[:2]
        names = ['sidebar', 'footer']
        if action == 'prepare':
            names += ['setup_left', 'setup_center', 'details', 'home']
        regions = {}
        for name in names:
            x, y, rw, rh = self.layouts.config['regions'][name]
            regions[name] = self.vision.read_text(image, region=(int(x*w), int(y*h), int(rw*w), int(rh*h)), min_confidence=.55)
        if action == 'start' and not any(control_label(i) in self.layouts.labels('go_live') for i in regions['footer']):
            # The camera icon can lower OCR confidence for the whole footer line.
            # Read just the button's text, still inside the protected footer.
            focused = self.vision.read_text(image, region=(int(.105*w), int(.94*h), int(.055*w), int(.05*h)), min_confidence=.55)
            regions['footer'] += [i for i in focused if control_label(i) in self.layouts.labels('go_live')]
        # The full sidebar can merge the small grey Live notice with nearby
        # account/timestamp text. Re-read only its protected sidebar area.
        if (self.layouts.has(regions['sidebar'], 'active_surface')
                and not self.layouts.has(regions['sidebar'], 'live_notice')):
            regions['sidebar'] += self.vision.read_text(
                image, region=(0, int(.18*h), int(.19*w), int(.14*h)), min_confidence=.55)
        frame = self.layouts.observe(image, regions)
        header_probe = {'bounds': [int(.91*w), int(.155*h), int(.97*w), int(.175*h)]}
        if (action in {'start', 'end', 'inspect'} and not frame.signals.get('end_control')
                and enabled_color(image, header_probe, 'red')):
            regions['live_header'] = self.vision.read_text(image, region=(0, int(.14*h), int(.25*w), int(.06*h)), min_confidence=.65)
            regions['live_header_end'] = self.vision.read_text(image, region=(int(.8*w), int(.14*h), int(.2*w), int(.06*h)), min_confidence=.65)
            frame = self.layouts.observe(image, regions)
        # A restriction dialog can appear above an otherwise valid ended page.
        # Read its protected center separately from the video and comment text.
        restriction = (self.vision.read_text(image, region=(int(.32*w), int(.42*h), int(.36*w), int(.24*h)), min_confidence=.65)
                       if action in {'prepare', 'inspect'} else [])
        if (phrase(restriction, 'confirm your identity')
                and phrase(restriction, 'before you can publish as this page')):
            self.observe_failure_evidence(image, [], action, {'account_block': 'identity_verification_required'})
            raise LiveAccountBlocked('identity_verification_required')
        if phrase(restriction, 'locked your account') and phrase(restriction, 'steps to get you back on facebook'):
            self.observe_failure_evidence(image, [], action, {'account_block':'account_locked'})
            raise LiveAccountBlocked('account_locked')
        self.observe_failure_evidence(image, [item for items in regions.values() for item in items], action,
                                      {'template_id': frame.template_id})
        self.template_id = frame.template_id
        return frame

    def click(self, item):
        self.live_input.human.click(*item['center'])

    @staticmethod
    def same(a, b):
        return abs(a['center'][0]-b['center'][0]) + abs(a['center'][1]-b['center'][1]) < 12

    def candidates(self, frame, target, region_names):
        result = []
        for name in region_names:
            for item in frame.regions.get(name, []):
                if label(item) in self.layouts.labels(target) and item.get('confidence', 0) >= .8:
                    if not any(self.same(item, prior) for prior in result):
                        result.append(item)
        return result

    def prepare(self, producer_url):
        p, expected = urlsplit(self.client.get_current_url() or ''), urlsplit(producer_url)
        if p.hostname not in {'facebook.com', 'www.facebook.com'} or not p.path.startswith('/live/producer') or (expected.query and p.query != expected.query):
            self.live_input.human.navigate(producer_url)
        deadline = time.monotonic() + self.layouts.config['timeouts']['prepare']
        self.preparation_deadline = deadline
        clicked = set()
        details_written = False
        home_scrolls = 0
        while time.monotonic() < deadline:
            frame = self.capture('prepare')
            if frame.template_id is None:
                h, w = frame.image.shape[:2]
                dialog = self.vision.read_text(frame.image,
                    region=(int(.35*w), int(.3*h), int(.3*w), int(.45*h)), min_confidence=.55)
                if phrase(dialog, 'persistent stream key') and phrase(dialog, 'another live video'):
                    raise RuntimeError('Facebook says this persistent stream key is assigned to another live video. Check and end the existing broadcast, or explicitly reassign the key in Live Producer before retrying.')
            if frame.signals['active_surface'] or frame.signals['live_notice']:
                raise RuntimeError('Facebook already has a live broadcast. End it before starting another.')
            if frame.signals['ended_surface']:
                raise RuntimeError('This Live Producer broadcast has ended. Prepare a new broadcast before starting.')
            if (frame.signals['setup_panel'] and not frame.signals['details_saved']
                    and getattr(self, 'title', '') and not details_written):
                # A prior reversible setup attempt can leave the right panel
                # scrolled below the saved-details marker. Restore the top and
                # reobserve before reopening or overwriting those fields.
                self.live_input.human.key_press('ctrl+Home', clear_modifiers=True)
                time.sleep(.5)
                restored = self.capture('prepare')
                if (not restored.signals['setup_panel'] or restored.signals['active_surface']
                        or restored.signals['ended_surface']):
                    continue
                frame = restored
                if not frame.signals['details_saved']:
                    saved = LivePostDetails(self).fill(frame, self.title, self.caption)
                    details_written = True
                    # The module just observed a saved setup after verified fields.
                    # Reuse that fresh frame instead of repeating every OCR region.
                    if saved is not None and saved.template_id in {'setup_left', 'setup_center', 'broadcast_ready'}:
                        frame = saved
                    else:
                        continue
            if frame.signals['setup_panel'] and not frame.signals['details_saved']:
                raise RuntimeError('Complete the required title and description in Facebook Live Producer before starting the video.')
            if frame.template_id in {'setup_left', 'setup_center', 'broadcast_ready'}:
                self.template_id = frame.template_id
                self.state = 'prepared'
                return
            home = frame.regions.get('home', [])
            home_sidebar = any(label(i) == 'home' and i.get('confidence', 0) >= .8
                               for i in frame.regions.get('sidebar', []))
            home_context = home_sidebar and (frame.signals['home_surface'] or (
                phrase(home, 'go live') and phrase(home, 'create a live video ad')))
            if (home_context and not frame.signals['setup_control']
                    and 'open_setup' not in clicked and home_scrolls < 3):
                # The Home promo cards can push Set up live video below the
                # viewport. Scroll the main content, then detect afresh.
                h, w = frame.image.shape[:2]
                self.live_input.human.move_to(int(.6*w), int(.65*h))
                self.live_input.human.scroll('down', notches=2)
                home_scrolls += 1
                time.sleep(.5)
                continue
            for target, regions in [('open_setup', ['home']), ('streaming_source', ['setup_left', 'setup_center'])]:
                if target in clicked or not self.layouts.permits(frame, 'click_candidate', target=target):
                    continue
                candidates = self.candidates(frame, target, regions)
                if len(candidates) != 1:
                    continue
                fresh = self.capture('prepare')
                matches = self.candidates(fresh, target, regions)
                if fresh.template_id != frame.template_id or len(matches) != 1 or not self.same(candidates[0], matches[0]):
                    continue
                clicked.add(target)
                item = matches[0]
                if target == 'streaming_source':
                    x, y = item['center']
                    height = item['bounds'][3] - item['bounds'][1]
                    item = dict(item, center=(x, y-max(40, min(80, height*3))))
                self.click(item)
                break
            time.sleep(1)
        raise RuntimeError('Prepare this account in Facebook Live Producer: choose the broadcast destination, Streaming software, and complete the post details. Keep that page open, then click Live again.')

    def start(self):
        deadline = time.monotonic() + self.layouts.config['timeouts']['start']
        dialog_confirmed = False
        while time.monotonic() < deadline:
            frame = self.capture('start')
            if self.layouts.permits(frame, 'verify_publication', state='live'):
                self.template_id = frame.template_id
                self.state = 'live'
                return
            if frame.signals['ended_surface']:
                raise RuntimeError('The broadcast ended before Live could be confirmed. Check Facebook before retrying.')
            if not self.publish_clicked and self.layouts.permits(frame, 'request_publish', target='go_live'):
                candidates = self.layouts.controls(frame, 'go_live')
                fresh = self.capture('start')
                matches = self.layouts.controls(fresh, 'go_live')
                if self.layouts.permits(fresh, 'request_publish', target='go_live') and len(candidates) == len(matches) == 1 and self.same(candidates[0], matches[0]):
                    self.publish_clicked = True
                    self.report('go_live_requested')
                    self.click(matches[0])
                    self.report('go_live_click_sent')
            elif self.publish_clicked and not dialog_confirmed and frame.template_id is None:
                dialog_confirmed = self.confirm_go_live_dialog(frame.image)
            time.sleep(1)
        raise RuntimeError('Facebook did not confirm the broadcast. Check Live Producer; do not start another stream until this session is resolved.')

    def confirm_go_live_dialog(self, image):
        """Finish this run's footer Go live click when Facebook suggests a game tag."""
        h, w = image.shape[:2]
        x, y, rw, rh = self.layouts.config['regions']['dialog']
        region = (int(x*w), int(y*h), int(rw*w), int(rh*h))

        def button(screen):
            items = self.vision.read_text(screen, region=region, min_confidence=.75)
            if not phrase(items, 'tag a game'):
                return None
            controls = [item for item in items
                        if control_label(item) in self.layouts.labels('go_live') and enabled_color(screen, item)]
            return controls[0] if len(controls) == 1 else None

        candidate = button(image)
        if candidate is None:
            return False
        # Require the same enabled button on a fresh screen. The caller allows
        # one click, so an unconfirmed dialog ends in review instead of a retry.
        match = button(self.client.screenshot())
        if match is None or not self.same(candidate, match):
            return False
        self.report('go_live_dialog_requested')
        self.click(match)
        self.report('go_live_dialog_click_sent')
        return True

    def end(self):
        deadline = time.monotonic() + self.layouts.config['timeouts']['end']
        clicked = confirmed = False
        while time.monotonic() < deadline:
            frame = self.capture('end')
            if (self.layouts.permits(frame, 'verify_publication', state='ended')
                    or (frame.signals.get('ended_surface') and not frame.signals.get('end_control'))):
                self.template_id = frame.template_id
                self.state = 'ended'
                return
            if not confirmed and (clicked or frame.template_id is None):
                confirmed = self.confirm_end_dialog(frame.image)
                if confirmed:
                    clicked = True
            candidates = self.layouts.controls(frame, 'end_live')
            if not clicked and len(candidates) == 1 and enabled_color(frame.image, candidates[0], 'red'):
                fresh = self.capture('end')
                matches = self.layouts.controls(fresh, 'end_live')
                if (len(matches) == 1 and enabled_color(fresh.image, matches[0], 'red')
                        and self.same(candidates[0], matches[0])):
                    clicked = True
                    self.report('end_live_requested')
                    self.click(matches[0])
                    self.report('end_live_click_sent')
            time.sleep(1)
        raise RuntimeError('The stream is stopping, but Facebook did not confirm the broadcast ended. Check Live Producer.')

    def confirm_end_dialog(self, image):
        h, w = image.shape[:2]
        x, y, rw, rh = self.layouts.config['regions']['dialog']
        region = (int(x*w), int(y*h), int(rw*w), int(rh*h))

        def button(screen):
            items = self.vision.read_text(screen, region=region, min_confidence=.75)
            if not (phrase(items, 'end live') or phrase(items, 'are you sure')):
                return None
            controls = [item for item in items
                        if label(item).replace('iive', 'live') in self.layouts.labels('confirm_end')
                        and (enabled_color(screen, item) or enabled_color(screen, item, 'red'))]
            return controls[0] if len(controls) == 1 else None

        candidate = button(image)
        if candidate is None:
            return False
        # Distinguish the colored action from the identically worded heading,
        # and require its location, label and enabled color on a fresh screen.
        match = button(self.client.screenshot())
        if match is None or not self.same(candidate, match):
            return False
        self.report('end_confirmation_requested')
        self.click(match)
        self.report('end_confirmation_click_sent')
        return True

    def evaluate(self, requirement, context):
        return requirement.get('action') == context['inputs']['action']

    def call(self, capability, parameters, context):
        action = context['inputs']['action']
        if capability != 'wait_for_state' or parameters.get('state') != action or action not in {'prepare', 'start', 'end'}:
            raise RuntimeError('Live workflow requested an unsupported browser operation')
        if action == 'prepare':
            self.prepare(context['inputs']['producer_url'])
        else:
            getattr(self, action)()
        return CapabilityResult()

    def run(self, action, producer_url, title='', caption='', pinned_comment=None):
        self.title, self.caption, self.pinned_comment = title, caption, pinned_comment
        if action not in {'prepare', 'start', 'end', 'inspect', 'comment', 'finish'}:
            raise ValueError('Unknown Live browser action')
        if action != 'prepare':
            url = urlsplit(self.client.get_current_url() or '')
            if url.hostname in {'facebook.com', 'www.facebook.com'} and url.path.startswith('/checkpoint/'):
                self.reject_checkpoint(action)
            if (url.hostname not in {'facebook.com', 'www.facebook.com'}
                    or (action != 'finish' and not url.path.startswith('/live/producer'))):
                raise RuntimeError('Keep Facebook Live Producer open while broadcasting')
        if action == 'inspect':
            self.inspect()
            return {'success': True, 'brain': self.package.metadata(), 'template_id': self.template_id, 'state': self.state}
        if action == 'comment':
            LiveComment(self, after_end=getattr(self, 'comment_after_end', False)).post(pinned_comment)
            return {'success': True, 'brain': self.package.metadata(), 'template_id': self.template_id, 'state': 'commented'}
        if action == 'finish':
            # Optional navigation only: never publish, react or edit anything.
            # Locate the logo in the header rather than clicking fixed pixels.
            image = self.client.screenshot()
            h, w = image.shape[:2]
            position = self.vision.find_template(image[:int(h*.22), :int(w*.2)], 'facebook_logo', threshold=.85)
            outcome = 'logo_missing'
            if position is not None:
                self.live_input.human.click(*position)
                time.sleep(random.uniform(1, 3))
                destination = urlsplit(self.client.get_current_url() or '')
                outcome = 'feed_not_ready'
                if destination.hostname in {'facebook.com', 'www.facebook.com'} and destination.path in {'', '/'}:
                    self.report('optional_finish_news_feed_opened')
                    self.report('optional_finish_warming_started')
                    self.live_input.human.move_to(int(w*random.uniform(.4, .65)), int(h*random.uniform(.3, .6)))
                    self.live_input.human.scroll('down', notches=random.randint(1, 3))
                    time.sleep(random.uniform(2, 4))
                    self.report('optional_finish_warming_completed')
                    outcome = 'warmed'
            return {'success': True, 'brain': self.package.metadata(), 'template_id': self.template_id, 'state': 'finished',
                    'finish_outcome': outcome}
        result = WorkflowInterpreter(self.package, self).run({'action': action, 'producer_url': producer_url})
        if result.terminal_state != 'completed':
            raise RuntimeError('Live workflow requires review before continuing')
        result = {'success': True, 'brain': self.package.metadata(), 'template_id': self.template_id, 'state': self.state}
        if action == 'prepare':
            result['prepared_stream_key'] = LiveStreamKey(self).read()
        return result
