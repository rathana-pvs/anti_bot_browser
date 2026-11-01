"""Facebook flows on a phone, done the way a person would: launcher icon, feed, Gallery, profile picture.

Hybrid route: each step tries on-screen taps first. When that fails before the publish tap, the step
may use its shortcut (an intent from the shell) once, after the screen is back on the feed, and the
fallback is logged with its frame. Account problems (logged out, checkpoint) never fall back, and
nothing after the publish tap is ever retried: an unconfirmed publish is uncertain, not failed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from phone import screen
from phone.device import KEY_BACK, KEY_ESCAPE, KEY_HOME
from phone.screen import Line, Point, match, texts

FEED_PROMPT = "What's on your mind?"
# Seconds before each of the three looks for a new post on the profile; a reel can take a while.
COMMENT_WAITS = (30, 25, 25)
PUBLISH_CONFIRM_TIMEOUT_S = 180
# The comment box's placeholder: "Write a comment..." on posts, "Add a comment" in the reel player, and
# "Comment as NAME" under the "Get the ball rolling" sheet of a post with no comments yet.
COMMENT_BOX = ["Write a comment", "Add a comment", "Comment as"]


class StepFailed(Exception):
    """A step did not reach the screen it expects. Before the publish tap a job can be retried."""


class AccountBlocked(Exception):
    """Logged out or stopped by Facebook: the account needs review; no shortcut is tried."""


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
    def __init__(self, device, log: Callable[..., None], human_only: bool = False):
        self.device = device
        self.log = log
        self.human_only = human_only
        self.fallbacks: list[dict] = []
        self._fallen_back: set[str] = set()

    # --- waiting ------------------------------------------------------------------------------
    async def wait_for(self, step: str, any_of: list[str], timeout: float = 20.0, exact: bool = False,
                       handle_interruptions: bool = True) -> tuple[str, Point, list[Line]]:
        """Until one of the texts shows; known pop-ups are closed meanwhile."""
        dev = self.device
        deadline = dev.now() + timeout
        lines: list[Line] = []
        while dev.now() < deadline:
            lines = await dev.read()
            for wanted in any_of:
                point = match(lines, wanted, exact)
                if point:
                    return wanted, point, lines
            self.check_account(lines)
            if handle_interruptions and await self.close_interruption(lines):
                continue
            await dev.sleep(0.6)
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

    # --- hybrid route -------------------------------------------------------------------------
    async def hybrid(self, step: str, human: Callable[[], Awaitable], shortcut: Callable[[], Awaitable] | None):
        try:
            return await human()
        except StepFailed as failure:
            if self.human_only or shortcut is None or step in self._fallen_back:
                raise
            self._fallen_back.add(step)
            frame = self.device.snapshot(f"fallback_{step}_{int(time.time())}")
            record = {"step": step, "reason": str(failure)[:300], "frame": frame}
            self.log(event="fallback", **record)
            try:
                result = await shortcut()
            except StepFailed as again:
                self.fallbacks.append({**record, "worked": False})
                self.log(event="fallback_failed", step=step, reason=str(again)[:300])
                raise
            self.fallbacks.append({**record, "worked": True})
            return result

    async def scroll(self, start: Point, end: Point) -> None:
        """A swipe that never lands on the keyboard: with the keyboard up (a comment box focused) it
        would gesture-type words into the box. The keyboard is hidden and its sheet closed first."""
        dev = self.device
        if await asyncio.to_thread(dev.keyboard_shown):
            self.log(event="keyboard", action="hide and close before scrolling")
            await dev.key(KEY_ESCAPE)
            if await asyncio.to_thread(dev.keyboard_shown):
                raise StepFailed("keyboard: still shown after Escape; not scrolling over it")
            await dev.key(KEY_BACK)
        await dev.swipe(start, end)

    # --- feed ---------------------------------------------------------------------------------
    async def open_feed(self) -> None:
        """Facebook on its feed, scrolled to the top, with nothing half-made left open."""
        async def by_intent():
            await asyncio.to_thread(self.device.launch_facebook)
            await self.discard_until_feed()
        await self.hybrid("open_facebook", self._open_from_launcher, by_intent)

    async def _open_from_launcher(self) -> None:
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
        for _ in range(8):
            try:
                found, _, lines = await self.wait_for("open facebook", [
                    FEED_PROMPT, "New post", "Post settings", "Reel settings", "Discard changes", "Discard post",
                    "facebook"], timeout=12)
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
                self.log(event="feed", action="scroll to top")  # reopened part-way down the feed
                await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
                await self.scroll(dev.pt(540, 700), dev.pt(540, 1900))
                continue
            self.log(event="leftover", screen=found, action="back")
            await dev.key(KEY_BACK)
            await dev.sleep(0.8)
        raise StepFailed("open facebook: could not reach the feed")

    # --- composing ----------------------------------------------------------------------------
    async def start_post(self, item: PostItem, media_uri: str, mime: str) -> None:
        """The composer with this job's media and caption."""
        async def by_share():
            await self.discard_until_feed()
            await asyncio.to_thread(self.device.share_to_composer, media_uri, mime)
            await self.wait_for("composer with media", ["Next"], exact=True, timeout=30)
        await self.hybrid("start_post", self._compose_from_gallery, by_share)
        await self.write_caption(item)

    async def _compose_from_gallery(self) -> None:
        dev = self.device
        _, point, _ = await self.wait_for("feed", [FEED_PROMPT])
        await dev.tap(point)
        _, gallery, _ = await self.wait_for("composer", ["Gallery"], exact=True)
        await dev.tap((gallery[0], gallery[1] - dev.pt(0, 35)[1]))
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
        # The job's media was just saved, so it is the newest: the first cell, its circle top-right.
        offset = dev.pt(168, 110)
        await dev.tap((point[0] + offset[0], point[1] + offset[1]))
        _, nxt, _ = await self.wait_for("picker selection", ["Next"], exact=True, timeout=10)
        await dev.tap(nxt)
        await self.wait_for("composer with media", [FEED_PROMPT, "Describe your reel", "Add a description"],
                            timeout=30)

    async def write_caption(self, item: PostItem) -> None:
        dev = self.device
        _, field_point, _ = await self.wait_for("caption field", [FEED_PROMPT, "Describe your reel",
                                                                  "Add a description"], timeout=15)
        await dev.tap(field_point)
        await dev.paste(item.caption)
        await dev.key(KEY_ESCAPE)
        lines = await dev.read()
        if item.caption_key and not match(lines, item.caption_key):
            raise StepFailed(f"caption: not shown after paste; screen shows {texts(lines, 10)}")
        if not item.caption_key and any(match(lines, prompt) for prompt in (FEED_PROMPT, "Describe your reel")):
            raise StepFailed("caption: the field is still empty after paste")

    async def publish(self, before_tap: Callable[[], None]) -> float:
        """Next, then SHARE (photo) or Share now (reel). `before_tap` records the attempt as about to
        publish; from the tap on, the outcome is uncertain until confirmed. Returns the tap time."""
        dev = self.device
        _, nxt, _ = await self.wait_for("composer next", ["Next"], exact=True)
        await dev.tap(nxt)
        found, _, _ = await self.wait_for("settings", ["Post settings", "Reel settings"], timeout=20)
        button = "SHARE" if found == "Post settings" else "Share now"
        # The title shows before the button has finished drawing.
        _, point, _ = await self.wait_for(f"settings {button}", [button], exact=True, timeout=10,
                                          handle_interruptions=False)
        dev.snapshot("before_publish")
        before_tap()
        self.log(event="about_to_publish", screen=found)
        tapped = dev.now()
        await dev.tap(point)
        return tapped

    async def wait_published(self, item: PostItem, tapped: float) -> float:
        """A photo shows on the feed with its caption and "Just now"; a reel is confirmed once the
        "Posting" bar has come and gone (it is then found on the profile for the comment)."""
        dev = self.device
        seen_posting = False
        while dev.now() < tapped + PUBLISH_CONFIRM_TIMEOUT_S:
            lines = await dev.read()
            if await self.close_interruption(lines):
                continue
            if match(lines, "Posting"):
                seen_posting = True
            elif match(lines, FEED_PROMPT):
                if item.kind == "photo" and match(lines, "Just now") and (
                        not item.caption_key or match(lines, item.caption_key)):
                    return dev.now() - tapped
                if item.kind == "reel" and (seen_posting or dev.now() - tapped > 25):
                    return dev.now() - tapped
            await dev.sleep(1.5)
        dev.snapshot("publish_unconfirmed")
        raise PublishUncertain(f"no confirmation within {PUBLISH_CONFIRM_TIMEOUT_S} s after the publish tap")

    # --- profile and first comment ------------------------------------------------------------
    async def open_profile(self) -> None:
        async def by_link():
            await self.discard_until_feed()
            await asyncio.to_thread(self.device.open_profile_link)
            await self.wait_for("profile", ["Posts"], exact=True, timeout=15)

        async def by_picture():
            _, point, _ = await self.wait_for("feed for profile", [FEED_PROMPT])
            await self.device.tap((self.device.pt(77, 0)[0], point[1]))  # profile picture left of the box
            await self.wait_for("profile", ["Posts"], exact=True, timeout=15)
        await self.hybrid("open_profile", by_picture, by_link)

    async def find_and_comment(self, item: PostItem, published_at: float, waits=COMMENT_WAITS,
                               max_age_minutes: float | None = None) -> int:
        """Profile, Posts, newest first: this job's post, then its comment icon. Three looks with a
        refresh between them; then the comment is skipped. Returns the look that found the post."""
        dev = self.device
        for attempt, _ in enumerate(waits, 1):
            remaining = published_at + sum(waits[:attempt]) - dev.now()
            if remaining > 0:
                await dev.sleep(remaining)
            allowed = max_age_minutes if max_age_minutes is not None else (dev.now() - published_at) / 60 + 2
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
        dev.snapshot("comment_skipped")
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
                        return False
            await self.scroll(dev.pt(540, 1700), dev.pt(540, 1150))
        return False

    async def write_comment(self, item: PostItem) -> None:
        dev = self.device
        _, box, _ = await self.wait_for("comment box", COMMENT_BOX, timeout=10)
        await dev.tap(box)
        await dev.paste(item.comment)
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
                await self.wait_for("comment shown", [key[:16]], timeout=15)
            except StepFailed as unseen:
                raise CommentUncertain(str(unseen)) from unseen
        else:
            await dev.sleep(3)
        for _ in range(3):
            await dev.key(KEY_BACK)
