"""scrcpy live view for physical Android phones.

The pinned scrcpy-server is pushed to the phone, its abstract socket is
forwarded to a loopback port, and the video and control sockets are bridged to
one WebSocket. Only the foreground Android user is visible, so callers switch
the phone to the profile's user first. Nothing here leaves the local machine.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import struct
from dataclasses import dataclass, field
from pathlib import Path

from backend.services.adb_client import AdbClient, AdbError, AdbUnavailable, validate_serial

logger = logging.getLogger(__name__)

SCRCPY_VERSION = "3.3.4"
SERVER_JAR = Path(__file__).resolve().parents[1] / "vendor" / "scrcpy" / f"scrcpy-server-v{SCRCPY_VERSION}"
SERVER_SHA256 = "8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e"
DEVICE_JAR = "/data/local/tmp/automat-scrcpy-server.jar"
CONNECT_TIMEOUT_S = 8.0
MAX_SIZE = 1280

CODEC_H264 = 0x68323634  # "h264"
DEVICE_NAME_LENGTH = 64
PACKET_HEADER = struct.Struct(">QI")

# Control message types and Android constants used by the viewer.
MSG_INJECT_KEYCODE = 0
MSG_INJECT_TEXT = 1
MSG_INJECT_TOUCH = 2
ACTION_DOWN, ACTION_UP, ACTION_MOVE = 0, 1, 2
POINTER_ID_FINGER = -2
MAX_TEXT_BYTES = 300
ALLOWED_KEYCODES = frozenset({
    3, 4, 187,  # home, back, recent apps
    19, 20, 21, 22, 61, 66, 67, 112,  # arrows, tab, enter, backspace, forward delete
    24, 25, 26, 224,  # volume, power, wake
})


class LiveViewError(RuntimeError):
    pass


def _u16_fixed(value: float) -> int:
    value = min(max(value, 0.0), 1.0)
    return 0xFFFF if value >= 1.0 else int(value * 0x10000)


def pack_touch(action: int, x: int, y: int, width: int, height: int, pressure: float = 1.0) -> bytes:
    if action not in (ACTION_DOWN, ACTION_UP, ACTION_MOVE):
        raise ValueError("Unsupported touch action")
    if not (0 < width <= 0xFFFF and 0 < height <= 0xFFFF):
        raise ValueError("Invalid screen size")
    x = min(max(int(x), 0), width - 1)
    y = min(max(int(y), 0), height - 1)
    return struct.pack(">BBqiiHHHII", MSG_INJECT_TOUCH, action, POINTER_ID_FINGER, x, y, width, height,
                       _u16_fixed(0.0 if action == ACTION_UP else pressure), 0, 0)


def pack_key(action: int, keycode: int) -> bytes:
    if keycode not in ALLOWED_KEYCODES:
        raise ValueError("Unsupported key")
    return struct.pack(">BBiii", MSG_INJECT_KEYCODE, action, keycode, 0, 0)


def pack_text(text: str) -> bytes:
    data = text.encode("utf-8")
    if not data or len(data) > MAX_TEXT_BYTES:
        raise ValueError("Text must be 1 to 300 bytes")
    return struct.pack(">BI", MSG_INJECT_TEXT, len(data)) + data


def control_messages(message: dict) -> list[bytes]:
    """Translate one viewer message into scrcpy control messages."""
    kind = message.get("type")
    if kind == "touch":
        return [pack_touch(int(message["action"]), int(message["x"]), int(message["y"]),
                           int(message["width"]), int(message["height"]))]
    if kind == "key":
        keycode = int(message["keycode"])
        return [pack_key(ACTION_DOWN, keycode), pack_key(ACTION_UP, keycode)]
    if kind == "text":
        return [pack_text(str(message["text"]))]
    raise ValueError("Unsupported message")


def parse_codec_meta(data: bytes) -> dict:
    codec, width, height = struct.unpack(">III", data)
    if codec != CODEC_H264:
        raise LiveViewError("The phone sent an unexpected video codec")
    return {"codec": "h264", "width": width, "height": height}


def server_command(scid: int) -> list[str]:
    return [
        f"CLASSPATH={DEVICE_JAR}", "app_process", "/", "com.genymobile.scrcpy.Server", SCRCPY_VERSION,
        f"scid={scid:08x}", "log_level=info", "tunnel_forward=true", "audio=false", "control=true",
        "video_codec=h264", f"max_size={MAX_SIZE}", "video_bit_rate=4000000", "max_fps=30",
        "clipboard_autosync=false", "cleanup=true", "power_on=true",
    ]


def _verify_bundled_server() -> None:
    if not SERVER_JAR.is_file():
        raise LiveViewError("The bundled scrcpy server is missing. Reinstall the app.")
    if hashlib.sha256(SERVER_JAR.read_bytes()).hexdigest() != SERVER_SHA256:
        raise LiveViewError("The bundled scrcpy server failed its integrity check. Reinstall the app.")


def _ensure_server_on_phone(client: AdbClient, serial: str) -> None:
    _verify_bundled_server()
    remote = client.shell(serial, f"sha256sum {DEVICE_JAR} 2>/dev/null; true").split()
    if not remote or remote[0] != SERVER_SHA256:
        client.push(serial, str(SERVER_JAR), DEVICE_JAR)


@dataclass
class LiveSession:
    serial: str
    client: AdbClient
    scid: int = field(default_factory=lambda: secrets.randbits(31))
    port: int | None = None
    process: asyncio.subprocess.Process | None = None
    video: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
    control: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
    device_name: str = ""
    size: dict | None = None
    _stderr: list[str] = field(default_factory=list)

    async def start(self) -> None:
        await asyncio.to_thread(_ensure_server_on_phone, self.client, self.serial)
        self.port = await asyncio.to_thread(self.client.forward, self.serial, f"localabstract:scrcpy_{self.scid:08x}")
        self.process = await asyncio.create_subprocess_exec(
            self.client.binary, "-s", self.serial, "shell", *server_command(self.scid),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        asyncio.create_task(self._collect_output())
        try:
            self.video = await self._connect_first_socket()
            self.control = await asyncio.open_connection("127.0.0.1", self.port)
            reader = self.video[0]
            self.device_name = (await reader.readexactly(DEVICE_NAME_LENGTH)).split(b"\0", 1)[0].decode("utf-8", "replace")
            self.size = parse_codec_meta(await reader.readexactly(12))
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            detail = self._stderr[-1] if self._stderr else str(exc) or type(exc).__name__
            raise LiveViewError(f"The phone’s screen stream didn’t start: {detail}") from exc

    async def _connect_first_socket(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # adb accepts the forwarded connection before the server listens, so a
        # connection only counts once the server's dummy byte arrives.
        deadline = asyncio.get_running_loop().time() + CONNECT_TIMEOUT_S
        while True:
            if self.process and self.process.returncode is not None:
                raise LiveViewError("The scrcpy server exited: " + (self._stderr[-1] if self._stderr else "no output"))
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                try:
                    await asyncio.wait_for(reader.readexactly(1), timeout=1.0)
                    return reader, writer
                except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
                    writer.close()
            except OSError:
                pass
            if asyncio.get_running_loop().time() >= deadline:
                raise asyncio.TimeoutError("timed out waiting for the scrcpy server")
            await asyncio.sleep(0.2)

    async def _collect_output(self) -> None:
        assert self.process and self.process.stdout
        async for raw in self.process.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._stderr = (self._stderr + [line])[-20:]
                logger.info("scrcpy %s: %s", self.serial, line)

    async def read_packet(self) -> bytes:
        """One scrcpy video packet: 12-byte header (pts and flags, size) plus payload."""
        assert self.video
        header = await self.video[0].readexactly(PACKET_HEADER.size)
        _, size = PACKET_HEADER.unpack(header)
        return header + await self.video[0].readexactly(size)

    async def send(self, message: dict) -> None:
        assert self.control
        writer = self.control[1]
        for data in control_messages(message):
            writer.write(data)
        await writer.drain()

    async def drain_device_messages(self) -> None:
        assert self.control
        while await self.control[0].read(4096):
            pass

    async def close(self) -> None:
        for pair in (self.video, self.control):
            if pair:
                pair[1].close()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
        if self.port:
            try:
                await asyncio.to_thread(self.client.remove_forward, self.serial, self.port)
            except AdbError:
                pass


_sessions: dict[str, LiveSession] = {}


async def open_session(serial: str, client: AdbClient | None = None) -> LiveSession:
    """Start a live session, replacing any earlier one on the same phone."""
    validate_serial(serial)
    client = client or AdbClient()
    if not client.binary:
        raise AdbUnavailable("ADB is not installed. Install Android platform-tools and reopen the app.")
    previous = _sessions.pop(serial, None)
    if previous:
        await previous.close()
    session = LiveSession(serial, client)
    _sessions[serial] = session
    try:
        await session.start()
    except BaseException:
        await close_session(session)
        raise
    return session


async def close_session(session: LiveSession) -> None:
    if _sessions.get(session.serial) is session:
        _sessions.pop(session.serial, None)
    await session.close()
