"""What a warming visit sees on the account's own profile: the name and follower count.

Read right after the login check has loaded the account's own profile, from a
screenshot and one text read of the header. When the header is not on screen it
scrolls to the top of the page once and reads again; it never clicks or
navigates. Any doubt or error means no snapshot rather than a wrong one: a visit
must never fail because this could not be read.
"""

import re
import time

# The profile header can sit anywhere from just under the browser bar (page scrolled) to low on the
# screen (cover photo showing), so the whole column right of the sidebar is read:
# (left, top, width, height) as fractions of the screen.
HEADER_REGION = (.36, .14, .33, .80)
# A count line has a number: "0 followers · 0 following", "500 friends". The Followers tab has none.
_COUNT_LINE = re.compile(r"\d[\d.,]*\s*[km]?\s+(followers?|following|friends)\b", re.I)
_FOLLOWERS = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([km])?\s*followers?\b", re.I)
_NOT_A_NAME = {"facebook", "manage page", "posts"}
MAX_NAME = 80


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]+", " ", (text or "").casefold())).strip()


def header_region(width: int, height: int) -> tuple[int, int, int, int]:
    left, top, w, h = HEADER_REGION
    return int(width * left), int(height * top), int(width * w), int(height * h)


def profile_header(items, width: int, height: int):
    """The name and the count line right under it, or None when no profile header is on screen.

    Found from the count line up: the header's name sits directly above its follower/friend
    count, left-aligned with it. Big text in the cover photo has no count under it, and a post
    by the account has no count under its name.
    """
    placed = [item for item in items if item.get("bounds") and item.get("text", "").strip()]
    for count in sorted((item for item in placed if _COUNT_LINE.search(item["text"])), key=lambda item: item["bounds"][1]):
        left, top = count["bounds"][0], count["bounds"][1]
        above = [item for item in placed if item is not count
                 # OCR boxes of a large name and the line under it can overlap by a few pixels.
                 and -height * .01 <= top - item["bounds"][3] <= height * .05
                 and item["bounds"][1] < top
                 and abs(item["bounds"][0] - left) <= width * .02
                 and 2 <= len(item["text"].strip()) <= 60
                 and _normalize(item["text"]) not in _NOT_A_NAME]
        if above:
            title = max(above, key=lambda item: item["bounds"][3])  # the line right above the count
            return _whole_line(title, placed, width), [count]
    return None


def _whole_line(title, items, width):
    """The name with the pieces OCR split it into on the same line ("The" + "Express Desk")."""
    top, bottom = title["bounds"][1], title["bounds"][3]
    line = [title]
    for item in sorted(items, key=lambda item: item["bounds"][0]):
        if item in line or not (top <= (item["bounds"][1] + item["bounds"][3]) / 2 <= bottom) \
                or abs(item["bounds"][3] - item["bounds"][1] - (bottom - top)) > (bottom - top) * .5:
            continue
        last = max(line, key=lambda piece: piece["bounds"][2])
        first = min(line, key=lambda piece: piece["bounds"][0])
        # Joined only when it touches the line's ends: a word gap, not another column.
        # OCR boxes of neighbouring words may overlap a little.
        gap = width * .02
        if -gap <= item["bounds"][0] - last["bounds"][2] <= gap or -gap <= first["bounds"][0] - item["bounds"][2] <= gap:
            line.append(item)
    if len(line) == 1:
        return title
    line.sort(key=lambda piece: piece["bounds"][0])
    bounds = (line[0]["bounds"][0], min(p["bounds"][1] for p in line), line[-1]["bounds"][2], max(p["bounds"][3] for p in line))
    return {**title, "text": " ".join(piece["text"].strip() for piece in line), "bounds": bounds}


def parse_followers(text: str) -> int | None:
    """'1.2K followers' -> 1200, '3,456 followers' -> 3456; None when no follower count is shown."""
    match = _FOLLOWERS.search(text or "")
    if not match:
        return None
    number, suffix = match.group(1).replace(",", ""), (match.group(2) or "").lower()
    try:
        value = float(number)
    except ValueError:
        return None
    value *= {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
    return int(round(value)) if 0 <= value < 10_000_000_000 else None


def read_snapshot(items, width: int, height: int, identity: str | None) -> dict | None:
    """The snapshot for these header text items, or None when they are not a profile header."""
    header = profile_header(items, width, height)
    if header is None:
        return None
    title, counts = header
    name = re.sub(r"\s+", " ", title["text"]).strip()[:MAX_NAME]
    line = " ".join(item.get("text", "") for item in counts)
    snapshot = {"kind": identity if identity in ("page", "personal") else None}
    # A name with no letters is a script the browser shows as boxes (read as "000"): only the count is kept.
    if re.search(r"[^\W\d_]", name):
        snapshot["name"] = name
    followers = parse_followers(line)
    if followers is not None:
        snapshot["followers"] = followers
    return snapshot if "name" in snapshot or "followers" in snapshot else None


# Enough wheel notches to reach the top from wherever a freshly loaded profile sits.
TOP_SCROLL_NOTCHES = 12
SETTLE_SECONDS = 1.2


def _read_screen(task) -> tuple[dict | None, bool]:
    """(snapshot, a screen was read) for what is on screen now.

    Chrome's loading cross is waited out for a few seconds, then ignored: Facebook can leave a request
    hanging long after the header has drawn. A header still drawing is safe to read, since the name
    must sit right above its count line or nothing is kept."""
    from modules.reel_profile_readiness import NAVIGATION_HINT_S, browser_navigation_state

    screen = task.client.screenshot()
    for _ in range(int(NAVIGATION_HINT_S)):  # one look a second
        if screen is None or browser_navigation_state(screen) == "idle":
            break
        time.sleep(1.)
        screen = task.client.screenshot()
    if screen is None:
        return None, False
    height, width = screen.shape[:2]
    items = task.vision.read_text(screen, region=header_region(width, height), min_confidence=.8)
    return read_snapshot(items or [], width, height, getattr(task, "facebook_identity", None)), True


def _scroll_to_top(task) -> None:
    """One scroll up over the main column (the wheel over the sidebar would scroll the sidebar)."""
    width, height = task.client.get_screen_dimensions()
    task.human.move_to(int(width * .55), int(height * .55))
    task.human.scroll("up", notches=TOP_SCROLL_NOTCHES)
    time.sleep(SETTLE_SECONDS)


def capture(task) -> dict | None:
    """Read the snapshot from the Page on screen; None for a personal profile, or on any doubt or error.

    Read as the page loaded first. Only when no header is found does it scroll to the top
    of the page once and read again: Facebook sometimes opens a profile scrolled past it.
    """
    if getattr(task, "facebook_identity", None) != "page":
        return None  # only Pages are tracked; a personal profile is left alone
    try:
        snapshot, settled = _read_screen(task)
        if snapshot is None and settled:
            task.log("DEBUG", "Profile header not on screen; scrolling to the top to read it.")
            _scroll_to_top(task)
            snapshot, _ = _read_screen(task)
    except Exception as exc:
        task.log("DEBUG", f"Could not read the page name and followers: {exc}")
        return None
    if snapshot is None:
        task.log("DEBUG", "The profile header was not readable; page name and followers not updated.")
        return None
    followers = snapshot.get("followers")
    task.log("INFO", f"Profile header: {snapshot.get('name', 'name not readable')!r}"
                     + (f", {followers:,} followers" if followers is not None else ", followers not shown"))
    return snapshot
