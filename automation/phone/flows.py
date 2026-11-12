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
# The comment box's placeholder: "Write a comment..." on posts, "Add a comment" in the reel player, and
# "Comment as NAME" under the "Get the ball rolling" sheet of a post with no comments yet.
COMMENT_BOX = ["Write a comment", "Add a comment", "Comment as"]
# Swipes down the profile looking for its tabs; promo cards can push them about three swipes below the top.
PROFILE_TAB_SCROLLS = 6
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
    caption_tail: str = field(init=False)

    def __post_init__(self):
        if self.kind not in ("photo", "reel"):
            raise ValueError("kind must be photo or reel")
        self.caption_key = screen.caption_key(self.caption)
        self.caption_tail = screen.caption_tail_key(self.caption)


class Flows:
    def __init__(self, device, log: Callable[..., None]):
        self.device = device
        self.log = log

    # --- waiting ------------------------------------------------------------------------------
    async def wait_for(self, step: str, any_of: list[str], timeout: float = 20.0, exact: bool = False,
                       handle_interruptions: bool = True, evidence: bool = True,
                       discard_old: bool = False) -> tuple[str, Point, list[Line]]:
        """Until one of the texts shows; known pop-ups are closed meanwhile. A caller that recovers from a
        miss passes `evidence=False`, so only real failures leave a fail_ screenshot. `discard_old` is for a
        step before this job has made anything: a prompt about an earlier job's post is answered with discard."""
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
                point = match(lines, wanted, exact)
                if point:
                    return wanted, point, lines
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
        dev = self.device
        # Home twice closes an open app drawer; then open it, scrolled to the top when needed.
        await dev.key(KEY_HOME)
        await dev.key(KEY_HOME)
        await dev.swipe(dev.pt(540, 1900), dev.pt(540, 600))
        label = None
        for attempt in range(3):
            lines = await dev.read()
            label = match(lines, "Facebook", exact=True)
            if label:
                break
            self.log(event="retry", step="app drawer", attempt=attempt + 1, screen=texts(lines, 8))
            await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
        if not label:
            dev.snapshot(f"fail_drawer_{int(time.time())}")
            raise StepFailed("app drawer: the Facebook icon was not found")
        await dev.tap((label[0], label[1] - dev.pt(0, 110)[1]))  # the icon sits above its label
        await self.discard_until_feed()

    async def discard_until_feed(self) -> None:
        """Back to the feed without publishing anything: a draft left by an earlier job is discarded."""
        dev = self.device
        home_attempted = False
        for _ in range(8):
            try:
                found, _, lines = await self.wait_for("open facebook", [
                    FEED_PROMPT, "New post", "Create post", "New reel", "Post settings", "Reel settings", "Discard changes",
                    "Discard post", "Discard video", "Discard draft", screen.LEFT_UNFINISHED, "facebook"], timeout=12, evidence=False)
            except StepFailed as unknown:
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
                home = dev.find_icon("home_tab") if not home_attempted else None
                if home:  # Jump to the top first; if it does not work, fall back to bounded swipes.
                    home_attempted = True
                    self.log(event="feed", action="tap Home")
                    await dev.tap(home)
                else:
                    self.log(event="feed", action="scroll to top")  # reopened part-way down the feed
                    await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
                    await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
                await dev.settle(timeout=2.0)  # read the feed where it stopped, not while it still moves
                continue
            self.log(event="leftover", screen=found, action="back")
            await dev.key(KEY_BACK)
            await dev.sleep(0.8)
        lines = await dev.read()  # the last pass may have just reached the top
        if match(lines, "facebook", exact=True) and match(lines, FEED_PROMPT):
            return
        dev.snapshot(f"fail_feed_{int(time.time())}")
        raise StepFailed(f"open facebook: could not reach the feed; screen shows {texts(lines)}")

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
        plus = dev.find_icon("create")
        if not plus:
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
        await dev.tap((point[0] + offset[0], point[1] + offset[1]))
        # Picking can open the composer at once. Its own Next leads past the caption, so the prompts come first.
        found, nxt, _ = await self.wait_for("picker selection", [VIDEO_PROMPT, PHOTO_PROMPT, FEED_PROMPT, "Next"], timeout=10)
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
        _, _, lines = await self.wait_for("profile", ["Create reel", "All", "Posts"], timeout=15)
        button = match(lines, "Create reel")
        for _ in range(4):  # the profile reopens where it was last scrolled to
            if button:
                break
            await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
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
            found, anchor, lines = await self.wait_for("reel picker", list(offsets), exact=True, timeout=15)
            if not await self._discard_unfinished_reel(lines):
                offset = offsets[found]
                await dev.tap((anchor[0] + offset[0], anchor[1] + offset[1]))
                found, nxt, lines = await self.wait_for("reel editor", [screen.LEFT_UNFINISHED, "Next"], timeout=30)  # OCR reads its arrow too
                if found == "Next":
                    break
                await self._discard_unfinished_reel(lines)
        else:
            raise StepFailed(f"reel picker: an unfinished reel keeps coming back; {texts(lines)}")
        # Next can be drawn before the editor takes taps (the clip still loading, its "Add audio" hint
        # sliding in), so a tap may leave the editor up. Next is tapped again while the editor stays.
        for attempt in range(1, 4):
            await dev.tap(nxt)
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
                again = match(after, "Next")
                if not again:
                    dev.snapshot(f"fail_composer_with_media_{int(time.time())}")
                    raise
                self.log(event="retry", step="reel editor next", attempt=attempt + 1)
                nxt = again

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
            # Facebook shows a Gallery tile (tapped above its label), a list with a Photo/video row, or a bottom
            # bar of icons where only GIF has a label and the photo icon comes first.
            try:
                found, entry, _ = await self.wait_for("composer", ["Gallery", "Photo/video", "GIF"], exact=True,
                                                      evidence=last_try, discard_old=True)
                break
            except StepFailed as missed:  # a tap while the feed still moves only stops it
                if last_try:
                    raise
                self.log(event="retry", step="composer", detail=str(missed)[:200])
                await self.discard_until_feed()
        if found == "GIF":
            await dev.tap((dev.pt(90, 0)[0], entry[1]))
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
        dev = self.device
        _, field_point, _ = await self.wait_for("caption field", [FEED_PROMPT, PHOTO_PROMPT, "Describe your reel",
                                                                  "Add a description"], timeout=15)
        await dev.tap(field_point)
        await self._enter_text(item.caption, "caption")
        await dev.key(KEY_ESCAPE)
        lines = await dev.read()
        if item.caption_key and not match(lines, item.caption_key) and not (
                item.caption_tail and screen.shown_across_lines(lines, item.caption_tail)):
            raise StepFailed(f"caption: not shown after paste; screen shows {texts(lines, 10)}")
        if not item.caption_key and any(match(lines, prompt) for prompt in (FEED_PROMPT, PHOTO_PROMPT, "Describe your reel")):
            raise StepFailed("caption: the field is still empty after paste")
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
                if not screen.button_disabled(dev.image(), point):
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
        _, point, _ = await self.wait_for("feed for profile", [FEED_PROMPT])
        await self.device.tap((self.device.pt(77, 0)[0], point[1]))  # profile picture left of the box

    async def open_profile(self) -> None:
        await self._tap_profile_picture()
        # A Page's profile has an All tab, with its Posts list further down. It reopens on the tab it
        # last showed, so the tab is tapped. Promo cards can push the tabs below the screen.
        dev = self.device
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
        for attempt, _ in enumerate(waits, 1):
            remaining = published_at + sum(waits[:attempt]) - dev.now()
            if remaining > 0:
                await dev.sleep(remaining)
            # Facebook may date the post from the publish tap, before the upload finished.
            allowed = max_age_minutes if max_age_minutes is not None else (dev.now() - published_at) / 60 + 4
            if attempt == 1:
                await self.open_feed()
                await self.open_profile()
            else:
                await self.refresh_profile()
            if await self.scan_profile(item, allowed, attempt):
                await self.write_comment(item)
                return attempt
            self.log(event="retry", step="find post", attempt=attempt,
                     next_wait=waits[attempt] if attempt < len(waits) else None)
        raise StepFailed(f"comment skipped: the post did not show on the profile after {len(waits)} looks")

    async def refresh_profile(self) -> None:
        """Back to the top of the profile, pull to refresh, then the Posts tab again."""
        dev = self.device
        for _ in range(6):
            if match(await dev.read(), "followers"):
                break
            await self.scroll(dev.pt(540, 800), dev.pt(540, 2000))
        await self.scroll(dev.pt(540, 900), dev.pt(540, 2000))
        await dev.sleep(3)
        tab = match(await dev.read(), "Posts", exact=True)
        if tab:
            await dev.tap(tab)

    async def scan_profile(self, item: PostItem, allowed_minutes: float, attempt: int) -> bool:
        """Scrolls the Posts list. Pinned posts are skipped; a post older than this job ends the look,
        since this job's post would be above it. True once the comment sheet is open."""
        dev = self.device
        for scroll in range(12):
            await dev.settle(timeout=2.0)  # autoplaying video keeps the profile from ever being still
            lines = await dev.read()
            pinned = [line.point[1] for line in lines if line.text.strip().casefold().startswith("pinned")]

            def near_pin(y: float) -> bool:
                return any(0 <= y - pin < dev.pt(0, 160)[1] for pin in pinned)

            anchor = match(lines, item.caption_key) if item.caption_key else None
            if not item.caption_key:  # nothing OCR can read in the caption: the newest unpinned post
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
                        self.log(event="stop", step="find post", attempt=attempt,
                                 reason=f"reached a post {age} min old")
                        dev.snapshot(f"comment_look_{attempt}_post_not_there")
                        return False
            await self.scroll(dev.pt(540, 1700), dev.pt(540, 1150))
        dev.snapshot(f"comment_look_{attempt}_post_not_there")
        return False

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
