import json
from types import SimpleNamespace

import pytest

from backend.services import android_devices, android_files
from backend.services.adb_client import AdbError
from backend.services.android_devices import DeviceNotFound


def reply(payload):
    return "Result: Bundle[{json=" + json.dumps({"ok": True, **payload}) + "}]\n"


class FakePhone:
    """One phone with Android users 0 (on screen) and 11 (the account's)."""

    def __init__(self, running=True, free_mb=10_000):
        self.running = {0: True, 11: running}
        self.free_mb = free_mb
        self.current = 0
        self.events = []
        self.written = []
        self.media_state = "saved"

    def users(self, serial):
        return [SimpleNamespace(user_id=uid, running=on) for uid, on in self.running.items()]

    def current_user(self, serial):
        return self.current

    def start_user(self, serial, user_id):
        self.events.append(("start", user_id))
        self.running[user_id] = True

    def stop_user(self, serial, user_id):
        self.events.append(("stop", user_id))
        self.running[user_id] = False

    def free_storage_mb(self, serial):
        return self.free_mb

    def write_content(self, serial, user_id, uri, local, timeout=120):
        assert self.running[user_id], "wrote into a stopped user"
        self.written.append((serial, user_id, uri, open(local, "rb").read()))
        self.events.append(("write", user_id))

    def shell(self, serial, *command, timeout=None):
        method = command[command.index("--method") + 1]
        user = int(command[command.index("--user") + 1])
        assert self.running[user]
        self.events.append((method, user))
        if method == "media_status":
            return reply({"state": self.media_state, "uri": "content://media/external/images/media/77", "size": 3,
                          "error": "disk full"})
        if method == "delete_media":
            return reply({"deleted": 1})
        raise AssertionError(method)


@pytest.fixture
def account(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    (root / "profile_020").mkdir(parents=True)
    (root / "profile_020" / "config.json").write_text(json.dumps({
        "id": "profile_020", "runtime": {"kind": "android_user", "device_serial": "PIXEL", "android_user_id": 11}}))
    (root / "profile_pc").mkdir()
    (root / "profile_pc" / "config.json").write_text(json.dumps({"id": "profile_pc"}))
    monkeypatch.setattr(android_files, "PROFILES_DIR", root)
    monkeypatch.setattr(android_devices, "PROFILES_DIR", root)
    monkeypatch.setattr(android_devices, "job_on", lambda serial: None)
    photo = tmp_path / "upload.jpg"
    photo.write_bytes(b"jpg")
    return root, photo


def test_copy_saves_into_the_accounts_own_user_and_is_listed(account):
    root, photo = account
    phone = FakePhone()
    record = android_files.copy_file("profile_020", photo, "My avatar (1).JPG", client=phone)
    serial, user, uri, data = phone.written[0]
    assert (serial, user, data) == ("PIXEL", 11, b"jpg")
    assert "mime=image/jpeg" in uri and f"name={record['name']}" in uri
    assert record["name"].startswith("My_avatar_1_") and record["name"].endswith(".jpg")
    assert record["uri"] == "content://media/external/images/media/77" and record["album"] == "Pictures"
    assert android_files.list_files("profile_020")["files"] == [record]
    assert not any(kind in ("start", "stop") for kind, _ in phone.events)


def test_a_stopped_user_is_started_for_the_copy_and_stopped_again(account):
    _, photo = account
    phone = FakePhone(running=False)
    android_files.copy_file("profile_020", photo, "a.png", client=phone)
    assert phone.events[0] == ("start", 11) and phone.events[-1] == ("stop", 11)
    assert phone.running[11] is False


def test_a_user_brought_on_screen_meanwhile_is_not_stopped(account):
    _, photo = account
    phone = FakePhone(running=False)
    phone.current = 11
    android_files.copy_file("profile_020", photo, "a.png", client=phone)
    assert ("stop", 11) not in phone.events


@pytest.mark.parametrize("name", ["notes.pdf", "clip.mov", "noext"])
def test_only_photos_and_videos_are_accepted(account, name):
    _, photo = account
    with pytest.raises(ValueError, match="JPEG, PNG or MP4"):
        android_files.copy_file("profile_020", photo, name, client=FakePhone())


def test_nothing_is_copied_while_the_phone_runs_a_job(account, monkeypatch):
    _, photo = account
    monkeypatch.setattr(android_devices, "job_on", lambda serial: "job-1")
    phone = FakePhone()
    with pytest.raises(AdbError, match="running a job"):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert phone.events == []


def test_a_nearly_full_phone_is_refused(account):
    _, photo = account
    phone = FakePhone(free_mb=100)
    with pytest.raises(AdbError, match="100 MB free"):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert phone.written == []


def test_a_failed_save_is_not_recorded_and_the_user_is_restored(account):
    _, photo = account
    phone = FakePhone(running=False)
    phone.media_state = "failed"
    with pytest.raises(AdbError, match="disk full"):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert android_files.list_files("profile_020")["files"] == []
    assert phone.running[11] is False


def test_remove_deletes_on_the_phone_and_forgets_the_file(account):
    _, photo = account
    phone = FakePhone()
    record = android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    result = android_files.remove_file("profile_020", record["id"], client=phone)
    assert result == {"removed": record["id"], "deleted_on_phone": True}
    assert ("delete_media", 11) in phone.events
    assert android_files.list_files("profile_020")["files"] == []


def test_unknown_accounts_files_and_pc_accounts_are_refused(account):
    with pytest.raises(DeviceNotFound):
        android_files.list_files("../etc")
    with pytest.raises(DeviceNotFound):
        android_files.list_files("profile_missing")
    with pytest.raises(ValueError, match="not on a phone"):
        android_files.list_files("profile_pc")
    with pytest.raises(DeviceNotFound):
        android_files.remove_file("profile_020", "000000000000", client=FakePhone())


def test_upload_route_copies_each_file_and_rejects_other_types(account, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import devices

    phone = FakePhone()
    monkeypatch.setattr(android_devices, "_client", lambda: phone)
    app = FastAPI()
    app.include_router(devices.router)
    client = TestClient(app)
    ok = client.post("/api/devices/accounts/profile_020/files", files={"file": ("cover.png", b"png", "image/png")})
    assert ok.status_code == 201 and ok.json()["original_name"] == "cover.png"
    assert phone.written[0][3] == b"png"
    bad = client.post("/api/devices/accounts/profile_020/files", files={"file": ("doc.pdf", b"x", "application/pdf")})
    assert bad.status_code == 400
    listed = client.get("/api/devices/accounts/profile_020/files").json()["files"]
    assert [item["id"] for item in listed] == [ok.json()["id"]]
    assert client.delete(f"/api/devices/accounts/profile_020/files/{ok.json()['id']}").status_code == 200
