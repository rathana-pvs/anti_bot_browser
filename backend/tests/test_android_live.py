import asyncio
import hashlib
import struct
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import app
from backend.routers import devices
from backend.services import android_live
from backend.services.adb_client import AdbError


def test_touch_message_matches_scrcpy_layout():
    data = android_live.pack_touch(android_live.ACTION_DOWN, 100, 200, 576, 1280)
    assert len(data) == 32
    assert struct.unpack(">BBqiiHHHII", data) == (2, 0, -2, 100, 200, 576, 1280, 0xFFFF, 0, 0)
    up = struct.unpack(">BBqiiHHHII", android_live.pack_touch(android_live.ACTION_UP, 9999, -5, 576, 1280))
    # Coordinates are clamped to the screen and lifting a finger has no pressure.
    assert up[3:5] == (575, 0) and up[7] == 0


def test_key_and_text_messages():
    assert android_live.pack_key(android_live.ACTION_DOWN, 4) == struct.pack(">BBiii", 0, 0, 4, 0, 0)
    assert android_live.control_messages({"type": "key", "keycode": 3}) == [
        struct.pack(">BBiii", 0, 0, 3, 0, 0), struct.pack(">BBiii", 0, 1, 3, 0, 0)]
    assert android_live.pack_text("héllo") == b"\x01\x00\x00\x00\x06" + "héllo".encode()


@pytest.mark.parametrize("message", [
    {"type": "key", "keycode": 999},
    {"type": "text", "text": ""},
    {"type": "text", "text": "x" * 301},
    {"type": "touch", "action": 7, "x": 1, "y": 1, "width": 10, "height": 10},
    {"type": "touch", "action": 0, "x": 1, "y": 1, "width": 0, "height": 10},
    {"type": "shell", "command": "rm -rf /"},
])
def test_unsupported_viewer_messages_are_rejected(message):
    with pytest.raises(ValueError):
        android_live.control_messages(message)


def test_codec_meta_and_server_command():
    assert android_live.parse_codec_meta(struct.pack(">III", 0x68323634, 576, 1280)) == {
        "codec": "h264", "width": 576, "height": 1280}
    with pytest.raises(android_live.LiveViewError):
        android_live.parse_codec_meta(struct.pack(">III", 0x68323635, 1, 1))
    command = android_live.server_command(0x1234abcd)
    assert command[:5] == [f"CLASSPATH={android_live.DEVICE_JAR}", "app_process", "/", "com.genymobile.scrcpy.Server",
                           android_live.SCRCPY_VERSION]
    assert "scid=1234abcd" in command and "audio=false" in command and "tunnel_forward=true" in command


def test_bundled_server_matches_pinned_digest():
    assert hashlib.sha256(android_live.SERVER_JAR.read_bytes()).hexdigest() == android_live.SERVER_SHA256


def test_live_websocket_requires_the_desktop_token(monkeypatch):
    monkeypatch.setenv("MANAGER_API_TOKEN", "secret-token")
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/devices/R58M12345ABC/live?access_token=wrong"):
            pass
    assert error.value.code == 1008


def test_live_websocket_accepts_the_token_header_from_the_dev_proxy(monkeypatch):
    monkeypatch.setenv("MANAGER_API_TOKEN", "secret-token")
    monkeypatch.setattr(android_live, "open_session", _no_phone)
    client = TestClient(app)
    with client.websocket_connect("/api/devices/R58M12345ABC/live", headers={"X-Manager-Token": "secret-token"}) as ws:
        assert ws.receive_json()["type"] == "status"
        assert ws.receive_json() == {"type": "error", "message": "no phone"}


async def _no_phone(serial):
    raise android_live.LiveViewError("no phone")


def test_reset_video_asks_the_phone_for_a_new_key_frame():
    assert android_live.control_messages({"type": "reset_video"}) == [bytes([17])]


def test_stream_keeps_the_phone_awake():
    assert "stay_awake=true" in android_live.server_command(1)


def test_viewer_hears_only_when_the_screen_changes(monkeypatch):
    # The last reading is an error the watcher does not catch, which ends its loop.
    readings = iter([True, False, False, AdbError("slow"), None, True, RuntimeError("done")])

    def screen_awake(_serial):
        reading = next(readings)
        if isinstance(reading, Exception):
            raise reading
        return reading

    sent = []

    class Socket:
        async def send_json(self, message):
            sent.append(message)

    monkeypatch.setattr(devices, "SCREEN_POLL_S", 0)
    monkeypatch.setattr(devices, "SCREEN_WAKE_GRACE_S", 0)
    session = SimpleNamespace(serial="PHONE", client=SimpleNamespace(screen_awake=screen_awake))
    with pytest.raises(RuntimeError, match="done"):
        asyncio.run(devices._watch_screen(Socket(), session))
    assert sent == [{"type": "screen", "awake": False}, {"type": "screen", "awake": True}]


def test_screen_that_sleeps_as_the_stream_starts_is_woken_without_telling_the_viewer(monkeypatch):
    readings = iter([False, True, RuntimeError("done")])
    woken, sent = [], []

    def screen_awake(_serial):
        reading = next(readings)
        if isinstance(reading, Exception):
            raise reading
        return reading

    class Socket:
        async def send_json(self, message):
            sent.append(message)

    monkeypatch.setattr(devices, "SCREEN_POLL_S", 0)
    session = SimpleNamespace(serial="PHONE", client=SimpleNamespace(screen_awake=screen_awake, wake_screen=woken.append))
    with pytest.raises(RuntimeError, match="done"):
        asyncio.run(devices._watch_screen(Socket(), session))
    assert woken == ["PHONE"] and sent == []


def test_stream_start_wakes_the_phone_first():
    woken = []

    def wake_screen(serial):
        woken.append(serial)
        raise android_live.LiveViewError("stop after waking")

    with pytest.raises(android_live.LiveViewError):
        asyncio.run(android_live.LiveSession("PHONE", SimpleNamespace(wake_screen=wake_screen)).start())
    assert woken == ["PHONE"]
