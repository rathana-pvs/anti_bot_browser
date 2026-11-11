"""What a warming visit sees on the account's own profile: the name and follower count.

Read once, right after the login check has loaded the account's own profile,
from one screenshot and one text read of the header. It never clicks or
navigates, and any doubt or error means no snapshot rather than a wrong one:
a visit must never fail or slow down because this could not be read.
"""

import re

# Where the name sits on a freshly loaded own profile, as (left, top, width, height) fractions of the screen.
HEADER_REGION = (.36, .14, .30, .18)
_COUNT_WORD = re.compile(r"\b(followers?|following|friends)\b", re.I)
_FOLLOWERS = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([km])?\s*followers?\b", re.I)
_NOT_A_NAME = {"facebook", "manage page", "posts"}
MAX_NAME = 80


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]+", " ", (text or "").casefold())).strip()


def header_region(width: int, height: int) -> tuple[int, int, int, int]:
    left, top, w, h = HEADER_REGION
    return int(width * left), int(height * top), int(width * w), int(height * h)


def profile_header(items, width: int, height: int):
    """The name and the count lines right under it, or None when this is not a profile header.

    Only the real header has a follower/friend count directly under the name;
    a scrolled page shows post text there instead.
    """
    titles = [item for item in items if item.get("bounds") and 2 <= len(item.get("text", "").strip()) <= 60]
    if not titles:
        return None
    title = max(titles, key=lambda item: item["bounds"][3] - item["bounds"][1])
    if _normalize(title["text"]) in _NOT_A_NAME:
        return None
    left, _, _, bottom = title["bounds"]
    counts = [item for item in items if item is not title and item.get("bounds")
              and 0 <= item["bounds"][1] - bottom <= height * .05 and abs(item["bounds"][0] - left) <= width * .02
              and _COUNT_WORD.search(item.get("text", ""))]
    return (title, counts) if counts else None


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
    line = " ".join(item.get("text", "") for item in sorted(counts, key=lambda item: item["bounds"][1]))
    snapshot = {"name": name, "kind": identity if identity in ("page", "personal") else None}
    followers = parse_followers(line)
    if followers is not None:
        snapshot["followers"] = followers
    return snapshot


def capture(task) -> dict | None:
    """Read the snapshot from the profile on screen now; None on any doubt or error."""
    try:
        from modules.reel_profile_readiness import browser_navigation_state

        screen = task.client.screenshot()
        if screen is None or browser_navigation_state(screen) != "idle":
            return None
        height, width = screen.shape[:2]
        items = task.vision.read_text(screen, region=header_region(width, height), min_confidence=.8)
        snapshot = read_snapshot(items or [], width, height, getattr(task, "facebook_identity", None))
    except Exception as exc:
        task.log("DEBUG", f"Could not read the page name and followers: {exc}")
        return None
    if snapshot is None:
        task.log("DEBUG", "The profile header was not readable; page name and followers not updated.")
        return None
    followers = snapshot.get("followers")
    task.log("INFO", f"Profile header: {snapshot['name']!r}"
                     + (f", {followers:,} followers" if followers is not None else ", followers not shown"))
    return snapshot
