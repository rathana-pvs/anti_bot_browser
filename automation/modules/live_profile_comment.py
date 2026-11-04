"""Timestamp-only profile fallback before a Live comment has been submitted."""
import time

from engine.vision import VisionEngine
from live_comment_status import LiveCommentFailure, COMMENT_VERIFY_SECONDS
from modules.reel_top_post import first_header_region, retry_unreadable_header
from modules.post_timestamp import parse_header_timestamp
from skills.comment import FirstComment, first_comment_search_regions


class LiveProfileComment:
    MAX_POST_AGE_SECONDS = 180
    _first_comment_search_regions = staticmethod(first_comment_search_regions)

    def __init__(self, task):
        self.task = task
        self.client, self.vision = task.client, task.vision
        self.human = task.live_input.human
        self.checks = {}

    def observe(self, screen=None):
        # Keep checks passive: reading the address bar would steal the comment
        # editor's focus and replace the clipboard during native text entry.
        screen = self.client.screenshot() if screen is None else screen
        h, w = screen.shape[:2]
        self.height = h
        items = self.vision.read_text(screen, region=(int(.28*w), int(.08*h), int(.67*w), int(.92*h)), min_confidence=.15)
        self.task.observe_failure_evidence(screen, items, 'live_profile_comment', self.checks)
        return items

    @staticmethod
    def inputs(items):
        fields = [i for i in items if VisionEngine.is_comment_input_text(i.get('text', ''))]
        return sorted(fields, key=lambda i: i['center'][1])[:1]

    def find(self, deadline):
        # Leave the existing entry and delivery budget intact. Never refresh or
        # switch surfaces after submission has started.
        limit = min(time.monotonic() + 30, deadline - COMMENT_VERIFY_SECONDS - 15)
        if time.monotonic() >= limit:
            raise LiveCommentFailure('profile_post_not_found')
        self.task.report('live_profile_comment_fallback_requested')
        self.human.navigate('https://www.facebook.com/me')
        self.human.key_press('ctrl+home', check=False)
        time.sleep(.8)
        recent = False
        for _ in range(6):
            if time.monotonic() >= limit:
                break
            screen = self.client.screenshot()
            items = self.observe(screen)
            h, w = screen.shape[:2]
            region, toolbar_seen = first_header_region(items, w, h)
            if region is not None:
                header = self.vision.read_text(screen, region=region, min_confidence=.55)
                _, check = retry_unreadable_header(self.vision, screen, region, header)
                if not check['accepted'] or check.get('age_seconds', 9999) > self.MAX_POST_AGE_SECONDS:
                    raise LiveCommentFailure('profile_post_not_recent')
                # Exclude profile composer fields above the first post.
                floor = region[1]
                authors = sorted((i for i in header if i.get('bounds')
                    and region[1] <= i['bounds'][1] < region[1] + .065*h
                    and parse_header_timestamp(i.get('text', '')) is None), key=lambda i: i['bounds'][1])
                marker = authors[0]['text'].casefold() if authors else None
                marker_y = authors[0]['bounds'][1] if authors else -h
                recent = True
                self.checks = {'profile_first_post_recent':True, 'verified_post_age_seconds':check['age_seconds']}
                self.task.report('live_profile_recent_post_verified')
                break
            self.human.move_to(int(.65*w), int(.55*h))
            self.human.scroll('down', notches=1 if toolbar_seen else 2)
            time.sleep(.8)
        if not recent:
            raise LiveCommentFailure('profile_post_not_found')

        # Reuse Reel's first-post comment-field/action locator and small scrolls.
        # The first card is already timestamp-verified; do not refresh or search
        # a later post when that first header is old or unreadable.
        for scan in range(14):
            if time.monotonic() >= limit:
                break
            screen = self.client.screenshot()
            items = self.observe(screen)
            # Do not scroll through an unreadable/missing first comment field
            # and accidentally select the next post by this same profile.
            markers = sorted((i['bounds'][1] for i in items if i.get('bounds')
                and marker and i.get('text', '').casefold() == marker))
            current = [y for y in markers if y <= marker_y + .02*h]
            if current:
                marker_y = min(current)
                following = [y for y in markers if y > marker_y + .08*h]
            else:
                marker_y = -h
                following = markers
            boundary = min(following) if following else h
            fields = [i for i in self.inputs(items) if floor <= i['bounds'][1] < boundary]
            if fields:
                self.task.report('live_profile_comment_input_found')
                return items, fields[0]
            if following:
                raise LiveCommentFailure('profile_input_missing')
            _, action = FirstComment(self).find_targets(screen)
            self.human.move_to(int(.65*w), int(.55*h))
            self.human.scroll('down', notches=1 if action else 2)
            floor = 0  # The verified first header can leave the screen while its video scrolls.
            time.sleep(.8)
        raise LiveCommentFailure('profile_input_missing')
