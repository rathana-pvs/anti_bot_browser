"""Locate only the first post header below the profile's Posts/List view toolbar."""
import re

from .post_timestamp import parse_header_timestamp


def bounds(item):
    return item.get('bounds')


def text(item):
    return re.sub(r'\s+', ' ', item.get('text', '').casefold()).strip()


def first_header_region(items, width, height):
    strong = [i for i in items if i.get('confidence', 0) >= .55 and bounds(i)]
    titles = sorted((i for i in strong if text(i) == 'posts'), key=lambda i: bounds(i)[1])
    for title in titles:
        left, top, _, bottom = bounds(title)
        controls = [i for i in strong if text(i) in {'filters', 'manage posts'}
                    and bounds(i)[0] > left and abs(bounds(i)[1] - top) < height * .04]
        if not controls:
            continue
        views = [i for i in strong if text(i) in {'list view', 'grid view'}
                 and top < bounds(i)[1] < bottom + height * .12 and bounds(i)[0] >= left]
        if not views:
            continue
        header_top = max(bounds(i)[3] for i in views) + int(.012 * height)
        header_height = int(.12 * height)
        if header_top + header_height > height * .94:
            return None, True  # Still above the first post; one small scroll may reveal it.
        x = max(int(.28 * width), left - int(.015 * width))
        right = min(width, max(bounds(i)[2] for i in controls) + int(.035 * width))
        return (x, header_top, max(1, right - x), header_height), True
    return None, False


def evaluate_header(items, region, height, *, min_confidence=.55):
    x, y, width, h = region
    rows = [i for i in items if bounds(i) and i.get('confidence', 0) >= min_confidence
            and x + 30 * height / 1080 <= bounds(i)[0] < x + width
            and y <= bounds(i)[1] and bounds(i)[3] <= y + h
            and text(i) not in {'pinned', 'pinned post', 'featured'}]
    rows.sort(key=lambda i: (bounds(i)[1], bounds(i)[0]))
    if not rows:
        return {'accepted': False, 'reason': 'top_header_unreadable'}
    author = rows[0]
    if parse_header_timestamp(author.get('text', '')) is not None:
        return {'accepted': False, 'reason': 'top_author_unreadable'}
    line_height = max(10, bounds(author)[3] - bounds(author)[1])
    # Timestamp belongs immediately under the author, aligned with that header.
    candidates = [i for i in rows if .5 * line_height <= bounds(i)[1] - bounds(author)[1] <= 2.2 * line_height
                  and abs(bounds(i)[0] - bounds(author)[0]) <= max(35, .04 * width)]
    if candidates:
        first_row_y = min(bounds(i)[1] for i in candidates)
        # Facebook can put attribution and the timestamp in separate OCR boxes
        # on this same header row. Include the row's right-hand timestamp too.
        candidates = [i for i in rows if abs(bounds(i)[1] - first_row_y) <= .4 * line_height
                      and bounds(i)[0] >= bounds(author)[0] - 35]
    evaluated = []
    for item in candidates:
        stamp = parse_header_timestamp(item.get('text', ''))
        if stamp is None:
            continue
        score = .75 * stamp.score + .25 * item['confidence']
        evaluated.append({'age_seconds': stamp.age_seconds, 'unit': stamp.unit,
                          'score': round(score, 3), 'accepted': stamp.recent and score >= .85,
                          'reason': 'recent_top_post' if stamp.recent and score >= .85 else 'old_or_low_score_top_post'})
    if not evaluated:
        return {'accepted': False, 'reason': 'top_timestamp_unreadable'}
    # Conflicting reads are uncertainty, never permission to use the newer one.
    if len({(e['age_seconds'], e['unit']) for e in evaluated}) != 1:
        return {'accepted': False, 'reason': 'ambiguous_top_timestamp'}
    return max(evaluated, key=lambda e: e['score'])
