"""One phone account's screen and input: the only phone code that talks to the phone.

Frames come from a scrcpy stream (H.264 decoded with PyAV; a still screen sends no frames, so the last
one is kept). Touches and keys go over scrcpy's control socket, text through the companion app's
clipboard in the account's own Android user, and media through the companion app's gallery writer.
Facebook is only ever operated through the screen: no intents are sent to it.
"""

from __future__ import annotations

import asyncio
import random
import struct
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from backend.services import android_live
from backend.services.adb_client import AdbClient, AdbError
from backend.services.android_agent import AGENT_AUTHORITY, agent_call

from phone.screen import Line, Point, find_template, lines_from_ocr, load_templates

FACEBOOK = "com.facebook.katana"
KEY_HOME, KEY_BACK, KEY_ESCAPE, KEY_PASTE = 3, 4, 111, 279
AGENT_TIMEOUT_S = 15.0
MEDIA_SAVE_TIMEOUT_S = 60.0
# Facebook's own login approval screen: it can open over the feed at any moment, and input must never land on it.
APPROVAL_SCREEN = "approvefromanotherdevice"
APPROVAL_WAIT_S = 15.0
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}


class PhoneError(RuntimeError):
    pass


class ApprovalScreenUp(PhoneError):
    """Facebook's login approval screen stayed up: a person looks at it; nothing is tapped."""


def media_mime(path: str) -> str:
    mime = MIME.get(Path(path).suffix.lower())
    if not mime:
        raise PhoneError(f"Unsupported media type: {Path(path).name} (use JPEG, PNG or MP4)")
    return mime


class PhoneDevice:
    """Async primitives for the flows. Points are in stream pixels (576 wide on a 1080 px phone);
    `pt` converts full-resolution coordinates."""

    def __init__(self, serial: str, user_id: int, evidence_dir: Path, log: Callable[..., None],
                 client: AdbClient | None = None, reader=None):
        self.serial, self.user_id = serial, int(user_id)
        self.evidence_dir = evidence_dir
        self.log = log
        self.client = client or AdbClient()
        self.reader = reader
        self.templates = load_templates()
        self.session = None
        self.width = self.height = 0
        self._codec = None
        self._config = b""
        self._frame: np.ndarray | None = None
        self.frame_count = 0
        self._lock = threading.Lock()
        self._tasks: list[asyncio.Task] = []

    # --- session ------------------------------------------------------------------------------
    async def open(self) -> None:
        import av
        if self.reader is None:
            from engine.ocr_reader import create_reader
            self.reader = await asyncio.to_thread(create_reader, requested_device="cpu")
        await asyncio.to_thread(self._wake_into_user)
        self.session = await android_live.open_session(self.serial, self.client)
        self.width, self.height = self.session.size["width"], self.session.size["height"]
        self._codec = av.CodecContext.create("h264", "r")
        self._tasks = [asyncio.create_task(self._pump()), asyncio.create_task(self.session.drain_device_messages())]
        for _ in range(50):  # the first frame arrives within about a second
            if self._frame is not None:
                return
            await asyncio.sleep(0.1)
        raise PhoneError("The phone's screen stream sent no picture")

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self.session:
            await android_live.close_session(self.session)
            self.session = None

    def _wake_into_user(self) -> None:
        """The account's Android user on screen, awake and past a swipe lock screen."""
        if self.client.current_user(self.serial) != self.user_id:
            self.client.switch_user(self.serial, self.user_id)
            time.sleep(4)
        if not self.client.screen_awake(self.serial):
            self.client.wake_screen(self.serial)
            time.sleep(0.8)
        if self.client.lock_screen_showing(self.serial):
            self.client.dismiss_lock_screen(self.serial)
            time.sleep(1)
            if self.client.lock_screen_showing(self.serial):
                raise PhoneError("The phone is locked with a PIN or pattern; remove the lock for this user")

    async def _pump(self) -> None:
        while True:
            self._feed(await self.session.read_packet())

    def _feed(self, packet: bytes) -> None:
        import av
        pts, _ = struct.unpack(">QI", packet[:12])
        if pts & (1 << 63):  # codec config: prepended to the next frame
            self._config = packet[12:]
            return
        data, self._config = self._config + packet[12:], b""
        for frame in self._codec.decode(av.Packet(data)):
            image = frame.to_ndarray(format="bgr24")
            with self._lock:
                self._frame, self.frame_count = image, self.frame_count + 1

    # --- seeing -------------------------------------------------------------------------------
    def image(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    async def read(self) -> list[Line]:
        image = self.image()
        if image is None:
            return []
        return await asyncio.to_thread(lambda: lines_from_ocr(self.reader.readtext(image[:, :, ::-1])))

    def find_icon(self, name: str, below: float | None = None) -> Point | None:
        image = self.image()
        return None if image is None else find_template(image, self.templates[name], below)

    def snapshot(self, name: str) -> str | None:
        image = self.image()
        if image is None:
            return None
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self.evidence_dir / f"{name}.png"
        cv2.imwrite(str(path), image)
        return str(path)

    def focus(self) -> str | None:
        try:
            return self.client.focused_window(self.serial)
        except AdbError:
            return None

    def keyboard_shown(self) -> bool:
        """The on-screen keyboard is up. A swipe over it would gesture-type words into the focused field."""
        try:
            return "mInputShown=true" in self.client.shell(self.serial, "dumpsys input_method | grep -m1 mInputShown; true")
        except AdbError:
            return True  # unknown: treat as shown, so no swipe lands on a keyboard

    def pt(self, x_full: float, y_full: float) -> Point:
        scale = self.width / 1080
        return x_full * scale, y_full * scale

    # --- time ---------------------------------------------------------------------------------
    @staticmethod
    def now() -> float:
        return time.monotonic()

    @staticmethod
    async def sleep(seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def settle(self, quiet: float = 0.6, timeout: float = 5.0) -> None:
        """Until the screen stops changing. A tap while the feed still coasts after a swipe only
        stops the scroll, so taps after a swipe wait for this."""
        deadline = time.monotonic() + timeout
        last, since = self.frame_count, time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            if self.frame_count != last:
                last, since = self.frame_count, time.monotonic()
            elif time.monotonic() - since >= quiet:
                return

    # --- acting -------------------------------------------------------------------------------
    def _approval_up(self) -> bool:
        return APPROVAL_SCREEN in (self.focus() or "")

    async def _clear_of_approval(self) -> None:
        """Right before any input: waits while the login approval screen has focus, keeping its frame."""
        if not await asyncio.to_thread(self._approval_up):
            return
        self.log(event="approval_screen", action="waiting, no input")
        self.snapshot(f"approval_screen_{int(time.time())}")
        deadline = time.monotonic() + APPROVAL_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if not await asyncio.to_thread(self._approval_up):
                self.log(event="approval_screen", action="closed by itself")
                return
        self.snapshot(f"approval_screen_stayed_{int(time.time())}")
        raise ApprovalScreenUp("Facebook's login approval screen stayed open; check the account on the phone")

    async def _touch(self, action: int, x: float, y: float) -> None:
        await self.session.send({"type": "touch", "action": action, "x": int(x), "y": int(y),
                                 "width": self.width, "height": self.height})

    async def tap(self, point: Point, jitter: float = 4) -> None:
        x, y = point[0] + random.uniform(-jitter, jitter), point[1] + random.uniform(-jitter, jitter)
        await asyncio.sleep(random.uniform(0.25, 0.7))
        await self._clear_of_approval()
        await self._touch(android_live.ACTION_DOWN, x, y)
        await asyncio.sleep(random.uniform(0.05, 0.12))
        await self._touch(android_live.ACTION_UP, x, y)
        await asyncio.sleep(random.uniform(0.5, 1.0))

    async def swipe(self, start: Point, end: Point, duration: float = 0.35) -> None:
        """An eased finger path with a little sideways wobble, then wait for the scroll to stop."""
        steps = 14
        await self._clear_of_approval()
        await self._touch(android_live.ACTION_DOWN, *start)
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)
            await self._touch(android_live.ACTION_MOVE, start[0] + (end[0] - start[0]) * ease + random.uniform(-2, 2),
                              start[1] + (end[1] - start[1]) * ease)
            await asyncio.sleep(duration / steps)
        await self._touch(android_live.ACTION_UP, *end)
        await self.settle()

    async def key(self, code: int) -> None:
        await self._clear_of_approval()
        await self.session.send({"type": "key", "keycode": code})
        await asyncio.sleep(random.uniform(0.5, 0.9))

    async def paste(self, text: str) -> None:
        """Through the companion app's clipboard in this user (scrcpy's would land in user 0), so any
        language and emoji arrive intact. The clipboard is cleared again right after."""
        await asyncio.to_thread(agent_call, self.client, self.serial, self.user_id, "set_clipboard",
                                AGENT_TIMEOUT_S, {"text": text})
        try:
            await self.key(KEY_PASTE)
        finally:
            await asyncio.to_thread(agent_call, self.client, self.serial, self.user_id, "clear_clipboard",
                                    AGENT_TIMEOUT_S)

    # --- media --------------------------------------------------------------------------------
    def put_media(self, path: str, name: str) -> str:
        """The companion app saves the file to this user's gallery as its own item, like
        any app saving a download. Returns the gallery item's content URI."""
        mime = media_mime(path)
        uri = f"content://{AGENT_AUTHORITY}/media?name={name}&mime={mime}"
        self.client.write_content(self.serial, self.user_id, uri, path)
        deadline = time.monotonic() + MEDIA_SAVE_TIMEOUT_S
        while time.monotonic() < deadline:
            status = agent_call(self.client, self.serial, self.user_id, "media_status", AGENT_TIMEOUT_S, {"name": name})
            if status.get("state") == "saved":
                return status["uri"]
            if status.get("state") == "failed":
                raise PhoneError(f"The companion app could not save the media: {status.get('error')}")
            time.sleep(0.5)
        raise PhoneError("The companion app did not finish saving the media")

    def delete_media(self, uri: str) -> None:
        if uri.startswith("content://media/"):
            self.client.shell(self.serial, "content", "delete", "--user", str(self.user_id), "--uri", uri)
        else:
            agent_call(self.client, self.serial, self.user_id, "delete_media", AGENT_TIMEOUT_S, {"uri": uri})
