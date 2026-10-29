"""Passive Reel-gallery fallback requiring media, owner and recent-header proof."""
import re
import time
from datetime import datetime, timezone

from .post_timestamp import parse_header_timestamp
from .publication_result_verifier import ImagePublicationResultVerifier
from .reel_profile_readiness import browser_navigation_state, wait_for_profile_ready


def normalized(text):
    return ' '.join(re.findall(r'[^\W_]+', (text or '').casefold()))


def reels_tab(items, height):
    """Only the plural Reels tab beside other profile navigation labels."""
    candidates = []
    for item in items:
        if normalized(item.get('text')) != 'reels' or item.get('confidence', 0) < .75:
            continue
        _, y = item['center']
        peers = {normalized(i.get('text')) for i in items
                 if i.get('confidence', 0) >= .75 and i.get('center')
                 and abs(i['center'][1] - y) < .018 * height
                 and normalized(i.get('text')) in {'all', 'posts', 'about', 'photos', 'followers'}}
        if len(peers) >= 2:
            candidates.append(item)
    return candidates[0] if len(candidates) == 1 else None


def viewer_identity(items, author, caption, height):
    """Bind recency and caption to a single matching author in the Reel viewer."""
    strong = [i for i in items if i.get('bounds') and i.get('confidence', 0) >= .55]
    authors = [i for i in strong if normalized(i.get('text')) == normalized(author)]
    if not normalized(author) or len(authors) != 1:
        return {'accepted': False, 'reason': 'reel_owner_unconfirmed'}
    owner = authors[0]
    x, y, right, bottom = owner['bounds']
    line_height = max(10, bottom - y)
    header = [i for i in strong if .5 * line_height <= i['bounds'][1] - y <= 2.2 * line_height
              and x - .03 * height <= i['bounds'][0] <= right + .25 * height]
    stamps = [parse_header_timestamp(i.get('text')) for i in header]
    stamps = [s for s in stamps if s is not None]
    if not stamps or any(not s.recent for s in stamps) or len({s.age_seconds for s in stamps}) != 1:
        return {'accepted': False, 'reason': 'reel_recent_header_unconfirmed'}
    body = sorted((i for i in strong if bottom <= i['bounds'][1] <= bottom + .35 * height
                   and x - .03 * height <= i['bounds'][0] <= right + .35 * height),
                  key=lambda i: (i['bounds'][1], i['bounds'][0]))
    words = normalized(caption).split()
    prefix = ' '.join(words[:12])
    caption_matched = len(words) >= 4 and prefix in normalized(' '.join(i.get('text', '') for i in body))
    return {'accepted': True, 'reason': 'recent_reel_owner_verified',
            'age_seconds': stamps[0].age_seconds, 'caption_matched': caption_matched}


class ReelGalleryVerifier:
    def __init__(self, task):
        self.task = task
        self.media = ImagePublicationResultVerifier(task)

    def record(self, screen, reason, **checks):
        self.task.reel_gallery_check = {'accepted': False, 'reason': reason, **checks}
        self.task.log('INFO', 'Reel gallery verification: ' + reason)
        self.task.capture_evidence('reel_gallery_verification', screen,
                                   verification=self.task.reel_gallery_check)
        return False

    def verify(self):
        task = self.task
        author = getattr(task, '_reel_profile_author', None)
        if not author:
            return self.record(task.client.screenshot(), 'reel_owner_unavailable')
        if not task._allow_reel_profile_navigation():
            return False
        task.client.exec_cmd(['xdotool', 'key', 'ctrl+home'], check=False)
        time.sleep(.8)
        tab = None
        for scan in range(3):
            screen = task.client.screenshot()
            if browser_navigation_state(screen) != 'idle':
                return self.record(screen, 'gallery_navigation_not_ready')
            items = task.vision.read_text(screen, min_confidence=.55)
            tab = reels_tab(items, screen.shape[0])
            if tab:
                break
            if scan < 2:
                task.human.scroll('down', notches=2)
                time.sleep(1.)
        if not tab:
            return self.record(screen, 'reels_tab_not_found')
        # Re-read the same navigation row immediately before the reversible click.
        fresh = task.client.screenshot()
        confirmed = reels_tab(task.vision.read_text(fresh, min_confidence=.55), fresh.shape[0])
        if (browser_navigation_state(fresh) != 'idle' or not confirmed
                or abs(confirmed['center'][0] - tab['center'][0]) > 8
                or abs(confirmed['center'][1] - tab['center'][1]) > 8):
            return self.record(fresh, 'reels_tab_changed')
        task.human.click(*confirmed['center'])
        if not wait_for_profile_ready(task, gallery=True):
            return False
        screen = task.client.screenshot()
        # Match the source video's rendered cover, never an arbitrary tile.
        match = self.media._matching_image_bounds(screen, media_path=task.video_path)
        if match is None or match[1] <= confirmed['bounds'][3] + .05 * screen.shape[0]:
            return self.record(screen, 'reel_media_not_matched')
        x1, y1, x2, y2 = match
        h, w = screen.shape[:2]
        if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
            return self.record(screen, 'reel_media_partially_visible')
        task.capture_evidence('before_matched_reel_open', screen, media_bounds=list(match))
        # Recheck identity on a fresh frame so a changing grid cannot move the target.
        fresh = task.client.screenshot()
        next_match = self.media._matching_image_bounds(fresh, media_path=task.video_path)
        if browser_navigation_state(fresh) != 'idle' or next_match is None or max(abs(a-b) for a, b in zip(match, next_match)) > 8:
            return self.record(fresh, 'reel_media_changed')
        task.human.click(round((x1+x2)/2), round((y1+y2)/2))
        deadline = time.monotonic() + 15.
        check = {'reason': 'reel_viewer_loading'}
        while time.monotonic() < deadline:
            time.sleep(1.)
            screen = task.client.screenshot()
            if browser_navigation_state(screen) != 'idle':
                check = {'reason': 'reel_viewer_navigation_not_ready'}
                continue
            valid, url = task.validate_facebook_permalink(task.client.get_current_url() or '', post_type='reel')
            if not valid:
                check = {'reason': 'reel_permalink_unconfirmed'}
                continue
            check = viewer_identity(task.vision.read_text(screen, min_confidence=.55),
                                    author, task.caption, screen.shape[0])
            if not check['accepted']:
                continue
            media_matched = self.media._matching_image_bounds(screen, media_path=task.video_path) is not None
            if not check['caption_matched'] and not media_matched:
                check = {'accepted': False, 'reason': 'reel_viewer_content_mismatch'}
                continue
            task._verified_reel_gallery_permalink = {
                'post_url': url, 'post_url_verified_at': datetime.now(timezone.utc).isoformat(),
                'post_match_confidence': None, 'permalink_status': 'captured',
                'permalink_missing': False, 'permalink_recovery_attempted': False,
            }
            task.reel_gallery_check = {**check, 'reason': 'matching_recent_reel_verified',
                                       'media_matched': media_matched}
            task.capture_evidence('reel_gallery_confirmed', screen, verification=task.reel_gallery_check)
            task.log('SUCCESS', 'The Reels tab confirmed the matching video with the same author and a recent header.')
            return True
        return self.record(screen, check['reason'])
