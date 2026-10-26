"""Locate only the first post header below the profile's Posts/List view toolbar."""
import re

from .post_timestamp import parse_header_timestamp


def profile_feed_is_loading(screen, region=None):
    """Recognize Facebook's avatar-and-two-bars skeleton, not an OCR miss.

    Restrict detection to the first header when anchored, otherwise the right
    profile feed column. Require two aligned neutral bars beside an avatar
    placeholder so a blank card or unreadable text alone is not loading proof.
    """
    import cv2
    import numpy as np

    height, width = screen.shape[:2]
    scale = height / 1080
    if region is None:
        region = (int(width * .54), int(height * .2), int(width * .38), int(height * .74))
    x, y, w, h = region
    crop = screen[y:y+h, x:x+w]
    if not crop.size:
        return False
    colors = crop[:, :, :3].astype(np.int16)
    low, high = colors.min(axis=2), colors.max(axis=2)
    neutral = (high - low <= 4) & (((low >= 180) & (high <= 248)) |
                                         ((low >= 45) & (high <= 100)))
    contours, _ = cv2.findContours(neutral.astype(np.uint8) * 255,
                                  cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bars, avatars = [], []
    for contour in contours:
        bx, by, bw, bh = cv2.boundingRect(contour)
        if 30 * scale <= bw <= 240 * scale and 4 * scale <= bh <= 18 * scale and bw / bh >= 2.5:
            bars.append((bx, by, bw, bh))
        if (24 * scale <= bw <= 52 * scale and 24 * scale <= bh <= 52 * scale
                and .75 <= bw / bh <= 1.3 and cv2.contourArea(contour) >= .55 * bw * bh):
            avatars.append((bx, by, bw, bh))
    for bx, by, bw, bh in bars:
        paired = any(abs(px - bx) <= 4 * scale and 2 * scale <= py - (by + bh) <= 16 * scale
                     for px, py, _, _ in bars)
        if paired and any(0 <= bx - (ax + aw) <= 28 * scale and ay <= by <= ay + ah
                          for ax, ay, aw, ah in avatars):
            return True
    return False


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
    first = rows[0]
    line_height = max(10, bounds(first)[3] - bounds(first)[1])
    first_line = [i for i in rows if abs(bounds(i)[1] - bounds(first)[1]) <= .4 * line_height]
    if any(parse_header_timestamp(i.get('text', '')) is not None for i in first_line):
        # The author can be absent (or omitted by OCR). Accept a timestamp as
        # the first row only near the top of the toolbar-anchored header, never
        # by searching down into the caption when the header is unreadable.
        if bounds(first)[1] - y > .065 * height:
            return {'accepted': False, 'reason': 'top_timestamp_unreadable'}
        first_row_y = bounds(first)[1]
    else:
        author = first
        # Timestamp belongs immediately under the author, aligned with that header.
        candidates = [i for i in rows if .5 * line_height <= bounds(i)[1] - bounds(author)[1] <= 2.2 * line_height
                      and abs(bounds(i)[0] - bounds(author)[0]) <= max(35, .04 * width)]
        if not candidates:
            return {'accepted': False, 'reason': 'top_timestamp_unreadable'}
        first_row_y = min(bounds(i)[1] for i in candidates)
    # Include separate attribution/timestamp boxes on this row, including
    # conflicting reads. Author text is optional; row position remains bounded.
    candidates = [i for i in rows if abs(bounds(i)[1] - first_row_y) <= .4 * line_height
                  and bounds(i)[0] >= bounds(first)[0] - 35]
    evaluated = []
    for item in candidates:
        stamp = parse_header_timestamp(item.get('text', ''))
        if stamp is None:
            continue
        score = .75 * stamp.score + .25 * item['confidence']
        evaluated.append({'age_seconds': stamp.age_seconds, 'unit': stamp.unit,
                          'score': round(score, 3), 'accepted': stamp.recent and score >= .85,
                          'reason': ('old_top_post' if not stamp.recent else
                                     'recent_top_post' if score >= .85 else 'low_score_top_post')})
    if not evaluated:
        return {'accepted': False, 'reason': 'top_timestamp_unreadable'}
    # Conflicting reads are uncertainty, never permission to use the newer one.
    if len({(e['age_seconds'], e['unit']) for e in evaluated}) != 1:
        return {'accepted': False, 'reason': 'ambiguous_top_timestamp'}
    return max(evaluated, key=lambda e: e['score'])


def retry_unreadable_header(vision, screen, region, header, *, min_confidence=.55):
    """Re-read the same header enlarged once, preserving original coordinates.

    Keep original reads so a retry cannot replace an old/conflicting timestamp
    with a newer one. Successful and recognized-old checks need no retry.
    """
    height = screen.shape[0]
    result = evaluate_header(header, region, height, min_confidence=min_confidence)
    if result['reason'] not in {'top_header_unreadable', 'top_timestamp_unreadable'}:
        return header, result
    import cv2
    x, y, width, h = region
    # When the author is readable, enlarge only the row immediately below it.
    # Keep the original header coordinates/reads for layout and conflict checks.
    # Do not slide this crop down into captions when the timestamp is missing.
    rows = sorted((i for i in header if bounds(i) and i.get('confidence', 0) >= min_confidence
                   and x + 30 * height / 1080 <= bounds(i)[0] < x + width
                   and y <= bounds(i)[1] and bounds(i)[3] <= y + h),
                  key=lambda i: (bounds(i)[1], bounds(i)[0]))
    if rows:
        author = rows[0]
        line_height = max(10, bounds(author)[3] - bounds(author)[1])
        is_author = (len(text(author).split()) >= 2
                     and parse_header_timestamp(author.get('text', '')) is None
                     and not re.match(r'^(?:published|posted)\s+by\b', text(author)))
        below = [i for i in rows if .5 * line_height <= bounds(i)[1] - bounds(author)[1] <= 2.2 * line_height
                 and abs(bounds(i)[0] - bounds(author)[0]) <= max(35, .04 * width)]
        if is_author and below:
            row_y = min(bounds(i)[1] for i in below)
            same_row = [i for i in rows if abs(bounds(i)[1] - row_y) <= .4 * line_height]
            top = max(y, int(bounds(author)[3]) + 1, int(row_y - .25 * line_height))
            bottom = min(y + h, int(max(bounds(i)[3] for i in same_row) + .25 * line_height))
            if bottom > top:
                y, h = top, bottom - top
    crop = screen[y:y+h, x:x+width]
    if not crop.size:
        return header, result
    enlarged = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    reads = vision.read_text(enlarged, region=(0, 0, enlarged.shape[1], enlarged.shape[0]),
                             min_confidence=min_confidence)
    combined = list(header)
    for item in reads:
        if not bounds(item):
            continue
        left, top, right, bottom = bounds(item)
        mapped = dict(item, bounds=(x + left/2, y + top/2, x + right/2, y + bottom/2),
                      center=(x + (left+right)/4, y + (top+bottom)/4))
        combined.append(mapped)
    result = evaluate_header(combined, region, height, min_confidence=min_confidence)
    if result['reason'] not in {'top_header_unreadable', 'top_timestamp_unreadable'}:
        return combined, result
    # A glyph such as "Im" is only a location hint, never a numeric rewrite.
    # Secondary recognition sees a tight candidate crop without the privacy icon.
    fallback = getattr(vision, 'read_text_with_fallback', None)
    if not callable(fallback):
        return combined, result
    candidates = [i for i in combined if bounds(i)
                  and re.fullmatch(r'[a-zA-Z0-9]{1,8}', i.get('text', '').strip())
                  and x + 30 * height / 1080 <= bounds(i)[0]
                  and y <= bounds(i)[1] <= y + .065 * height
                  and bounds(i)[2] - bounds(i)[0] <= .12 * width]
    seen = set()
    for item in candidates:
        left, top, right, bottom = bounds(item)
        pad_x = max(2, round(2 * height / 1080))
        pad_y = max(3, round((bottom-top) * .4))
        left, top = max(x, int(left)-pad_x), max(y, int(top)-pad_y)
        right, bottom = min(x+width, int(right)+pad_x), min(y+h, int(bottom)+pad_y)
        tight = (left, top, right-left, bottom-top)
        if tight in seen:
            continue
        seen.add(tight)
        recovered = fallback(screen, region=tight, primary=[], min_confidence=max(.60, min_confidence),
                             accept=lambda read: parse_header_timestamp(read.get('text', '')) is not None)
        if isinstance(recovered, list):
            combined.extend(recovered)
        if len(seen) >= 2:
            break
    return combined, evaluate_header(combined, region, height, min_confidence=min_confidence)
