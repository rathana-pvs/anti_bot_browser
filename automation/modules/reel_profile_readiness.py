"""Visual, bounded navigation checks; never infer readiness from an old feed."""
import time

import cv2
import numpy as np


# Chrome's reload/stop glyphs at the standard Linux toolbar position. Unknown
# browser chrome fails closed rather than treating absence of a spinner as idle.
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


def wait_for_profile_ready(task, *, timeout=20., poll=1., settle=2., gallery=False):
    """Require idle browser chrome and stable profile content before any scan."""
    deadline = time.monotonic() + timeout
    previous = None
    stable_since = None
    last = None
    reason = 'profile_navigation_unrecognized'
    while time.monotonic() < deadline:
        last = task.client.screenshot()
        state = browser_navigation_state(last)
        if state != 'idle':
            reason = 'profile_navigation_loading' if state == 'loading' else 'profile_navigation_unrecognized'
            previous = stable_since = None
        else:
            h, w = last.shape[:2]
            content = cv2.resize(last[int(.16*h):int(.66*h), int(.28*w):int(.95*w)], (160, 90))
            stable = previous is not None and np.mean(np.abs(content.astype(float) - previous)) < 1.5
            if not stable:
                stable_since = time.monotonic()
            previous = content.astype(float)
            reason = 'profile_content_unsettled'
            if stable and time.monotonic() - stable_since >= settle:
                items = task.vision.read_text(last, min_confidence=.55)
                labels = {' '.join(i.get('text', '').casefold().split()) for i in items}
                profile_visible = 'posts' in labels and bool(labels & {'reels', 'photos', 'list view'})
                gallery_visible = 'reels' in labels and bool(labels & {'all', 'photos', 'about'})
                # At the top of a tall profile cover, the Posts toolbar can
                # still be below the fold. The profile tabs also anchor readiness.
                ready = gallery_visible if gallery else profile_visible or gallery_visible
                if ready:
                    task.reel_profile_readiness = {'accepted': True, 'reason': 'profile_ready'}
                    return True
                reason = 'profile_not_visible'
        time.sleep(min(poll, max(0., deadline - time.monotonic())))
    task.reel_profile_readiness = {'accepted': False, 'reason': reason}
    task.log('WARN', 'Reel verification readiness stopped: ' + reason)
    task.capture_evidence('reel_profile_not_ready', last, verification=task.reel_profile_readiness)
    return False
