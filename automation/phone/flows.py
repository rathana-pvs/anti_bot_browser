"""Facebook flows on a phone, done the way a person would: launcher icon, feed, Gallery, profile picture.

Every step reads the screen with OCR and taps what it sees; Facebook is never started or steered by an
intent from the shell. A step that does not reach its screen fails the job with a report. Nothing after
the publish tap is ever retried: an unconfirmed publish is uncertain, not failed.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from phone import screen
from phone.device import KEY_BACK, KEY_ESCAPE, KEY_HOME
from phone.screen import Line, Point, match, texts

# A URL or long digit run that nobody types on a phone; paste is used so the caption lands verbatim.
_PASTE_PATTERNS = (
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"\b\w+\.(com|net|org|co|io|me|app|dev|xyz|info|biz)\b", re.IGNORECASE),
    re.compile(r"\d{6,}"),
)


def _should_paste(text: str) -> bool:
    """True when the text is something a real person pastes rather than types on a phone: URLs,
    long number runs, or multi-line captions. For other text, length and content weight the choice."""
    if "\n" in text:
        return True
    if any(pattern.search(text) for pattern in _PASTE_PATTERNS):
        return True
    if len(text) <= 30:
        return False
    if len(text) > 150:
        return random.random() < 0.70
    return random.random() < 0.20

FEED_PROMPT = "What's on your mind?"
# A Page's composer once a photo is picked; it shows POST right there, with no Next.
PHOTO_PROMPT = "Say something about this photo"
# The same composer for a video that is not 9:16: it would post as a plain video, not a reel, so
# such a video goes through the profile's Create reel button instead.
VIDEO_PROMPT = "Say something about this video"
# Seconds before each of the three looks for a new post on the profile; a reel can take a while.
COMMENT_WAITS = (30, 25, 25)
# A reel can take minutes after its upload bar goes before Facebook shows it on the profile.
REEL_COMMENT_WAITS = (30, 60, 120, 120)


def comment_waits(kind: str) -> tuple[float, ...]:
    """Seconds before each look for the post: the first from the publish, the rest from the look before."""
    return REEL_COMMENT_WAITS if kind == "reel" else COMMENT_WAITS


def comment_looks(kind: str, published_ago: float) -> tuple[float, ...]:
    """The looks still ahead for a comment job that starts `published_ago` s after the publish.
    Looks already missed become one look now; the later ones keep their times."""
    times, total = [], 0.0
    for wait in comment_waits(kind):
        total += wait
        times.append(total)
    ahead = [t for t in times if t > published_ago]
    if len(ahead) < len(times):
        ahead.insert(0, published_ago)
    return tuple(b - a for a, b in zip([0.0, *ahead], ahead))
PUBLISH_CONFIRM_TIMEOUT_S = 180
# How long a greyed-out publish button may stay grey before the job fails without tapping it.
PUBLISH_ENABLE_TIMEOUT_S = 180
PUBLISH_ENABLE_SCROLLS = 3  # swipes down a settings list whose end must be seen before its button turns blue
# A big video on a slow proxy may keep "Posting" up for long; leaving kills the upload.
UPLOAD_TIMEOUT_S = 900
# The bar can vanish for a moment or be misread, so it must stay gone this long.
UPLOAD_GONE_S = 20
# How long a reel waits on its result screen when the bar was never seen.
UPLOAD_NEVER_SEEN_S = 60
CAPTION_TRIES = 2  # pastes into a caption field that stays empty: one more after a paste that went nowhere
KEYBOARD_WAIT_S = 3.0  # for the keyboard after a tap on a text field, before typing or pasting
CAPTION_PROMPTS = [FEED_PROMPT, PHOTO_PROMPT, "Describe your reel", "Add a description"]  # an empty caption field
# The comment box's placeholder: "Write a comment..." on posts, "Add a comment" in the reel player, and
# "Comment as NAME" under the "Get the ball rolling" sheet of a post with no comments yet.
COMMENT_BOX = ["Write a comment", "Add a comment", "Comment as"]
# Swipes down the profile looking for its tabs; promo cards can push them about three swipes below the top.
PROFILE_TAB_SCROLLS = 6
NEW_POST_MAX_MINUTES = 10  # a post whose time label is at most this old counts as this job's
PROFILE_BACK_UP_SCROLLS = 2  # swipes back up before an older post ends a look: the profile may open past our post
# Home taps that jump the feed to its top. A tap can stop short while the feed still coasts, so it gets one more.
HOME_TAPS = 2
LAUNCHES = 2  # reopenings of Facebook from the app drawer while returning to its feed
PLUS_LOOKS = 2  # looks for the feed's + button, the second after the screen settles
CREATE_BAR_BOTTOM = 300  # 1080-wide px: the feed's top bar, where its + sits, ends above this
# Taps on the gallery's newest cell. One that lands while the grid still loads opens nothing (seen on a Pixel).
PICKER_TAPS = 3
GRID_CELL_PX = 360  # 1080-wide px: one cell of the three-across gallery grid
CONSENT_TAPS = 2  # taps on Facebook's "Allow access" (camera roll) before the step gives up
# Facebook hides its tab bar (Home included) while the feed is scrolled down. A slow drag up does not bring it
# back; a short quick flick up does (seen on a Pixel 7 Pro), without moving the feed much.
TAB_BAR_FLICK_S = 0.18
TAB_BAR_REVEALS = 2
# Reads of the composer's position before the profile picture beside it is tapped: two reads in a row must
# agree, so a feed still moving after Home cannot carry the stories row (Create story, Create reel) under the tap.
PROFILE_TAP_READS = 4
PROFILE_TAP_STILL_PX = 20  # 1080-wide px the composer may move between two reads and still count as still
# Taps before a tap that keeps opening Create story or Create reel fails the step. The feed can jump on its
# own right before a tap (seen on a Pixel 7 Pro: a refresh lands and the top bar folds away), so misses happen.
PROFILE_TAP_TRIES = 3
# What a missed profile tap opens: the stories row's cards right under the profile picture.
CREATE_SCREENS = ["Create story", "Create reel"]
# Swipes up to the top of the profile (its "followers" line) before a look scans down the posts.
PROFILE_TOP_SCROLLS = 6
# A slow drag down the posts list. A quick 0.35 s swipe flings the list on well past the drag, which
# can carry this job's post header (its time label) under the profile's sticky tab bar unread.
PROFILE_SCAN_SWIPE_S = 1.2
# A back-up drag that moves the older post's time label less than this (1080-wide px) did not scroll.
PROFILE_MOVED_MIN = 40
# Seconds spent reading between scrolls of a warming session.
READING_PAUSES = {"quick": (1.0, 3.0), "balanced": (2.5, 5.5), "relaxed": (5.0, 10.0)}


class StepFailed(Exception):
    """A step did not reach the screen it expects. Before the publish tap a job can be retried."""


class NotAReel(StepFailed):
    """Facebook opened a plain video post for the picked video."""


class AccountBlocked(Exception):
    """Logged out or stopped by Facebook: the account needs review."""


class PublishUncertain(Exception):
    """The publish tap happened but the post was not confirmed. Never published again automatically."""


class CommentUncertain(Exception):
    """The send arrow was tapped but the comment did not show. Never sent again automatically."""


@dataclass
class PostItem:
    kind: str  # "photo" or "reel"
    caption: str
    comment: str = ""
    caption_key: str = field(init=False)

    def __post_init__(self):
        if self.kind not in ("photo", "reel"):
            raise ValueError("kind must be photo or reel")
        self.caption_key = screen.caption_key(self.caption)


class Flows:
    def __init__(self, device, log: Callable[..., None]):
        self.device = device
        self.log = log

    # --- waiting ------------------------------------------------------------------------------
    async def wait_for(self, step: str, any_of: list[str], timeout: float = 20.0, exact: bool = False,
                       handle_interruptions: bool = True, evidence: bool = True,
                       discard_old: bool = False, whole: tuple[str, ...] = (),
                       icons: tuple[str, ...] = ()) -> tuple[str, Point, list[Line]]:
        """Until one of the texts (or else one of the `icons` templates, returned by name) shows; known
        pop-ups are closed meanwhile. A caller that recovers from a
        miss passes `evidence=False`, so only real failures leave a fail_ screenshot. `discard_old` is for a
        step before this job has made anything: a prompt about an earlier job's post is answered with discard.
        The texts in `whole` must fill their line (see `match`)."""
        dev = self.device
        deadline = dev.now() + timeout
        lines: list[Line] = []
        while dev.now() < deadline:
            lines = await dev.read()
            old_post = screen.discard_button(lines) if discard_old else None
            if old_post:
                self.log(event="leftover", screen=step, action="discard")
                await dev.tap(old_post)
                await dev.settle(timeout=2.0)
                continue
            for wanted in any_of:
                point = match(lines, wanted, exact, whole=wanted in whole)
                if point:
                    return wanted, point, lines
            for icon in icons:
                point = dev.find_icon(icon)
                if point:
                    return icon, point, lines
            self.check_account(lines)
            if handle_interruptions and await self.close_interruption(lines):
                continue
            await dev.sleep(0.6)
        if evidence:
            dev.snapshot(f"fail_{step.replace(' ', '_')}_{int(time.time())}")
        raise StepFailed(f"{step}: none of {any_of} appeared; screen shows {texts(lines)}")

    def check_account(self, lines: list[Line]) -> None:
        if screen.logged_out(lines):
            self.device.snapshot(f"logged_out_{int(time.time())}")
            raise AccountBlocked("Facebook shows its login screen: the account is logged out")
        if screen.checkpoint(lines):
            self.device.snapshot(f"checkpoint_{int(time.time())}")
            raise AccountBlocked(f"Facebook stopped the account: {texts(lines, 6)}")

    async def close_interruption(self, lines: list[Line]) -> bool:
        found = screen.interruption(lines)
        if not found:
            return False
        kind, action, point = found
        self.log(event="interruption", kind=kind, context=texts(lines, 4))
        if action == "tap":
            await self.device.tap(point)
        else:
            await self.device.key(KEY_BACK)
        return True

    async def _wait_for_keyboard(self) -> bool:
        """Until the keyboard shows (the tapped field has focus), at most KEYBOARD_WAIT_S. False when it
        never did: the text is still entered, as some fields open their own screen first."""
        dev = self.device
        deadline = dev.now() + KEYBOARD_WAIT_S
        while True:
            if await asyncio.to_thread(dev.keyboard_shown):
                return True
            if dev.now() >= deadline:
                self.log(event="keyboard", action="not shown after the tap")
                return False
            await dev.sleep(0.3)

    async def scroll(self, start: Point, end: Point, duration: float = 0.35) -> None:
        """A swipe that never lands on the keyboard: with the keyboard up (a comment box focused) it
        would gesture-type words into the box. The keyboard is hidden and its sheet closed first."""
        dev = self.device
        if await asyncio.to_thread(dev.keyboard_shown):
            self.log(event="keyboard", action="hide and close before scrolling")
            await dev.key(KEY_ESCAPE)
            if await asyncio.to_thread(dev.keyboard_shown):
                raise StepFailed("keyboard: still shown after Escape; not scrolling over it")
            await dev.key(KEY_BACK)
        await dev.swipe(start, end, duration=duration)

    # --- warming ------------------------------------------------------------------------------
    async def warm(self, scrolls: int = 3) -> None:
        """A short browse before the session's first post: varied swipes with the occasional backtrack
        and, once in a while, a post opened by its text. No-op after the first call until the device's
        session closes. Failures are swallowed: warming is comfort, never fatal."""
        dev = self.device
        if dev._warmed or scrolls <= 0:
            return
        dev._warmed = True
        reverse_at = random.randint(4, 6)
        tap_after = random.randint(2, 3)
        forward_count = 0
        try:
            for i in range(scrolls):
                dwell = random.uniform(4.0, 8.0) if random.random() < 0.15 else random.uniform(0.8, 2.5)
                await dev.sleep(dwell)
                if forward_count >= tap_after and random.random() < 0.5:
                    lines = await dev.read()
                    readable = screen.readable_lines(lines, dev.height)
                    if readable:
                        await self._read_post(random.choice(readable), dev.sleep)
                        tap_after = forward_count + random.randint(3, 5)
                        continue
                back = i > 0 and i % reverse_at == 0
                if back:
                    reverse_at = i + random.randint(4, 6)
                else:
                    forward_count += 1
                start, end, duration = self._feed_swipe(back)
                await dev.swipe(start, end, duration=duration)
        except (StepFailed, Exception) as warm_error:  # don't let warming fail the job
            self.log(event="warm_skipped", reason=str(warm_error)[:200])

    def _feed_swipe(self, back: bool = False) -> tuple[Point, Point, float]:
        """A varied swipe: finger up moves the feed forward, finger down a little way back."""
        dev = self.device
        x = dev.width * random.uniform(0.20, 0.80)
        span = dev.height * random.uniform(0.40, 0.75)
        if back:
            start_y = dev.height * random.uniform(0.25, 0.40)
            end_y = min(dev.height * 0.95, start_y + span * 0.5)
        else:
            start_y = dev.height * random.uniform(0.70, 0.85)
            end_y = max(dev.height * 0.05, start_y - span)
        return (x, start_y), (x, end_y), random.uniform(0.25, 0.55)

    async def visit(self, options: dict, should_stop: Callable[[], bool] = lambda: False) -> dict:
        """A standalone warming session from the feed: read-only browsing of the feed and the account's
        own Page until the time is up or `should_stop` says so. Nothing is liked, commented or typed."""
        dev = self.device
        begun = dev.now()
        requested = options["surface"] if options["surface"] != "random" else random.choice(("news_feed", "profile"))
        own_page_seconds = options["max_seconds"] if requested == "profile" else 0.0
        if requested == "news_feed" and options["auto"] and random.random() < 0.6:
            own_page_seconds = options["max_seconds"] * random.uniform(0.2, 0.4)
        browsing = {"pace": options["pace"], "reread": options["reread"],
                    "long_breaks": options["long_breaks"], "should_stop": should_stop}
        scrolls, fallback = 0, False
        if requested == "news_feed":
            scrolls += await self.browse(options["max_seconds"] - own_page_seconds, **browsing)
        if own_page_seconds and not should_stop():
            try:
                await self.discard_until_feed()
                await self.open_profile()
                self.log(event="warm", action="own page")
                scrolls += await self.browse(own_page_seconds, read_posts=False, **browsing)
            except StepFailed as unavailable:
                self.log(event="warm", action="own page unavailable", reason=str(unavailable)[:200])
                fallback = True
                await self.discard_until_feed()
                if requested == "profile":
                    scrolls += await self.browse(own_page_seconds, **browsing)
        return {"warming_surface_requested": requested,
                "warming_surface": "news_feed" if fallback else requested,
                "warming_surface_fallback": fallback, "scroll_actions": scrolls,
                "duration_seconds": round(dev.now() - begun, 1),
                "stop_reason": "asked_to_stop" if should_stop() else "finished"}

    async def browse(self, seconds: float, *, pace: str = "balanced", reread: bool = False, long_breaks: bool = False,
                     read_posts: bool = True, should_stop: Callable[[], bool] = lambda: False) -> int:
        """Scrolls with reading pauses and, now and then, opens a post by its text. Every pass reads
        the screen, so a logged-out or stopped account ends the session. Returns the scrolls made."""
        dev = self.device
        until = dev.now() + seconds
        scrolls, reopened = 0, False
        read_at = random.randint(3, 6)

        async def pause(wanted: float) -> None:
            end = min(dev.now() + wanted, until)
            while dev.now() < end and not should_stop():
                await dev.sleep(min(2.0, end - dev.now()))

        while dev.now() < until and not should_stop():
            lines = await dev.read()
            self.check_account(lines)
            if await self.close_interruption(lines):
                continue
            focus = await asyncio.to_thread(dev.focus)
            if focus and "com.facebook" not in focus:
                if reopened:
                    raise StepFailed(f"browse: left Facebook again; {focus} is showing")
                self.log(event="warm", action="back to Facebook", focus=focus)
                reopened = True
                await self.open_feed()
                continue
            if read_posts and scrolls >= read_at:
                read_at = scrolls + random.randint(4, 8)
                readable = screen.readable_lines(lines, dev.height)
                if readable:
                    await self._read_post(random.choice(readable), pause)
                    continue
            back = reread and scrolls > 0 and random.random() < 0.15
            start, end, duration = self._feed_swipe(back)
            await self.scroll(start, end, duration)
            scrolls += 1
            self.log(event="warm", action="scroll back" if back else "scroll", scrolls=scrolls)
            await pause(random.uniform(*READING_PAUSES[pace]))
            if long_breaks and random.random() < 0.08:
                self.log(event="warm", action="long pause")
                await pause(random.uniform(8.0, 15.0))
        return scrolls

    async def _read_post(self, line: Line, pause) -> None:
        """Taps a post's text, reads, and comes back only if something actually opened: Back on the
        feed itself would leave Facebook. A long caption just expands in place; a post with media can
        open Facebook's full-screen viewer, which has no comment box but is its own window."""
        dev = self.device
        before = await asyncio.to_thread(dev.focus)
        self.log(event="warm", action="read post")
        await dev.tap(line.point)
        await pause(random.uniform(4.0, 10.0))
        lines = await dev.read()
        if await asyncio.to_thread(dev.focus) != before or any(match(lines, box) for box in COMMENT_BOX):
            await dev.key(KEY_BACK)
            await dev.sleep(random.uniform(0.8, 1.5))
            self.log(event="warm", action="closed post", focus=await asyncio.to_thread(dev.focus))

    # --- feed ---------------------------------------------------------------------------------
    async def open_feed(self) -> None:
        """Facebook on its feed, scrolled to the top, with nothing half-made left open."""
        await self._launch_from_drawer()
        await self.discard_until_feed()

    async def _launch_from_drawer(self) -> None:
        """Facebook opened by its icon in the phone's app drawer."""
        dev = self.device
        # Home twice closes an open app drawer; then open it, scrolled to the top when needed.
        await dev.key(KEY_HOME)
        await dev.key(KEY_HOME)
        # A sheet over the home screen (Android's contact sharing) would take the swipe that opens the drawer.
        if await self.close_interruption(await dev.read()):
            await dev.key(KEY_HOME)
        await dev.swipe(dev.pt(540, 1900), dev.pt(540, 600))
        label = None
        for attempt in range(3):
            lines = await dev.read()
            label = match(lines, "Facebook", exact=True)
            if label:
                break
            self.log(event="retry", step="app drawer", attempt=attempt + 1, screen=texts(lines, 8))
            if await self.close_interruption(lines):  # it came up after the swipe: close it, swipe again
                await dev.key(KEY_HOME)
                await dev.swipe(dev.pt(540, 1900), dev.pt(540, 600))
                continue
            await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
        if not label:
            dev.snapshot(f"fail_drawer_{int(time.time())}")
            raise StepFailed("app drawer: the Facebook icon was not found")
        await dev.tap((label[0], label[1] - dev.pt(0, 110)[1]))  # the icon sits above its label

    async def _relaunch_if_left(self, launches: int) -> bool:
        """When the phone's launcher (home screen or app drawer) is in front instead of Facebook, Facebook
        is opened from the drawer again; Back there does nothing. The drawer's own "Facebook" label must
        not pass for a Facebook screen either. False when Facebook is in front, or after LAUNCHES tries."""
        focus = await asyncio.to_thread(self.device.focus) or ""
        if launches >= LAUNCHES or "launcher" not in focus.casefold():
            return False
        self.log(event="feed", action="reopen facebook", reason="the phone's home screen is in front", attempt=launches + 1)
        await self._launch_from_drawer()
        return True

    async def discard_until_feed(self) -> None:
        """Back to the feed without publishing anything: a draft left by an earlier job is discarded."""
        dev = self.device
        home_taps = reveals = launches = 0
        for _ in range(8):
            try:
                found, _, lines = await self.wait_for("open facebook", [
                    FEED_PROMPT, "New post", "Create post", "New reel", "Post settings", "Reel settings", "Discard changes",
                    "Discard post", "Discard video", "Discard draft", screen.LEFT_UNFINISHED, "facebook"], timeout=12, evidence=False)
            except StepFailed as unknown:
                # A feed scrolled down hides its top and tab bars, so none of the feed's words show.
                # A flick brings the tab bar back; Home then jumps to the top. Back only when it does not.
                if reveals < TAB_BAR_REVEALS and home_taps < HOME_TAPS and "com.facebook" in (
                        await asyncio.to_thread(dev.focus) or ""):
                    reveals += 1
                    home = await self._reveal_home_tab()
                    if home:
                        home_taps += 1
                        self.log(event="feed", action="tap Home", attempt=home_taps, after="hidden tab bar")
                        await dev.tap(home)
                        await dev.settle(timeout=2.0)
                        continue
                if await self._relaunch_if_left(launches):
                    launches += 1
                    continue
                self.log(event="unknown screen", action="back", detail=str(unknown)[:200])
                await dev.key(KEY_BACK)
                continue
            discard = screen.discard_button(lines)
            if discard:
                self.log(event="leftover", action="discard")
                await dev.tap(discard)
                await dev.sleep(1)
                continue
            if match(lines, "facebook", exact=True) and not match(lines, "New post"):
                if match(lines, FEED_PROMPT):
                    return
                home = None
                if home_taps < HOME_TAPS:
                    home = dev.find_icon("home_tab")
                    if not home and reveals < TAB_BAR_REVEALS:
                        home = await self._reveal_home_tab()
                        reveals += 1
                if home:  # Jump to the top first; if it does not work, fall back to bounded swipes.
                    home_taps += 1
                    self.log(event="feed", action="tap Home", attempt=home_taps)
                    await dev.tap(home)
                else:
                    self.log(event="feed", action="scroll to top")  # reopened part-way down the feed
                    await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
                    await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
                await dev.settle(timeout=2.0)  # read the feed where it stopped, not while it still moves
                continue
            if await self._relaunch_if_left(launches):
                launches += 1
                continue
            self.log(event="leftover", screen=found, action="back")
            await dev.key(KEY_BACK)
            await dev.sleep(0.8)
        lines = await dev.read()  # the last pass may have just reached the top
        if match(lines, "facebook", exact=True) and match(lines, FEED_PROMPT):
            return
        dev.snapshot(f"fail_feed_{int(time.time())}")
        raise StepFailed(f"open facebook: could not reach the feed; screen shows {texts(lines)}")

    async def _reveal_home_tab(self) -> Point | None:
        """A short quick flick up, so Facebook shows its hidden tab bar again; then the Home icon, if it shows."""
        dev = self.device
        self.log(event="feed", action="reveal tab bar")
        await self.scroll(dev.pt(540, 1000), dev.pt(540, 1350), duration=TAB_BAR_FLICK_S)
        await dev.settle(timeout=1.5)
        home = dev.find_icon("home_tab")
        if not home:
            self.log(event="feed", action="tab bar still hidden")
        return home

    # --- composing ----------------------------------------------------------------------------
    async def start_post(self, item: PostItem) -> None:
        """The composer with this job's media and caption."""
        # Warming can leave the feed far below its composer.
        await self.discard_until_feed()
        if item.kind == "reel" and await self._reel_from_create_menu():
            await self._pick_reel()
        else:
            try:
                await self._compose_from_gallery()
            except NotAReel as plain:
                self.log(event="route", step="create reel", reason=str(plain))
                await self.discard_until_feed()
                await self._compose_from_create_reel()
        await self.write_caption(item)

    async def _reel_from_create_menu(self) -> bool:
        """From the feed: its + menu, then Reel. False, with the menu closed, when this feed has neither."""
        dev = self.device
        # Facebook draws its + as a rounded square on some feeds and as a circle on others. The circle is
        # taken only from the top bar: further down a reel's settings have a dashed one (Share to story).
        plus = None
        for look in range(PLUS_LOOKS):
            if look:  # the top bar can still be sliding back in after Home brought the feed up
                await dev.settle(timeout=1.5)
            plus = dev.find_icon("create")
            if not plus:
                round_plus = dev.find_icon("create_round")
                plus = round_plus if round_plus and round_plus[1] < dev.pt(0, CREATE_BAR_BOTTOM)[1] else None
            if plus:
                break
        if not plus:
            dev.snapshot(f"no_plus_{int(time.time())}")
            self.log(event="route", step="create menu", reason="no + button on the feed")
            return False
        await dev.tap(plus)
        try:
            _, _, lines = await self.wait_for("create menu", ["Story", "Live"], exact=True, timeout=6)
            reel = screen.menu_reel(lines)
        except StepFailed:
            reel = None
        if not reel:
            self.log(event="route", step="create menu", reason="the + menu has no Reel")
            await dev.key(KEY_BACK)
            await self.discard_until_feed()
            return False
        await dev.tap(reel)
        return True

    async def _compose_from_gallery(self) -> None:
        dev = self.device
        point = await self.open_gallery()
        # The job's media was just saved, so it is the newest: the first cell, its circle top-right.
        offset = dev.pt(168, 110)
        cell = (point[0] + offset[0], point[1] + offset[1])
        # The grid's thumbnails draw after its "Gallery" title; a tap on a cell still loading does nothing.
        await dev.settle(timeout=2.0)
        await dev.tap(cell)
        # Picking can open the composer at once. Its own Next leads past the caption, so the prompts come first.
        # A tap that changed nothing (the picker still up, no Next) is tapped again: one more tap then
        # only selects what the first did not.
        for attempt in range(1, PICKER_TAPS + 1):
            last = attempt == PICKER_TAPS
            try:
                found, nxt, _ = await self.wait_for("picker selection", [VIDEO_PROMPT, PHOTO_PROMPT, FEED_PROMPT, "Next"],
                                                     timeout=10 if last else 4, whole=("Next",), evidence=last)
                break
            except StepFailed:
                if last or not match(await dev.read(), "Gallery", exact=True):
                    raise
                self.log(event="retry", step="picker selection", attempt=attempt + 1, reason="the tap opened nothing")
                await dev.tap(cell)
        if found == "Next":  # the composer's prompt may not have drawn yet
            await dev.settle(timeout=2.0)
            lines = await dev.read()
            found = next((prompt for prompt in (VIDEO_PROMPT, PHOTO_PROMPT, FEED_PROMPT) if match(lines, prompt)), found)
        if found == VIDEO_PROMPT:
            raise NotAReel("reel: Facebook opened a plain video post, not a reel; the video may not be 9:16")
        if found == "Next":
            await dev.tap(nxt)
        await self.wait_for("composer with media", [FEED_PROMPT, PHOTO_PROMPT, "Describe your reel",
                                                    "Add a description"], timeout=30)

    async def _compose_from_create_reel(self) -> None:
        """From the feed: the profile's Create reel button, the newest video in its picker, then Next."""
        dev = self.device
        await self._tap_profile_picture()
        _, _, lines = await self.wait_for("profile", ["Create reel", "followers", "All", "Posts"], timeout=15)
        button = match(lines, "Create reel")
        # The profile reopens where it was last scrolled to, so it is scrolled up to its header first. A new
        # Page's "Finish setting up your Page" card can push the button below the screen, so from the header
        # (its followers line) it is looked for further down.
        for _ in range(4):
            if button or match(lines, "followers"):
                break
            await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
            await dev.settle(timeout=2.0)
            lines = await dev.read()
            button = match(lines, "Create reel")
        for _ in range(PROFILE_TAB_SCROLLS):
            if button:
                break
            self.log(event="route", step="create reel", action="scroll down")
            await self.scroll(dev.pt(540, 1700), dev.pt(540, 1150))
            await dev.settle(timeout=2.0)
            button = match(await dev.read(), "Create reel")
        if not button:
            dev.snapshot(f"fail_create_reel_{int(time.time())}")
            raise StepFailed("create reel: the button was not found on the profile")
        await dev.tap(button)
        await self._pick_reel()

    async def _pick_reel(self) -> None:
        """From the reel picker: the newest video, then Next, to the composer."""
        dev = self.device
        # The first cell, from the "Gallery" title (whether or not a Drafts row sits above the grid), or, in
        # the layout without that title, from the "Recents" chip in the filter row right above the grid.
        offsets = {"Gallery": dev.pt(68, 394), "Recents": dev.pt(75, 290)}
        for _ in range(2):
            found, anchor, lines = await self.wait_for("reel picker", [*offsets, "Allow access"], exact=True, timeout=15)
            # Facebook's own camera roll consent, shown in place of the grid the first time (posting needs it).
            for _ in range(CONSENT_TAPS):
                if found != "Allow access":
                    break
                self.log(event="interruption", kind="camera roll consent", step="reel picker", answer="Allow access")
                await dev.tap(anchor)
                found, anchor, lines = await self.wait_for("reel picker", [*offsets, "Allow access"], exact=True,
                                                           timeout=10)
            if found == "Allow access":
                dev.snapshot(f"fail_reel_picker_{int(time.time())}")
                raise StepFailed(f"reel picker: still asking for camera roll access; {texts(lines)}")
            if not await self._discard_unfinished_reel(lines):
                offset = offsets[found]
                cell = (anchor[0] + offset[0], anchor[1] + offset[1])
                # With access to selected media only, Facebook puts a "View all media" tile in the first cell
                # (under a "You've allowed access to select media" bar): the newest video is the next cell.
                tile = match(lines, "View all")
                if not tile and match(lines, "allowed access to select"):  # the bar is up, its grid still drawing
                    await dev.settle(timeout=2.0)
                    tile = match(await dev.read(), "View all")
                if tile:
                    cell = (tile[0] + dev.pt(GRID_CELL_PX, 0)[0], tile[1])
                    self.log(event="reel picker", layout="selected media", action="tap the cell after View all media")
                await dev.tap(cell)
                button, lines = await self._wait_reel_editor()
                if button:
                    break
                await self._discard_unfinished_reel(lines)
        else:
            raise StepFailed(f"reel picker: an unfinished reel keeps coming back; {texts(lines)}")
        # Next can be drawn before the editor takes taps (the clip still loading, its "Add audio" hint
        # sliding in), so a tap may leave the editor up. Next is tapped again while the editor stays.
        for attempt in range(1, 4):
            self.log(event="reel editor next", label=button.label or "(icon)", attempt=attempt)
            await dev.tap(button.point)
            try:
                await self.wait_for("composer with media", ["Describe your reel", "Add a description"],
                                    timeout=10, evidence=attempt == 3)
                return
            except StepFailed:
                if attempt == 3:
                    raise
                after = await dev.read()
                if any(match(after, prompt) for prompt in ("Describe your reel", "Add a description")):
                    return  # it opened just as the wait ran out
                again = self._editor_button(after)
                if not again:
                    dev.snapshot(f"fail_composer_with_media_{int(time.time())}")
                    raise
                self.log(event="retry", step="reel editor next", attempt=attempt + 1)
                button = again

    def _editor_button(self, lines: list[Line]) -> screen.ActionButton | None:
        """The reel editor's step forward: its blue button, found by colour and place, whatever its label.
        A label that posts (Share...) is never tapped here, as the caption comes after this screen; an
        unknown label stops the job with a screenshot, so a new word is added to the list rather than
        guessed. An icon with no label is tapped only once the editor's own toolbar is read."""
        button = screen.action_button(lines, self.device.judged_image())
        if not button:
            return None
        if button.kind == "forward" or (button.kind == "" and screen.reel_editor_shown(lines)):
            return button
        if button.kind == "":
            return None  # a blue shape before the editor has drawn its toolbar
        self.device.snapshot(f"fail_reel_editor_button_{int(time.time())}")
        if button.kind == "publish":
            raise StepFailed(f"reel editor: its blue button says {button.label!r}, which would post before "
                             "the caption; Facebook's layout changed; nothing was tapped")
        raise StepFailed(f"reel editor: its blue button says {button.label!r}, a label not known as a step "
                         f"forward ({', '.join(screen.FORWARD_LABELS)}); nothing was tapped")

    async def _wait_reel_editor(self, timeout: float = 30.0) -> tuple[screen.ActionButton | None, list[Line]]:
        """Until the editor's step-forward button shows (returned) or the unfinished-reel prompt (None)."""
        dev = self.device
        deadline = dev.now() + timeout
        lines: list[Line] = []
        while dev.now() < deadline:
            lines = await dev.read()
            if match(lines, screen.LEFT_UNFINISHED):
                return None, lines
            button = self._editor_button(lines)
            if button:
                return button, lines
            self.check_account(lines)
            if await self.close_interruption(lines):
                continue
            await dev.sleep(0.6)
        dev.snapshot(f"fail_reel_editor_{int(time.time())}")
        raise StepFailed(f"reel editor: no step-forward button appeared; screen shows {texts(lines)}")

    async def _discard_unfinished_reel(self, lines: list[Line]) -> bool:
        """A stuck earlier job leaves a reel behind, and Facebook offers it back in the picker. This job has
        not started its own yet, so only the old one is lost."""
        if not match(lines, screen.LEFT_UNFINISHED):
            return False
        discard = match(lines, "DISCARD", exact=True)
        if not discard:
            raise StepFailed(f"reel picker: an unfinished reel without a Discard button; {texts(lines)}")
        self.log(event="leftover", screen="unfinished reel", action="discard")
        await self.device.tap(discard)
        await self.device.settle(timeout=2.0)
        return True

    async def open_gallery(self) -> Point:
        """From the feed: the composer, then its Gallery picker, past Facebook's camera roll consent
        (shown once; posting needs it). Returns the picker's "Gallery" title."""
        dev = self.device
        for last_try in (False, True):
            _, point, _ = await self.wait_for("feed", [FEED_PROMPT])
            await dev.tap(point)
            # Facebook shows a Gallery tile (tapped above its label), a list with a Photo/video row, or a bar
            # of icons where the photo icon comes first. OCR does not always read the bar's GIF, so the photo
            # icon is also looked for by its picture.
            try:
                found, entry, _ = await self.wait_for("composer", ["Gallery", "Photo/video", "GIF"], exact=True,
                                                      evidence=last_try, discard_old=True,
                                                      icons=("composer_photo",))
                break
            except StepFailed as missed:  # a tap while the feed still moves only stops it
                if last_try:
                    raise
                self.log(event="retry", step="composer", detail=str(missed)[:200])
                await self.discard_until_feed()
        if found == "composer_photo":
            await dev.tap(entry)
        elif found == "GIF":
            # A full-width bar starts at the left edge; the newer floating bar is centred, with the photo
            # icon two slots (about 128 px each at 1080 wide) left of GIF.
            centred = entry[0] > dev.pt(400, 0)[0]
            await dev.tap((entry[0] - dev.pt(255, 0)[0] if centred else dev.pt(90, 0)[0], entry[1]))
        else:
            await dev.tap(entry if found == "Photo/video" else (entry[0], entry[1] - dev.pt(0, 35)[1]))
        found, point, lines = await self.wait_for("picker", ["Gallery", "Allow access", "Allow Facebook to access"],
                                                  timeout=15)
        for _ in range(2):  # Facebook's own camera roll consent, first time only; posting needs it
            if found == "Gallery" and not match(lines, "Allow access"):
                break
            target = match(lines, "Allow", exact=True) or match(lines, "Allow access", exact=True)
            if not target:
                raise StepFailed(f"picker: camera roll consent without an Allow button; {texts(lines)}")
            self.log(event="interruption", kind="camera roll consent", answer="Allow")
            await dev.tap(target)
            found, point, lines = await self.wait_for("picker", ["Gallery", "Allow"], timeout=10)
        if found != "Gallery" or match(lines, "Allow access"):
            raise StepFailed(f"picker: still asking for camera roll access; {texts(lines)}")
        return point

    async def get_ready(self) -> None:
        """First use after login: open the Gallery picker once, so Facebook's own consent screens are
        answered before any job, then leave without posting."""
        await self.open_feed()
        await self.open_gallery()
        self.log(event="gallery_ready")
        await self.device.key(KEY_BACK)  # the picker, then the empty composer is discarded
        await self.discard_until_feed()

    async def _enter_text(self, text: str, kind: str) -> None:
        """Paste or type into the focused field. Paste is used for URLs, newlines and long captions;
        otherwise we type with per-chunk delays. A mid-type failure falls back to paste so one
        flaky keystroke doesn't lose the job."""
        if not text:
            return
        dev = self.device
        if _should_paste(text):
            self.log(event="input", kind=kind, method="paste", length=len(text))
            await dev.paste(text)
            return
        try:
            self.log(event="input", kind=kind, method="type", length=len(text))
            await dev.type_text(text)
        except Exception as typing_failed:
            self.log(event="input_fallback", kind=kind, reason=str(typing_failed)[:200])
            await dev.paste(text)

    async def write_caption(self, item: PostItem) -> None:
        """Paste never drops text, so the caption is not read back (OCR misreads it); the field only has to
        have left its empty prompt. A prompt still showing is given a moment, as the paste may still be drawing."""
        dev = self.device
        _, field_point, _ = await self.wait_for("caption field", CAPTION_PROMPTS, timeout=15)
        lines: list[Line] = []
        for attempt in range(1, CAPTION_TRIES + 1):
            # A paste before the field has taken the tap (no keyboard yet) goes nowhere and leaves it empty.
            await dev.tap(field_point)
            await self._wait_for_keyboard()
            await self._enter_text(item.caption, "caption")
            await dev.key(KEY_ESCAPE)
            lines = await dev.read()
            for _ in range(3):
                if not item.caption or not any(match(lines, prompt) for prompt in CAPTION_PROMPTS):
                    break
                await dev.settle(timeout=1.0)
                lines = await dev.read()
            # Only a field still showing its empty prompt is pasted into again, so the caption cannot double.
            prompt = next((p for p in (match(lines, prompt) for prompt in CAPTION_PROMPTS) if p), None)
            if not item.caption or not prompt or attempt == CAPTION_TRIES:
                break
            self.log(event="retry", step="caption", attempt=attempt + 1, reason="the field stayed empty")
            field_point = prompt
        if item.caption and any(match(lines, prompt) for prompt in CAPTION_PROMPTS):
            dev.snapshot(f"fail_caption_{int(time.time())}")
            raise StepFailed(f"caption: the field is still empty after paste; screen shows {texts(lines, 10)}")
        done = match(lines, "Done", exact=True)
        if done and match(lines, "Add text", exact=True):  # this composer takes the caption on a screen of its own
            await dev.tap(done)

    async def publish(self, before_tap: Callable[[], None]) -> float:
        """Next, then SHARE or Post (photo) or Share now (reel); a Page's composer has POST, and its "New reel"
        screen has Share now under the caption, both with no Next. `before_tap` records the attempt as about to publish; from the
        tap on, the outcome is uncertain until confirmed. Returns the tap time."""
        dev = self.device
        found, nxt, _ = await self.wait_for("composer next", ["Next", "Share now", "POST"], exact=True)
        if found == "Next":
            await dev.tap(nxt)
            found, _, _ = await self.wait_for("settings", ["Post settings", "Reel settings", "Post audience"], timeout=20)
        button = {"Post settings": "SHARE", "POST": "POST", "Post audience": "Post"}.get(found, "Share now")
        # The title shows before the button has finished drawing.
        _, point, _ = await self.wait_for(f"settings {button}", [button], exact=True, timeout=10,
                                          handle_interruptions=False)
        point = await self._wait_until_enabled(button, point)
        dev.snapshot("before_publish")
        before_tap()
        self.log(event="about_to_publish", screen=found)
        tapped = dev.now()
        await dev.tap(point)
        return tapped

    async def _wait_until_enabled(self, button: str, point: Point) -> Point:
        """A tap on a greyed-out button does nothing, so the post would wrongly count as maybe published.
        Facebook can keep Reel Settings' Share now grey until its list has been scrolled to the end, so the
        list above the pinned button is scrolled down first; a swipe only scrolls, it taps nothing. A toast
        ("All videos will now be posted as reels...") can cover the button: dark, so not grey, but a tap
        there lands on the toast. The button is tapped only while its label is read on screen and not
        grey. Returns where to tap."""
        dev = self.device
        waited_from = dev.now()
        state = None
        scrolls = 0
        while True:
            seen = match(await dev.read(), button, exact=True)
            if seen:
                point = seen
                if not screen.button_disabled(dev.judged_image(), point):
                    if state in (None, "settled"):
                        break
                    # It just turned blue or came out from under a toast: let it settle, then check again.
                    self.log(event="publish_button", state="enabled", seconds=round(dev.now() - waited_from))
                    await dev.settle(timeout=2.0)
                    state = "settled"
                    continue
            now = "disabled" if seen else "hidden"
            if now != state:
                self.log(event="publish_button", state=now, button=button)
                state = now
            if seen and scrolls < PUBLISH_ENABLE_SCROLLS:
                scrolls += 1
                self.log(event="publish_button", action="scroll settings", attempt=scrolls)
                await self.scroll(dev.pt(540, 1800), dev.pt(540, 800))
                await dev.settle(timeout=2.0)
                continue
            if dev.now() >= waited_from + PUBLISH_ENABLE_TIMEOUT_S:
                dev.snapshot(f"fail_publish_disabled_{int(time.time())}")
                why = "disabled" if seen else "covered or not shown"
                raise StepFailed(f"publish: Facebook kept {button} {why} for {PUBLISH_ENABLE_TIMEOUT_S} s; nothing was tapped")
            await dev.sleep(3 if seen else 1)
        return point

    async def wait_published(self, item: PostItem, tapped: float) -> float:
        """A photo shows on the feed with its caption and "Just now"; a reel is confirmed once the
        "Posting" bar has come and stayed gone, since the upload dies if Facebook is left or its Android user
        stopped early (it is then found on the profile for the comment). A Page
        returns to an empty composer after sharing; it also shows the feed's prompt, so the feed is
        told by its logo, and the composer is left with Back. The feed may come back without the
        prompt in view, so the prompt is not required. A reel made with Create reel lands back on
        the profile, on whichever tab it last showed, told by its followers line or Create reel."""
        dev = self.device
        last_uploading = None
        while dev.now() < tapped + PUBLISH_CONFIRM_TIMEOUT_S or (
                last_uploading is not None and dev.now() - last_uploading < UPLOAD_GONE_S + 5  # time to confirm it gone
                and dev.now() < tapped + UPLOAD_TIMEOUT_S):
            lines = await dev.read()
            if await self.close_interruption(lines):
                continue
            if screen.uploading(lines):
                if last_uploading is None:
                    dev.snapshot("upload_showing")
                    self.log(event="upload", state="showing", seconds=round(dev.now() - tapped), screen=texts(lines, 6))
                last_uploading = dev.now()
            elif match(lines, "Create post"):
                self.log(event="leftover", screen="empty composer", action="back")
                await dev.key(KEY_BACK)
            elif match(lines, "facebook", exact=True) or (item.kind == "reel" and not match(lines, "Share now") and (
                    match(lines, "followers") or match(lines, "Create reel"))):
                if item.kind == "photo" and match(lines, "Just now") and (
                        not item.caption_key or match(lines, item.caption_key)):
                    dev.snapshot("publish_confirmed")
                    return dev.now() - tapped
                if item.kind == "reel" and (dev.now() - tapped > UPLOAD_NEVER_SEEN_S if last_uploading is None
                                            else dev.now() - last_uploading >= UPLOAD_GONE_S):
                    dev.snapshot("publish_confirmed")
                    self.log(event="upload", state="gone", seen=last_uploading is not None,
                             seconds=round(dev.now() - tapped), screen=texts(lines, 6))
                    return dev.now() - tapped
            await dev.sleep(1.5)
        dev.snapshot("publish_unconfirmed")
        raise PublishUncertain(f"no confirmation within {PUBLISH_CONFIRM_TIMEOUT_S} s after the publish tap")

    # --- profile and first comment ------------------------------------------------------------
    async def _tap_profile_picture(self) -> None:
        """Taps the profile picture left of the composer once two reads in a row put the composer at the
        same height. Right after Home the feed can still move, and the stories row under the picture
        (Create story, Create reel) would then take the tap."""
        dev = self.device
        still = dev.pt(0, PROFILE_TAP_STILL_PX)[1]
        _, point, _ = await self.wait_for("feed for profile", [FEED_PROMPT])
        for _ in range(PROFILE_TAP_READS):
            await dev.settle(timeout=2.0)
            _, again, _ = await self.wait_for("feed for profile", [FEED_PROMPT])
            if abs(again[1] - point[1]) <= still:
                break
            self.log(event="feed", action="wait for the composer to stop", moved=round(again[1] - point[1]))
            point = again
        else:
            dev.snapshot(f"fail_feed_for_profile_{int(time.time())}")
            raise StepFailed("feed for profile: the feed kept moving; the profile picture was not tapped")
        await dev.tap((dev.pt(77, 0)[0], again[1]))  # profile picture left of the box

    async def _missed_profile_tap(self, timeout: float = 4.0) -> str | None:
        """What a profile tap opened instead of the profile: a create screen, or "the feed" when the tap
        did nothing (a tap on a list still moving only stops it). None: the profile, or a screen the tab
        search below can judge. The profile of a Page has a Create reel button too, so only a screen
        with the picker's Gallery and none of the profile's tabs counts as a create screen."""
        dev = self.device
        deadline = dev.now() + timeout
        on_feed = False
        while dev.now() < deadline:
            lines = await dev.read()
            if match(lines, "Posts", exact=True) or match(lines, "All", exact=True):
                return None
            if match(lines, "Gallery"):
                wrong = next((name for name in CREATE_SCREENS if match(lines, name)), None)
                if wrong:
                    return wrong
            on_feed = bool(match(lines, "facebook", exact=True))  # the profile has no "facebook" bar
            if not on_feed:
                return None
            await dev.sleep(0.6)
        return "the feed" if on_feed else None

    async def open_profile(self) -> None:
        dev = self.device
        for attempt in range(1, PROFILE_TAP_TRIES + 1):
            await self._tap_profile_picture()
            wrong = await self._missed_profile_tap()
            if not wrong:
                break
            # A missed tap: close the create screen (it holds nothing yet) and tap again from the feed.
            self.log(event="profile_tap_missed", screen=wrong, attempt=attempt)
            dev.snapshot(f"profile_tap_missed_{attempt}")
            if wrong != "the feed":
                await dev.key(KEY_BACK)
                await dev.settle(timeout=2.0)
            if attempt == PROFILE_TAP_TRIES:
                raise StepFailed(f"profile: the profile tap opened {wrong} instead, {attempt} times")
            # Back can leave the feed a little scrolled, its top bar over the composer: Home first.
            await self.discard_until_feed()
        # A Page's profile has an All tab, with its Posts list further down. It reopens on the tab it
        # last showed, so the tab is tapped. Promo cards can push the tabs below the screen.
        timeout = 15
        for _ in range(PROFILE_TAB_SCROLLS):
            try:
                _, tab, _ = await self.wait_for("profile", ["Posts", "All"], exact=True, timeout=timeout,
                                                evidence=False)
                await dev.tap(tab)
                return
            except StepFailed:
                await self.scroll(dev.pt(540, 1700), dev.pt(540, 1150))
                await dev.settle(timeout=2.0)
                timeout = 3
        _, tab, _ = await self.wait_for("profile", ["Posts", "All"], exact=True, timeout=timeout)
        await dev.tap(tab)

    async def find_and_comment(self, item: PostItem, published_at: float, waits=None,
                               max_age_minutes: float | None = None) -> int:
        """Profile, Posts, newest first: this job's post, then its comment icon. A few looks with a
        refresh between them (more for a reel); then the comment is skipped. Returns the look that
        found the post. Each look that misses keeps a screenshot of the profile it saw."""
        dev = self.device
        waits = comment_waits(item.kind) if waits is None else waits
        last_miss = None
        for attempt, _ in enumerate(waits, 1):
            remaining = published_at + sum(waits[:attempt]) - dev.now()
            if remaining > 0:
                await dev.sleep(remaining)
            allowed = max_age_minutes if max_age_minutes is not None else NEW_POST_MAX_MINUTES
            next_wait = waits[attempt] if attempt < len(waits) else None
            try:
                if attempt == 1 or not await self.on_profile():
                    # First look, or the last one left the profile (a missed tap can open a create screen
                    # or leave the home feed showing): open it again rather than scan another screen.
                    if attempt > 1:
                        self.log(event="reopen profile", attempt=attempt)
                    await self.open_feed()
                    await self.open_profile()
                else:
                    await self.refresh_profile()
            except StepFailed as unopened:
                # Not this look's post missing, only the profile not reached: the next look opens it again.
                last_miss = str(unopened)[:200]
                self.log(event="retry", step="open profile", attempt=attempt, reason=last_miss, next_wait=next_wait)
                continue
            # The tab tap can leave the list with this job's post header under the sticky tab bar, so
            # each look scans down from the top of the profile, where that header passes in view.
            await self.profile_to_top()
            if await self.scan_profile(item, allowed, attempt):
                await self.write_comment(item)
                return attempt
            last_miss = None
            self.log(event="retry", step="find post", attempt=attempt, next_wait=next_wait)
        if last_miss:
            raise StepFailed(f"comment skipped: the profile did not open on the last look; {last_miss}")
        raise StepFailed(f"comment skipped: the post did not show on the profile after {len(waits)} looks")

    async def on_profile(self) -> bool:
        """The account's own profile shows: its tabs or its followers/friends line, and not the feed's
        "facebook" bar. A look that ended elsewhere (a create screen, the home feed) is not scanned."""
        lines = await self.device.read()
        if match(lines, "facebook", exact=True) or match(lines, "Gallery"):
            return False
        return bool(match(lines, "Posts", exact=True) or match(lines, "All", exact=True)
                    or match(lines, "followers") or match(lines, "friends"))

    async def profile_to_top(self) -> bool:
        """Swipes up until the profile's "followers" line shows. False when it never did."""
        dev = self.device
        for _ in range(PROFILE_TOP_SCROLLS):
            lines = await dev.read()
            if match(lines, "followers") and not match(lines, "facebook", exact=True):
                return True
            await self.scroll(dev.pt(540, 800), dev.pt(540, 2000))
        self.log(event="profile_top", found=False)
        return False

    async def refresh_profile(self) -> None:
        """Back to the top of the profile, pull to refresh, then the Posts tab again."""
        dev = self.device
        await self.profile_to_top()
        await self.scroll(dev.pt(540, 900), dev.pt(540, 2000))
        await dev.sleep(3)
        tab = match(await dev.read(), "Posts", exact=True)
        if tab:
            await dev.tap(tab)

    async def scan_profile(self, item: PostItem, allowed_minutes: float, attempt: int) -> bool:
        """Scrolls the Posts list for the newest post by its time label. Pinned posts are skipped; a post
        older than this job ends the look, since this job's post would be above it. The list can still
        sit with only this job's media in view, its header under the sticky tab bar and the older post
        below, so before an older post ends the look the list is dragged back down by that older post's
        time label (a drag that starts on autoplaying video may not scroll). Each back-up logs how far
        the label moved. Scans down with slow drags that do not fling past a header. True once the
        comment sheet is open."""
        dev = self.device
        backed_up = 0
        dragged: Line | None = None  # the older post's label a back-up dragged, to measure the move
        for scroll in range(12):
            await dev.settle(timeout=2.0)  # autoplaying video keeps the profile from ever being still
            lines = await dev.read()
            if dragged is not None:
                moved = self._label_moved(dragged, lines)
                self.log(event="back_up_moved", step="find post", attempt=attempt, back_up=backed_up,
                         moved=moved, scrolled=moved == "gone" or moved >= PROFILE_MOVED_MIN)
                dragged = None
            pinned = [line.point[1] for line in lines if line.text.strip().casefold().startswith("pinned")]

            def near_pin(y: float) -> bool:
                return any(0 <= y - pin < dev.pt(0, 160)[1] for pin in pinned)

            # This job's post is the newest unpinned one: its time label is within the allowed age. The
            # caption is not read; its preview can be cut off or hidden under the profile's tab bar.
            fresh = [line.point for line in lines if (screen.post_age_minutes(line.text) or 0) <= allowed_minutes
                     and screen.post_age_minutes(line.text) is not None and not near_pin(line.point[1])]
            anchor = fresh[0] if fresh else None
            if anchor:
                icon = dev.find_icon("comment", below=anchor[1])
                if icon:
                    self.log(event="found", step="post", attempt=attempt, scroll=scroll)
                    dev.snapshot("comment_post_found")
                    await dev.tap(icon)
                    try:
                        await self.wait_for("comment sheet", COMMENT_BOX, timeout=6)
                        return True
                    except StepFailed:
                        self.log(event="retry", step="comment icon")
                        continue
            else:
                for line in lines:
                    age = screen.post_age_minutes(line.text)
                    if age is not None and age > allowed_minutes and not near_pin(line.point[1]):
                        if backed_up < PROFILE_BACK_UP_SCROLLS:
                            backed_up += 1
                            self.log(event="back_up", step="find post", attempt=attempt, reason=f"a post {age} min old")
                            if backed_up == 1:
                                dev.snapshot(f"comment_look_{attempt}_before_back_up")
                            await self._drag_down_from(line)
                            dragged = line
                            break
                        self.log(event="stop", step="find post", attempt=attempt,
                                 reason=f"reached a post {age} min old")
                        dev.snapshot(f"comment_look_{attempt}_post_not_there")
                        return False
                else:
                    await self._scan_down()
                continue
            await self._scan_down()
        dev.snapshot(f"comment_look_{attempt}_post_not_there")
        return False

    async def _scan_down(self) -> None:
        dev = self.device
        await self.scroll(dev.pt(540, 1600), dev.pt(540, 1150), duration=PROFILE_SCAN_SWIPE_S)

    async def _drag_down_from(self, label: Line) -> None:
        """Drags the list down about 600 px, starting on an older post's time label: text, not video.
        A label low on the screen leaves no room for the drag, so it then starts higher up instead."""
        dev = self.device
        x = dev.pt(540, 0)[0]
        y = min(label.point[1], dev.pt(0, 1400)[1])
        await self.scroll((x, y), (x, y + dev.pt(0, 600)[1]), duration=PROFILE_SCAN_SWIPE_S)

    def _label_moved(self, label: Line, lines: list[Line]) -> int | str:
        """How far (1080-wide px) a dragged time label moved down; "gone" when it left the screen.
        Below PROFILE_MOVED_MIN the drag did not scroll the list."""
        same = [line.point[1] for line in lines if line.text.strip() == label.text.strip()]
        if not same:
            return "gone"
        moved = min(same, key=lambda y: abs(y - label.point[1])) - label.point[1]
        return round(moved * 1080 / self.device.pt(1080, 0)[0])

    async def write_comment(self, item: PostItem) -> None:
        dev = self.device
        _, box, _ = await self.wait_for("comment box", COMMENT_BOX, timeout=10)
        await dev.tap(box)
        await self._enter_text(item.comment, "comment")
        key = screen.caption_key(item.comment)
        if key and not match(await dev.read(), key[:16]):
            # Something else is in the box (or nothing): never send it. Clear by leaving the sheet.
            raise StepFailed("comment: the box does not show the comment after paste; not sent")
        send = None
        for _ in range(5):
            send = dev.find_icon("send")
            if send:
                break
            await dev.sleep(0.6)
        if not send:
            raise StepFailed("comment: the send arrow was not found")
        self.log(event="about_to_comment")
        await dev.tap(send)
        if key:
            try:
                await self.wait_for("comment shown", [key[:16]], timeout=15, evidence=False)
            except StepFailed as unseen:
                dev.snapshot("comment_not_confirmed")
                raise CommentUncertain(str(unseen)) from unseen
        else:
            await dev.sleep(3)
        dev.snapshot("comment_posted")
        for _ in range(3):
            await dev.key(KEY_BACK)
