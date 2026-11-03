"""Dismiss the known Meta website-ad promotion above the first profile post."""
import re


def find_meta_feed_promotion(screen, items, header_region):
    """Require the exact promotion copy and a visually confirmed circular X."""
    import cv2
    import numpy as np

    if header_region is None:
        return None
    height, width = screen.shape[:2]
    x, y, w, h = header_region
    scale = height / 1080
    rows = [i for i in items if i.get('bounds') and i.get('confidence', 0) >= .55
            and x <= i['bounds'][0] < x + w and y <= i['bounds'][1] < y + h]
    normalize = lambda i: re.sub(r'[^a-z0-9]+', ' ', i.get('text', '').casefold()).strip()
    titles = [i for i in rows if normalize(i) == 'drive more traffic to your website']
    labels = [i for i in rows if normalize(i) == 'meta']
    for title in titles:
        top = title['bounds'][1]
        if not any(0 < top - label['bounds'][1] <= 80 * scale for label in labels):
            continue
        # Only the card header's far-right close control; never the browser X
        # or the post menu below this promotion.
        left, right = max(x, int(x + w - 100 * scale)), min(width, x + w)
        upper, lower = max(y, int(top - 75 * scale)), min(height, top)
        gray = cv2.cvtColor(screen[upper:lower, left:right], cv2.COLOR_BGR2GRAY)
        if not gray.size:
            continue
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, max(1, 20 * scale),
                                  param1=60, param2=16, minRadius=max(5, int(12 * scale)),
                                  maxRadius=max(6, int(24 * scale)))
        matches = []
        for cx, cy, radius in ([] if circles is None else circles[0]):
            cx, cy = int(round(cx)), int(round(cy))
            half = max(5, int(round(radius * .45)))
            crop = gray[max(0, cy-half):cy+half+1, max(0, cx-half):cx+half+1]
            if crop.shape != (2*half+1, 2*half+1):
                continue
            crop = cv2.resize(crop, (17, 17))
            expected = np.zeros((17, 17), np.uint8)
            cv2.line(expected, (2, 2), (14, 14), 1, 2)
            cv2.line(expected, (14, 2), (2, 14), 1, 2)
            # Support light and dark cards, but require the diagonal X shape.
            for actual in (crop < 110, crop > 170):
                overlap = np.count_nonzero(actual & (expected > 0))
                union = np.count_nonzero(actual | (expected > 0))
                if union and overlap / union >= .45:
                    matches.append((cx + left, cy + upper))
                    break
        if len(matches) == 1:
            return {'center': matches[0], 'close_confirmed': True}
        return {'center': None, 'close_confirmed': False}
    return None


def dismiss_meta_feed_promotion(task, screen, items, header_region):
    """Return True while this card blocks the first post, with at most 2 clicks."""
    match = find_meta_feed_promotion(screen, items, header_region)
    if match is None:
        return False
    attempts = getattr(task, '_meta_feed_promotion_close_attempts', 0)
    if match['close_confirmed'] and attempts < 2:
        target = match['center']
        task.capture_evidence('meta_feed_promotion_before_close', screen, target=list(target))
        task.log('INFO', 'Closing the detected Meta website-ad promotion before checking the first post.')
        task.click_reversible(target, label='meta_feed_promotion_close', max_offset_px=1)
        task._meta_feed_promotion_close_attempts = attempts + 1
    else:
        task.log('WARN', 'Meta promotion blocks the first post; its close control was not confirmed or remained after two attempts.')
    return True
