"""Template-driven browser half of Facebook Live; FFmpeg stays in the service.

Unlike upload tasks this does not inherit BaseTask's screenshot evidence: Live
Producer displays stream credentials. Only safe workflow/template metadata is
returned; screenshots and OCR are transient.
"""
from pathlib import Path
import time
from urllib.parse import urlsplit

from engine.brain_runtime import CapabilityResult, WorkflowInterpreter
from engine.container_client import ContainerClient
from engine.live_brain import resolve_live_brain
from engine.runtime_paths import automation_dir
from engine.vision import VisionEngine
from modules.live_post_details import LivePostDetails
from modules.live_pinned_comment import LivePinnedComment
from modules.live_producer import LiveProducerTemplates, label, phrase


class FacebookLiveTask:
    def __init__(self, profile_id, brain_package=None):
        self.client = ContainerClient(profile_id)
        self.vision = VisionEngine(self.client)
        self.package = brain_package or resolve_live_brain(Path(__file__).resolve().parents[1], automation_dir())
        self.layouts = LiveProducerTemplates(self.package)
        self.template_id = None
        self.state = 'unknown'
        self.publish_clicked = False

    def capture(self, action):
        image = self.client.screenshot()
        h, w = image.shape[:2]
        names = ['sidebar', 'footer']
        if action == 'prepare':
            names += ['setup_left', 'setup_center', 'details', 'home']
        regions = {}
        for name in names:
            x, y, rw, rh = self.layouts.config['regions'][name]
            regions[name] = self.vision.read_text(image, region=(int(x*w), int(y*h), int(rw*w), int(rh*h)), min_confidence=.55)
        if action == 'start' and not any(label(i) in self.layouts.labels('go_live') for i in regions['footer']):
            # The camera icon can lower OCR confidence for the whole footer line.
            # Read just the button's text, still inside the protected footer.
            focused = self.vision.read_text(image, region=(int(.105*w), int(.94*h), int(.055*w), int(.05*h)), min_confidence=.55)
            regions['footer'] += [i for i in focused if label(i) in self.layouts.labels('go_live')]
        frame = self.layouts.observe(image, regions)
        self.template_id = frame.template_id
        return frame

    def click(self, item):
        x, y = item['center']
        self.client.exec_cmd(['xdotool', 'mousemove', str(x), str(y), 'click', '1'])

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
            self.client.navigate_to(producer_url)
        deadline = time.monotonic() + self.layouts.config['timeouts']['prepare']
        clicked = set()
        details_written = False
        while time.monotonic() < deadline:
            frame = self.capture('prepare')
            if frame.signals['active_surface'] or frame.signals['live_notice']:
                raise RuntimeError('Facebook already has a live broadcast. End it before starting another.')
            if frame.signals['ended_surface']:
                raise RuntimeError('This Live Producer broadcast has ended. Prepare a new broadcast before starting.')
            if frame.signals['setup_panel'] and getattr(self, 'title', '') and not details_written:
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
                if getattr(self, 'pinned_comment', None) is not None:
                    frame = LivePinnedComment(self).set(frame, self.pinned_comment)
                self.template_id = frame.template_id
                self.state = 'prepared'
                return
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
                    self.click(matches[0])
            time.sleep(1)
        raise RuntimeError('Facebook did not confirm the broadcast. Check Live Producer; do not start another stream until this session is resolved.')

    def end(self):
        deadline = time.monotonic() + self.layouts.config['timeouts']['end']
        clicked = confirmed = False
        while time.monotonic() < deadline:
            frame = self.capture('end')
            if self.layouts.permits(frame, 'verify_publication', state='ended'):
                self.template_id = frame.template_id
                self.state = 'ended'
                return
            if not clicked and self.layouts.permits(frame, 'click_candidate', target='end_live'):
                candidates = self.layouts.controls(frame, 'end_live')
                fresh = self.capture('end')
                matches = self.layouts.controls(fresh, 'end_live')
                if self.layouts.permits(fresh, 'click_candidate', target='end_live') and len(candidates) == len(matches) == 1 and self.same(candidates[0], matches[0]):
                    clicked = True
                    self.click(matches[0])
            elif clicked and not confirmed:
                image = frame.image
                h, w = image.shape[:2]
                x, y, rw, rh = self.layouts.config['regions']['dialog']
                dialog = self.vision.read_text(image, region=(int(x*w), int(y*h), int(rw*w), int(rh*h)), min_confidence=.75)
                if phrase(dialog, 'end live') or phrase(dialog, 'are you sure'):
                    buttons = [i for i in dialog if label(i).replace('iive', 'live') in self.layouts.labels('confirm_end')]
                    if len(buttons) == 1:
                        # Reobserve the dialog before its final confirmation.
                        fresh = self.client.screenshot()
                        items = self.vision.read_text(fresh, region=(int(x*w), int(y*h), int(rw*w), int(rh*h)), min_confidence=.75)
                        matches = [i for i in items if label(i).replace('iive', 'live') in self.layouts.labels('confirm_end')]
                        if (phrase(items, 'end live') or phrase(items, 'are you sure')) and len(matches) == 1 and self.same(buttons[0], matches[0]):
                            confirmed = True
                            self.click(matches[0])
            time.sleep(1)
        raise RuntimeError('The stream is stopping, but Facebook did not confirm the broadcast ended. Check Live Producer.')

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
        if action not in {'prepare', 'start', 'end'}:
            raise ValueError('Unknown Live browser action')
        if action != 'prepare':
            url = urlsplit(self.client.get_current_url() or '')
            if url.hostname not in {'facebook.com', 'www.facebook.com'} or not url.path.startswith('/live/producer'):
                raise RuntimeError('Keep Facebook Live Producer open while broadcasting')
        result = WorkflowInterpreter(self.package, self).run({'action': action, 'producer_url': producer_url})
        if result.terminal_state != 'completed':
            raise RuntimeError('Live workflow requires review before continuing')
        return {'success': True, 'brain': self.package.metadata(), 'template_id': self.template_id, 'state': self.state}
