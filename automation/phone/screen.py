"""Reading Facebook's phone screens: OCR lines, text matching, icon templates and post ages.

Pure functions over a frame and its OCR lines, so flows can be tested with scripted screens. The OCR is
English only; captions in other languages are matched only as "not empty".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "phone"
# Templates were cut from full-resolution (1080 px wide) screenshots.
TEMPLATE_WIDTH = 1080
ICON_THRESHOLD = 0.85
MIN_SCORE = 0.7

Point = tuple[float, float]


@dataclass(frozen=True)
class Line:
    text: str
    point: Point


def lines_from_ocr(results) -> list[Line]:
    """`readtext` results (box, text, score) as lines with centres, low-confidence ones dropped."""
    return [Line(text, (sum(p[0] for p in box) / 4, sum(p[1] for p in box) / 4))
            for box, text, score in results if score >= MIN_SCORE]


def squash(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def match(lines: list[Line], wanted: str, exact: bool = False, whole: bool = False) -> Point | None:
    """Exact for button labels; otherwise ignoring case, spaces and punctuation, which OCR sometimes
    merges or drops in longer text such as captions. `whole` is for a button whose icon OCR may read
    too ("→ Next"): the line must be the label alone, so a caption with the word in it is not taken."""
    key = squash(wanted)
    for line in lines:
        text = squash(line.text)
        if (line.text.strip() == wanted) if exact else (text == key if whole else (key and key in text)):
            return line.point
    return None


def same_screen(before: list[Line], after: list[Line], tolerance: float = 15.0) -> bool:
    """True when a swipe did not move the list: nearly every line still reads the same in the same
    place. Needs a few lines either side, so a blank or loading screen never counts as the end."""
    if len(before) < 3 or len(after) < 3:
        return False
    kept = sum(any(squash(a.text) == squash(b.text) and abs(a.point[1] - b.point[1]) <= tolerance for b in before)
               for a in after)
    return kept >= 0.8 * max(len(before), len(after))


_UPLOAD_BAR = ("posting", "uploading", "finishingup", "processing", "keepfacebookopen")


def uploading(lines: list[Line]) -> bool:
    """Facebook's upload bar, such as "Posting... Keep Facebook open." A longer line is a post in the feed."""
    return any(len(key) <= 40 and key.startswith(_UPLOAD_BAR) for key in (squash(line.text) for line in lines))


def button_disabled(image: np.ndarray | None, point: Point) -> bool:
    """A publish button Facebook has greyed out: light grey above its label. Blue is enabled; anything
    else, such as a label with no button shape, is not treated as disabled."""
    if image is None:
        return False
    scale = image.shape[1] / TEMPLATE_WIDTH
    x, y = int(point[0]), int(point[1] - 40 * scale)
    half = int(40 * scale)
    patch = image[max(0, y - 2):y + 3, max(0, x - half):x + half]
    if patch.size == 0:
        return False
    blue, green, red = np.median(patch.reshape(-1, 3), axis=0)
    return max(blue, green, red) - min(blue, green, red) < 30 and 180 < min(blue, green, red) < 245


# A screen's main action button: Facebook's blue filled shape in the bottom-right of the toolbar. It is found
# by colour and place, so a renamed label or a new icon still finds it; its label only says what a tap does.
BOTTOM_STRIP = 0.85  # the toolbar under a reel's video; a video's own text never reaches it
# OpenCV HSV (hue 0-180) for Facebook blue, about RGB (8, 101, 255); a video's blue cannot reach the strip.
_BLUE_LOW, _BLUE_HIGH = (95, 150, 150), (125, 255, 255)
_BUTTON_MIN = 60  # px across at 1080 wide; the round Next is about 125
_BUTTON_LABEL_BELOW = 80  # px at 1080 wide: how far under a round button its label can sit
FORWARD_LABELS = ("Next", "Continue", "Done")
PUBLISH_LABELS = ("Share", "Share now", "Post", "Post now", "Publish")  # a tap posts: never a step forward
EDITOR_TOOLS = ("Audio", "Edit", "Effects", "Text", "Stickers")


@dataclass(frozen=True)
class ActionButton:
    point: Point
    label: str  # what OCR read on or under it; "" for an icon alone
    kind: str  # "forward", "publish", or "unknown" for a label on neither list


def _distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _near(key: str, label: str) -> bool:
    """OCR misreads a letter or two ("Contlnue"): one off from 5 letters, two from 8. Shorter words must be
    exact, as one letter turns the toolbar's "Text" into "Next"."""
    want = squash(label)
    allowed = 2 if len(want) >= 8 else 1 if len(want) >= 5 else 0
    return key == want or (allowed > 0 and abs(len(key) - len(want)) <= allowed and _distance(key, want) <= allowed)


def label_kind(text: str) -> str:
    """"publish" first, so a misread publish label can never pass as a forward one; "" for no label."""
    key = squash(text)
    if not key:
        return ""
    if any(_near(key, label) for label in PUBLISH_LABELS):
        return "publish"
    if any(_near(key, label) for label in FORWARD_LABELS):
        return "forward"
    return "unknown"


def _blue_box(image: np.ndarray | None) -> tuple[float, float, float, float] | None:
    """The box (left, top, right, bottom) of the largest round or pill-shaped Facebook-blue shape in the
    bottom strip that reaches the right half. The whole width is searched, so a wide bar (Share now) is
    seen whole and its label, in its middle, is read."""
    if image is None:
        return None
    height, width = image.shape[:2]
    top = int(height * BOTTOM_STRIP)
    hsv = cv2.cvtColor(image[top:], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _BLUE_LOW, _BLUE_HIGH)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    least = _BUTTON_MIN * width / TEMPLATE_WIDTH
    best = None
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < least or h < least or x + w <= width / 2 or cv2.contourArea(contour) < 0.6 * w * h:
            continue  # a circle fills 0.79 of its box, a rounded bar more
        if best is None or w * h > best[2] * best[3]:
            best = (x, y, w, h)
    if best is None:
        return None
    x, y, w, h = best
    return float(x), float(top + y), float(x + w), float(top + y + h)


def blue_button(image: np.ndarray | None) -> Point | None:
    """The centre of the screen's blue action button (see `_blue_box`)."""
    box = _blue_box(image)
    return None if box is None else ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _label_in(lines: list[Line], box: tuple[float, float, float, float], width: int) -> str:
    """Text inside the button or just under it (a round button's label sits below). One- or two-character
    reads are dropped: OCR reads icons, such as the arrow, as stray letters ("e", "0")."""
    scale = width / TEMPLATE_WIDTH
    pad = 20 * scale
    left, top, right, bottom = box
    near = [line for line in lines if len(squash(line.text)) > 2
            and left - pad <= line.point[0] <= right + pad and top - pad <= line.point[1] <= bottom + _BUTTON_LABEL_BELOW * scale]
    return " ".join(line.text for line in sorted(near, key=lambda line: line.point[1])).strip()


def action_button(lines: list[Line], image: np.ndarray | None) -> ActionButton | None:
    """The screen's blue action button and what its label means. With no blue shape seen (or no frame),
    a known label alone on its line in the bottom strip; an unknown word there is not taken for a button."""
    box = _blue_box(image)
    if box:
        label = _label_in(lines, box, image.shape[1])
        return ActionButton(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, label_kind(label))
    top = image.shape[0] * BOTTOM_STRIP if image is not None else None
    for line in sorted(lines, key=lambda line: -line.point[1]):
        if top is not None and line.point[1] < top:
            break
        kind = label_kind(line.text)
        if kind in ("forward", "publish"):
            return ActionButton(line.point, line.text.strip(), kind)
    return None


def reel_editor_shown(lines: list[Line]) -> bool:
    """The reel editor's own toolbar, read whole, so a video's text cannot fake it."""
    return sum(match(lines, tool, whole=True) is not None for tool in EDITOR_TOOLS) >= 2


def menu_reel(lines: list[Line]) -> Point | None:
    """The + menu's Reel row. OCR can read its icon as a letter, so "B Reel" counts; a longer text
    behind the menu does not."""
    for line in lines:
        key = squash(line.text)
        if key.endswith("reel") and len(key) <= 6:
            return line.point
    return None


def texts(lines: list[Line], limit: int = 12) -> list[str]:
    return [line.text for line in lines][:limit]


def caption_key(caption: str) -> str:
    """The part of a caption used to find it on screen: its first English words, about 20 characters.
    Empty when the caption has nothing OCR can read back (other scripts, emoji only)."""
    readable = re.sub(r"[^\x20-\x7e]", " ", caption)
    words = " ".join(readable.split())
    return words[:20] if len(squash(words[:20])) >= 6 else ""


def load_templates(names=("comment", "send", "home_tab", "create", "create_round", "composer_photo")) -> dict[str, np.ndarray]:
    templates = {name: cv2.imread(str(TEMPLATE_DIR / f"{name}.png")) for name in names}
    missing = [name for name, image in templates.items() if image is None]
    if missing:
        raise FileNotFoundError(f"Phone templates missing in {TEMPLATE_DIR}: {missing}")
    return templates


# How far from its 1080-wide size an icon is searched for, when not the default ±6 %. Facebook draws its +
# about 6 % smaller than the template on many phones, right at the default's edge, so a bit smaller still
# was missed.
ICON_SPREAD = {"create": 0.12, "create_round": 0.12}


def find_template(image: np.ndarray, template: np.ndarray, below: float | None = None,
                  spread: float = 0.06) -> Point | None:
    """Centre of the best match at the frame's scale (searched ±`spread`), or None under the threshold."""
    scale = image.shape[1] / TEMPLATE_WIDTH
    offset = 0 if below is None else max(0, int(below))
    area = image[offset:]
    best_score, best = 0.0, None
    for factor in np.linspace(scale * (1 - spread), scale * (1 + spread), int(round(spread / 0.02)) * 2 + 1):
        small = cv2.resize(template, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        if area.shape[0] < small.shape[0] or area.shape[1] < small.shape[1]:
            continue
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(area, small, cv2.TM_CCOEFF_NORMED))
        if score > best_score:
            best_score = score
            best = (loc[0] + small.shape[1] / 2, loc[1] + offset + small.shape[0] / 2)
    return best if best_score >= ICON_THRESHOLD else None


_TIME = re.compile(r"^(just now|(\d+)\s*(m|min|mins|h|hr|hrs|d)\b|yesterday)", re.I)


def post_age_minutes(text: str) -> int | None:
    """Age of a post from its time label: "Just now", "7m", "2h"; None when it isn't one."""
    found = _TIME.match(text.strip().rstrip("·• ").strip())
    if not found:
        return None
    label = found.group(1).casefold()
    if label == "just now":
        return 0
    if label == "yesterday":
        return 24 * 60
    number, unit = int(found.group(2)), found.group(3).casefold()
    return number if unit.startswith("m") else number * 60 if unit.startswith("h") else number * 24 * 60


def logged_out(lines: list[Line]) -> bool:
    return bool(match(lines, "Log in", exact=True) and match(lines, "Create new account"))


def checkpoint(lines: list[Line]) -> bool:
    """Facebook stopped the account: a checkpoint, a confirm-identity or a suspension screen."""
    return any(match(lines, text) for text in (
        "Confirm your identity", "We suspended your account", "Your account has been locked",
        "We need to confirm", "account is restricted"))


def interruption(lines: list[Line]) -> tuple[str, str, Point | None] | None:
    """A known pop-up and how to close it: (kind, "tap" or "back", point). Permission prompts are
    answered the privacy-preserving way."""
    for wanted in ("Don't allow", "Don’t allow"):
        point = match(lines, wanted)
        if point and (match(lines, "Allow Facebook") or match(lines, "notifications")):
            return "permission prompt", "tap", point
    if match(lines, "reels streak") or match(lines, "View my reels achievements"):
        return "reels streak", "back", None
    # Android's own contact-sharing sheet, raised when two phones come close (phones side by side on a hub).
    # It covers the bottom of the screen, where the app drawer is swiped open.
    if match(lines, "Receive only", exact=True) or match(lines, "Share your contact info"):
        return "contact sharing sheet", "back", None
    point = match(lines, "Not now", exact=True)
    if point:
        return "not now", "tap", point
    return None


# Text that does something other than open its post when tapped.
_NOT_PLAIN_TEXT = re.compile(r"https?://|www\.|[#@]|\b\w+\.(com|net|org|co|io|me)\b", re.IGNORECASE)
READABLE_MIN_CHARS = 30


def readable_lines(lines: list[Line], height: float) -> list[Line]:
    """Lines of a post's own text a person could tap to read it. Only long plain text in the middle of
    the screen, so a tap cannot land on an icon-only Like or Share row; nothing on a screen with an ad."""
    if match(lines, "Sponsored"):
        return []
    return [line for line in lines
            if len(line.text.strip()) >= READABLE_MIN_CHARS and height * 0.25 <= line.point[1] <= height * 0.75
            and not _NOT_PLAIN_TEXT.search(line.text)]


LEFT_UNFINISHED = "Continue editing your last"


def discard_button(lines: list[Line]) -> Point | None:
    """The discard choice of the prompts a half-made post shows: "Discard changes?" or "Continue editing
    your last reel?" or "...post?" (DISCARD), the "finish your post later?" sheet (Discard post) or "Save this
    reel as a draft?" (Discard video)."""
    if match(lines, "Discard changes") or match(lines, LEFT_UNFINISHED):
        point = match(lines, "DISCARD", exact=True) or match(lines, "Discard", exact=True)
        if point:
            return point
    return (match(lines, "Discard post", exact=True) or match(lines, "Discard video", exact=True)
            or match(lines, "Discard draft", exact=True))


# --- the account's own Page header ---------------------------------------------------------------

# A count line has a number: "1.2K followers · 16 following", "500 friends". A tab named Followers has none.
_COUNT_LINE = re.compile(r"\d[\d.,]*\s*[km]?\s+(followers?|following|friends)\b", re.IGNORECASE)
_FRIENDS_LINE = re.compile(r"\d[\d.,]*\s*[km]?\s+friends\b", re.IGNORECASE)
# Lines that can sit right above the count line but are never the Page's name.
_NOT_A_NAME = {"facebook", "posts", "all", "about", "photos", "reels", "createreel", "editprofile",
               "managepage", "professionaldashboard", "seeyouraboutinfo"}
NAME_ABOVE_MAX = 170  # 1080-wide px: the name's centre sits this close above its count line's centre
SAME_ROW = 22  # 1080-wide px: OCR pieces of one line ("The" + "Express Desk") are this close in height
MAX_PAGE_NAME = 80


def page_header(lines: list[Line], width: float) -> dict | None:
    """The Page's name and follower count from its profile header, or None when no header is on screen
    or it is a personal profile (a friends count). The name is the line right above the count line."""
    scale = width / TEMPLATE_WIDTH
    from skills.account_snapshot import parse_followers
    for count in sorted((line for line in lines if _COUNT_LINE.search(line.text)), key=lambda line: line.point[1]):
        if _FRIENDS_LINE.search(count.text):
            return None  # a personal profile: only Pages are tracked
        followers = parse_followers(count.text)
        above = [line for line in lines if line is not count
                 and 0 < count.point[1] - line.point[1] <= NAME_ABOVE_MAX * scale
                 and 2 <= len(line.text.strip()) <= 60 and re.search(r"[^\W\d_]", line.text)
                 and not _COUNT_LINE.search(line.text) and squash(line.text) not in _NOT_A_NAME]
        snapshot: dict = {"kind": "page"}
        if above:
            title = max(above, key=lambda line: line.point[1])
            row = sorted((line for line in above if abs(line.point[1] - title.point[1]) <= SAME_ROW * scale),
                         key=lambda line: line.point[0])
            snapshot["name"] = re.sub(r"\s+", " ", " ".join(line.text.strip() for line in row)).strip()[:MAX_PAGE_NAME]
        if followers is not None:
            snapshot["followers"] = followers
        if "name" in snapshot or "followers" in snapshot:
            return snapshot
    return None
