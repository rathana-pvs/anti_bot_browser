"""Visual, bounded navigation checks. An old feed is never taken for a new post: readiness only lets the
scan read the page, and the scan accepts a brand-new top post alone."""
import time

import cv2
import numpy as np


# Chrome's reload/stop glyphs at the standard Linux toolbar position. Unknown
# browser chrome is never read as idle; it is waited out like loading.
_GLYPHS = {
    'idle': [
        '                ', '     ++#++   ++ ', '    #######+ #+ ',
        '   ##+   +##+#+ ', '  +#+      +##+ ', '  #+     +++##+ ',
        ' +#      #####+ ', ' +#       ++++  ', ' +#             ',
        ' +#             ', '  #+        +#  ', '  +#+      +#+  ',
        '   ##++  +###   ', '    +######+    ', '      ++++      ',
    ],
    'loading': [
        '                ', '                ', '   +        +   ',
        '  +#+      +#+  ', '   +#+    +#+   ', '    +#+  +#+    ',
        '     +#++#+     ', '      +##+      ', '      +##+      ',
        '     +#++#+     ', '    +#+  +#+    ', '   +#+    +#+   ',
        '  +#+      +#+  ', '   +        +   ', '                ',
    ],
}

# The current Linux Chrome reload icon has a shorter arrow and a thicker ring
# than the original toolbar glyph. Keep both explicit shapes: lowering the
# match threshold would also admit unknown controls as proof of readiness.
_RELOAD_GLYPH = [
    '                ', '                ', '     ###### ##  ',
    '    ##########  ', '   ###    ####  ', '  ###    #####  ',
    '  ##     #####  ', '  ##            ', '  ##        ##  ',
    '  ##        ##  ', '  ###      ###  ', '   ###    ###   ',
    '    ########    ', '     ######     ', '                ',
]
_TEMPLATES = {
    state: [np.array([[c != ' ' for c in row] for row in rows])
            for rows in ([glyph, _RELOAD_GLYPH] if state == 'idle' else [glyph])]
    for state, glyph in _GLYPHS.items()
}


def browser_navigation_state(screen):
    """Recognize reload versus stop, including common display scaling factors."""
    if screen is None or not isinstance(screen, np.ndarray):
        return 'unknown'
    scores = {'idle': 0., 'loading': 0.}
    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    for scale in (1., 1.25, 1.5, 2.):
        x, y, w, h = (round(n * scale) for n in (87, 55, 16, 15))
        crop = gray[y:y+h, x:x+w]
        if crop.shape != (h, w):
            continue
        crop = cv2.resize(crop, (16, 15)).astype(float)
        background = np.median(np.concatenate((crop[0], crop[-1], crop[:, 0], crop[:, -1])))
        ink = np.abs(crop - background) > 35
        for state, templates in _TEMPLATES.items():
            for template in templates:
                union = (ink | template).sum()
                scores[state] = max(scores[state], float((ink & template).sum()) / max(1, union))
    winner = max(scores, key=scores.get)
    loser = 'idle' if winner == 'loading' else 'loading'
    return winner if scores[winner] >= .60 and scores[winner] - scores[loser] >= .12 else 'unknown'


# Chrome's toolbar is a hint, not a gate: Facebook can leave a request hanging so the stop cross stays up
# long after the profile has drawn. Past this wait the page itself decides. A stale or half-drawn profile
# is still safe to read, since the scan that follows only accepts a brand-new top post.
NAVIGATION_HINT_S = 8.
# Without Chrome's word that loading finished, the page must hold still longer.
UNCONFIRMED_SETTLE_S = 4.
# A profile that never holds still (an autoplaying video, a live counter) is read anyway after this.
UNSETTLED_READ_S = 6.
_PROFILE_LABELS = {'posts', 'reels', 'photos', 'about', 'followers', 'list view', 'grid view', 'all', 'mentions'}


def _profile_visible(task, screen, gallery):
    items = task.vision.read_text(screen, min_confidence=.55)
    labels = {' '.join(i.get('text', '').casefold().split()) for i in items}
    gallery_visible = 'reels' in labels and bool(labels & {'all', 'photos', 'about'})
    if gallery:
        return gallery_visible
    # At the top of a tall profile cover the Posts toolbar can still be below the fold, and OCR can
    # miss a tab, so any two of the profile's own labels anchor it.
    return gallery_visible or len(labels & _PROFILE_LABELS) >= 2


def wait_for_profile_ready(task, *, timeout=20., poll=1., settle=2., gallery=False):
    """A drawn profile, read once the page holds still. Chrome's loading cross is waited out for
    NAVIGATION_HINT_S, then ignored."""
    started = time.monotonic()
    deadline = started + timeout
    previous = None
    stable_since = None
    judging_since = None
    hint_logged = False
    last = None
    reason = 'profile_navigation_unrecognized'
    while time.monotonic() < deadline:
        now = time.monotonic()
        last = task.client.screenshot()
        state = browser_navigation_state(last)
        if state != 'idle' and now - started < NAVIGATION_HINT_S:
            reason = 'profile_navigation_loading' if state == 'loading' else 'profile_navigation_unrecognized'
            previous = stable_since = judging_since = None
        else:
            if state != 'idle' and not hint_logged:
                task.log('INFO', f'Chrome still shows {state} after {NAVIGATION_HINT_S:g} s; reading the profile from the page itself.')
                hint_logged = True
            needed = settle if state == 'idle' else max(settle, UNCONFIRMED_SETTLE_S)
            h, w = last.shape[:2]
            content = cv2.resize(last[int(.16*h):int(.66*h), int(.28*w):int(.95*w)], (160, 90)).astype(float)
            stable = previous is not None and np.mean(np.abs(content - previous)) < 1.5
            if not stable:
                stable_since = now
            previous = content
            judging_since = judging_since if judging_since is not None else now
            settled = stable and now - stable_since >= needed
            restless = now - judging_since >= UNSETTLED_READ_S
            reason = 'profile_content_unsettled'
            if settled or restless:
                if _profile_visible(task, last, gallery):
                    why = 'profile_ready' if state == 'idle' and settled else (
                        'profile_ready_browser_still_loading' if settled else 'profile_ready_unsettled')
                    task.reel_profile_readiness = {'accepted': True, 'reason': why}
                    if why != 'profile_ready':
                        task.log('INFO', 'Reel verification readiness: ' + why)
                    return True
                reason = 'profile_not_visible'
        time.sleep(min(poll, max(0., deadline - time.monotonic())))
    task.reel_profile_readiness = {'accepted': False, 'reason': reason}
    task.log('WARN', 'Reel verification readiness stopped: ' + reason)
    task.capture_evidence('reel_profile_not_ready', last, verification=task.reel_profile_readiness)
    return False
