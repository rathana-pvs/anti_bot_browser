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


def match(lines: list[Line], wanted: str, exact: bool = False) -> Point | None:
    """Exact for button labels; otherwise ignoring case, spaces and punctuation, which OCR sometimes
    merges or drops in longer text such as captions."""
    key = squash(wanted)
    for line in lines:
        if (line.text.strip() == wanted) if exact else (key and key in squash(line.text)):
            return line.point
    return None


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


def menu_reel(lines: list[Line]) -> Point | None:
    """The + menu's Reel row. OCR can read its icon as a letter, so "B Reel" counts; a longer text
    behind the menu does not."""
    for line in lines:
        key = squash(line.text)
        if key.endswith("reel") and len(key) <= 6:
            return line.point
    return None


def shown_across_lines(lines: list[Line], wanted: str) -> bool:
    """`wanted` anywhere in the screen's text read top to bottom, for text that wraps onto several lines."""
    key = squash(wanted)
    return bool(key) and key in squash("".join(line.text for line in sorted(lines, key=lambda line: line.point[1])))


def texts(lines: list[Line], limit: int = 12) -> list[str]:
    return [line.text for line in lines][:limit]


def caption_key(caption: str) -> str:
    """The part of a caption used to find it on screen: its first English words, about 20 characters.
    Empty when the caption has nothing OCR can read back (other scripts, emoji only)."""
    readable = re.sub(r"[^\x20-\x7e]", " ", caption)
    words = " ".join(readable.split())
    return words[:20] if len(squash(words[:20])) >= 6 else ""


def caption_tail_key(caption: str) -> str:
    """The end of a caption's last line: a long caption's box scrolls there, so its start is out of sight.
    Empty when that line has nothing OCR can read back."""
    last = (caption.strip().splitlines() or [""])[-1]
    words = " ".join(re.sub(r"[^\x20-\x7e]", " ", last).split())
    return words[-20:] if len(squash(words[-20:])) >= 6 else ""


def load_templates(names=("comment", "send", "home_tab", "create")) -> dict[str, np.ndarray]:
    templates = {name: cv2.imread(str(TEMPLATE_DIR / f"{name}.png")) for name in names}
    missing = [name for name, image in templates.items() if image is None]
    if missing:
        raise FileNotFoundError(f"Phone templates missing in {TEMPLATE_DIR}: {missing}")
    return templates


def find_template(image: np.ndarray, template: np.ndarray, below: float | None = None) -> Point | None:
    """Centre of the best match at the frame's scale (searched ±6 %), or None under the threshold."""
    scale = image.shape[1] / TEMPLATE_WIDTH
    offset = 0 if below is None else max(0, int(below))
    area = image[offset:]
    best_score, best = 0.0, None
    for factor in np.linspace(scale * 0.94, scale * 1.06, 7):
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
