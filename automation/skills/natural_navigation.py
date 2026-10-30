"""Natural-session navigation: move around Facebook the way a person does.

Only used when the account's natural session switch is on. Going to the own
profile clicks the account's name (sidebar or avatar menu) or refreshes when
already there, and from outside Facebook it types the home address first and
then clicks the name; going home clicks the Facebook logo. Every click is checked by
the address that loads, and any doubt falls back to typing the URL.
"""

import json
import re
import time
import urllib.parse
from difflib import SequenceMatcher

from engine.runtime_paths import profiles_dir
from engine.screen_state import ScreenState
from modules.reel_profile_readiness import browser_navigation_state
from skills.session import is_facebook_url

PROFILE_URL = "https://www.facebook.com/me"
HOME_URL = "https://www.facebook.com/"
IDENTITY_FILE = "natural_session.json"
CLICK_HABITS = ("sidebar", "avatar", "typed")
_HOST_PATH = re.compile(r"^(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/\S*)?$", re.I)
# A page in one of these states is a settled Facebook screen; a composer or
# dialog is never navigated away from by clicking.
_SETTLED_STATES = {ScreenState.FEED_READY, ScreenState.POST_CONFIRMED}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]+", " ", (text or "").casefold())).strip()


def page_key(url: str | None) -> str | None:
    """Comparable identity of a Facebook page: host-free path plus a profile id."""
    if not url:
        return None
    parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    path = parsed.path.rstrip("/").casefold()
    if path == "/profile.php":
        ids = urllib.parse.parse_qs(parsed.query).get("id")
        return f"/profile.php?id={ids[0]}" if ids else None
    return path or "/"


def load_identity(task) -> dict | None:
    try:
        value = json.loads((profiles_dir() / task.profile_id / IDENTITY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if isinstance(value, dict) and value.get("profile_name") and page_key(value.get("profile_url")):
        return value
    return None


def save_identity(task, name: str, url: str) -> None:
    path = profiles_dir() / task.profile_id / IDENTITY_FILE
    path.write_text(json.dumps({"profile_name": name, "profile_url": url}), encoding="utf-8")


class NaturalNavigation:
    def __init__(self, task):
        self.task = task

    @property
    def session(self):
        return getattr(self.task, "behavior_session", None)

    def enabled(self) -> bool:
        return bool(self.session is not None and getattr(self.session, "natural", False))

    # Reading the address --------------------------------------------------

    def read_address_bar(self, screen) -> str | None:
        """Read the URL from Chrome's address bar in a screenshot.

        Browser chrome cannot be drawn by the page, so this is trusted like the
        clipboard read, without focusing the address bar.
        """
        if screen is None:
            return None
        height, width = screen.shape[:2]
        items = self.task.vision.read_text(screen, region=(150, 48, max(1, width - 430), 30), min_confidence=0.6)
        items = sorted((item for item in items if item.get("text")), key=lambda item: item.get("bounds", (0,))[0])
        text = "".join(item["text"].strip() for item in items).replace(" ", "")
        match = _HOST_PATH.match(text)
        if not match:
            return None
        return text if text.startswith("http") else "https://" + text

    def current_url(self, screen=None) -> str | None:
        task = self.task
        try:
            url = self.read_address_bar(screen if screen is not None else task.client.screenshot())
        except Exception:
            url = None
        if url:
            self._count("address_bar_ocr")
            return url
        self._count("address_bar_copy")
        return task.client.get_current_url()

    # Navigation -----------------------------------------------------------

    def go(self, url: str) -> str | None:
        """Navigate without typing when natural; returns the method, or None to type."""
        if url not in (PROFILE_URL, HOME_URL):
            return None
        task = self.task
        screen = task.client.screenshot()
        current = self.current_url(screen)
        if not is_facebook_url(current):
            return self._cold_start(url)
        if task.recognizer.observe(screen).state not in _SETTLED_STATES:
            return None
        if url == HOME_URL:
            return "logo" if self._click_logo(screen) else None
        identity = load_identity(task)
        if identity is None:
            return None
        if page_key(current) == page_key(identity["profile_url"]):
            self.refresh()
            return "refresh"
        habit = self._habit()
        if habit == "typed":
            return None
        return self._click_name(screen, identity, habit)

    def _cold_start(self, url: str) -> str | None:
        """From outside Facebook, type the home address and click the own name.

        A person rarely types their profile address; they open Facebook and
        click their name. Without a learned name this declines, and the
        caller types the profile address so the name can be learned.
        """
        if url != PROFILE_URL:
            return None
        identity = load_identity(self.task)
        if identity is None:
            return None
        habit = self._habit()
        if habit == "typed":
            return None
        self.task.human.navigate(HOME_URL)
        screen = self._settled_home()
        if screen is None:
            return None
        method = self._click_name(screen, identity, habit)
        return f"home_{method}" if method else None

    def _click_name(self, screen, identity, habit: str) -> str | None:
        for method in (habit, "avatar" if habit == "sidebar" else "sidebar"):
            clicked = self._click_sidebar_name(screen, identity) if method == "sidebar" else self._click_avatar_name(screen, identity)
            if clicked and self._landed(identity["profile_url"]):
                return method
            screen = self.task.client.screenshot()
        return None

    def refresh(self) -> None:
        """Press F5 as a real key press on the focused browser window."""
        self.task.client.ensure_focus()
        self.task.human.key_press("F5")

    def remember_profile(self) -> None:
        """After a typed visit to the own profile, learn its name and address."""
        task = self.task
        if load_identity(task) is not None:
            return
        screen = task.client.screenshot()
        if browser_navigation_state(screen) != "idle":
            return
        url = self.current_url(screen)
        key = page_key(url)
        if not is_facebook_url(url) or key in (None, "/", "/me", "/home.php"):
            return
        height, width = screen.shape[:2]
        items = task.vision.read_text(
            screen, region=(int(width * .36), int(height * .14), int(width * .30), int(height * .18)), min_confidence=.8,
        )
        titles = [item for item in items if item.get("bounds") and 2 <= len(item.get("text", "").strip()) <= 60]
        if not titles:
            return
        title = max(titles, key=lambda item: item["bounds"][3] - item["bounds"][1])
        name = title["text"].strip()
        if _normalize(name) in {"facebook", "manage page", "posts"}:
            return
        # Only the real profile header has its follower/friend count right
        # under the name; a scrolled page shows post text here instead.
        left, _, _, bottom = title["bounds"]
        counts = [item for item in items if item is not title and item.get("bounds")
                  and 0 <= item["bounds"][1] - bottom <= height * .05 and abs(item["bounds"][0] - left) <= width * .02
                  and re.search(r"\b(followers?|following|friends)\b", item.get("text", ""), re.I)]
        if not counts:
            return
        save_identity(task, name, url)
        task.log("INFO", f"Natural session: remembered this account's profile name {name!r}.")

    # Helpers --------------------------------------------------------------

    def _habit(self) -> str:
        weights = self.session.persona.navigation_weights
        roll = self.session.stream("session").random() * sum(weights)
        for habit, weight in zip(CLICK_HABITS, weights):
            roll -= weight
            if roll < 0:
                return habit
        return CLICK_HABITS[-1]

    def _find_name(self, screen, region, name):
        target = _normalize(name)
        best = None
        for item in self.task.vision.read_text(screen, region=region, min_confidence=0.7):
            score = SequenceMatcher(None, _normalize(item.get("text", "")), target).ratio()
            if score >= 0.9 and (best is None or score > best[0]):
                best = (score, item)
        return best[1] if best else None

    def _click_sidebar_name(self, screen, identity) -> bool:
        height, width = screen.shape[:2]
        item = self._find_name(screen, (0, int(height * .08), int(width * .22), int(height * .45)), identity["profile_name"])
        if item is None:
            return False
        self.task.click_reversible(item["center"], label="profile_name", bounds=item.get("bounds"), max_offset_px=4)
        return True

    def _click_avatar_name(self, screen, identity) -> bool:
        task = self.task
        height, width = screen.shape[:2]
        task.human.click(width - 52, 115)
        time.sleep(task.composer_entry.timing.sample(.6, 1.0, 1.6) if hasattr(task, "composer_entry") else 1.0)
        menu = task.client.screenshot()
        item = self._find_name(menu, (max(0, width - 420), 130, 400, 260), identity["profile_name"])
        if item is None:
            task.human.key_press("Escape")
            return False
        task.click_reversible(item["center"], label="avatar_menu_profile", bounds=item.get("bounds"), max_offset_px=4)
        return True

    def _click_logo(self, screen) -> bool:
        self.task.human.click(35, 115)
        return self._landed(HOME_URL)

    def _landed(self, expected_url: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        expected = page_key(expected_url)
        while time.monotonic() < deadline:
            time.sleep(1.0)
            screen = self.task.client.screenshot()
            if browser_navigation_state(screen) != "idle":
                continue
            url = self.current_url(screen)
            if is_facebook_url(url) and page_key(url) in ({expected, "/home.php"} if expected == "/" else {expected}):
                return True
        return False

    def _settled_home(self, timeout: float = 15.0):
        """The feed screen once Facebook's home page has loaded, else None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(1.0)
            screen = self.task.client.screenshot()
            if browser_navigation_state(screen) != "idle":
                continue
            url = self.current_url(screen)
            if (is_facebook_url(url) and page_key(url) in {"/", "/home.php"}
                    and self.task.recognizer.observe(screen).state in _SETTLED_STATES):
                return screen
        return None

    def _count(self, name: str) -> None:
        counts = getattr(self.task, "_natural_navigation_counts", None)
        if counts is None:
            counts = self.task._natural_navigation_counts = {}
        counts[name] = counts.get(name, 0) + 1
