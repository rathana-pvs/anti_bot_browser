import json
from types import SimpleNamespace

import pytest

from backend.services import android_devices, android_files
from backend.services.adb_client import AdbError
from backend.services.android_devices import DeviceNotFound


def reply(payload):
    return "Result: Bundle[{json=" + json.dumps({"ok": True, **payload}) + "}]\n"


class FakePhone:
    """One phone with Android users 0 and 11 (the account's); 11 is on screen unless told otherwise."""

    def __init__(self, running=True, free_mb=10_000, on_screen=True):
        self.running = {0: True, 11: running}
        self.free_mb = free_mb
        self.current = 11 if on_screen and running else 0
        self.events = []
        self.written = []
        self.media_state = "saved"
        # States the next saves end in, one per save; then media_state.
        self.media_states = []
        self.media_error = "disk full"
        self.log = ""
        # The companion app stops reading the file once the gallery refuses it.
        self.stalls = False

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

    def write_content(self, serial, user_id, uri, local, timeout=120, stop_when=None):
        assert self.running[user_id], "wrote into a stopped user"
        self.written.append((serial, user_id, uri, open(local, "rb").read()))
        self.events.append(("write", user_id))
        if self.stalls:
            # The companion app stopped reading: only stop_when can end the write.
            assert stop_when is not None and stop_when(), "a stalled write was never cut short"
            self.events.append(("write stopped", user_id))
            return False
        return True

    def shell(self, serial, *command, timeout=None):
        if command[0] == "logcat":
            self.events.append(("logcat",))
            return self.log
        method = command[command.index("--method") + 1]
        user = int(command[command.index("--user") + 1])
        assert self.running[user]
        self.events.append((method, user))
        if method == "media_status":
            state = self.media_states.pop(0) if self.media_states else self.media_state
            return reply({"state": state, "uri": "content://media/external/images/media/77", "size": 3,
                          "error": self.media_error})
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


@pytest.mark.parametrize("phone", [FakePhone(running=False), FakePhone(running=True, on_screen=False)],
                         ids=["stopped", "running in the background"])
def test_files_go_only_to_the_account_on_screen_and_no_user_is_started(account, phone):
    _, photo = account
    with pytest.raises(AdbError, match="isn't on the phone's screen"):
        android_files.copy_file("profile_020", photo, "a.png", client=phone)
    assert phone.events == [] and phone.written == []


def test_a_file_is_removed_only_while_the_account_is_on_screen(account):
    _, photo = account
    record = android_files.copy_file("profile_020", photo, "a.jpg", client=FakePhone())
    away = FakePhone(on_screen=False)
    with pytest.raises(AdbError, match="isn't on the phone's screen"):
        android_files.remove_file("profile_020", record["id"], client=away)
    assert away.events == []
    assert [item["id"] for item in android_files.list_files("profile_020", client=away)["files"]] == [record["id"]]


def test_the_file_list_says_whether_the_account_is_on_screen(account):
    assert android_files.list_files("profile_020", client=FakePhone())["on_screen"] is True
    assert android_files.list_files("profile_020", client=FakePhone(on_screen=False))["on_screen"] is False


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


def test_a_failed_save_is_not_recorded(account):
    _, photo = account
    phone = FakePhone()
    phone.media_state = "failed"
    with pytest.raises(AdbError, match="disk full"):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert android_files.list_files("profile_020", client=phone)["files"] == []


SCALAR = "Exception thrown by custom scalar function (code 1 SQLITE_ERROR)"
PHONE_LOG = """E/MediaProvider( 900): java.lang.IllegalStateException: an older unrelated error
E/SQLiteConnection( 900): An exception was thrown by custom scalar function.
E/SQLiteConnection( 900): java.lang.RuntimeException: wrapped
E/SQLiteConnection( 900): \tat com.android.providers.media.DatabaseHelper.lambda$onConfigure$1(DatabaseHelper.java:612)
E/SQLiteConnection( 900): Caused by: java.lang.NumberFormatException: For input string: "abc"
E/SQLiteConnection( 900): \t... 12 more
"""


def test_a_gallery_database_error_is_retried_once_under_a_new_name(account, monkeypatch):
    monkeypatch.setattr(android_files, "RETRY_DELAY_S", 0)
    _, photo = account
    phone = FakePhone()
    phone.media_states, phone.media_error = ["failed"], SCALAR
    record = android_files.copy_file("profile_020", photo, "photo_2026-09-07_17-50-02.jpg", client=phone)
    first, second = (uri for _, _, uri, _ in phone.written)
    assert first != second and record["name"] in second
    assert record["name"].startswith("photo_2026-09-07_17-50-02_") and record["name"].endswith(".jpg")
    assert [item["id"] for item in android_files.list_files("profile_020", client=phone)["files"]] == [record["id"]]
    assert ("logcat",) not in phone.events


def test_a_repeated_gallery_database_error_reports_the_phones_own_reason(account, monkeypatch):
    monkeypatch.setattr(android_files, "RETRY_DELAY_S", 0)
    _, photo = account
    phone = FakePhone()
    phone.media_state, phone.media_error, phone.log = "failed", SCALAR, PHONE_LOG
    with pytest.raises(AdbError, match='refused the file twice: .*SQLITE_ERROR.*NumberFormatException: For input string: "abc"'):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert len(phone.written) == 2
    assert android_files.list_files("profile_020", client=phone)["files"] == []


# As logged by a Pixel 8 Pro with 31 Android users, for user 33.
MARKERS_FULL_LOG = """E/DatabaseBackupAndRecovery(17951): Failed to set xattr:user.extdbnextrowid33 to 1000001000 for path: /data/media/0.
E/DatabaseBackupAndRecovery(17951): android.system.ErrnoException: setxattr failed: E2BIG (Argument list too long)
E/MediaProvider(17439): Xattr:user.extdbnextrowid33 not found on external storage: android.system.ErrnoException: getxattr failed: ENODATA (No data available)
E/SQLiteConnection(17439): Exception thrown by custom scalar function
W/System.err(17439): java.lang.RuntimeException: Cannot find next row id xattr for external.db.
"""


def test_a_refused_file_stops_the_transfer_instead_of_waiting_for_adb(account, monkeypatch):
    monkeypatch.setattr(android_files, "RETRY_DELAY_S", 0)
    _, photo = account
    phone = FakePhone()
    phone.stalls, phone.media_state, phone.media_error = True, "failed", SCALAR
    with pytest.raises(AdbError, match="refused the file twice"):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert phone.events.count(("write stopped", 11)) == 2


def test_a_phone_out_of_gallery_markers_is_explained(account, monkeypatch):
    monkeypatch.setattr(android_files, "RETRY_DELAY_S", 0)
    _, photo = account
    phone = FakePhone()
    phone.stalls, phone.media_state, phone.media_error, phone.log = True, "failed", SCALAR, MARKERS_FULL_LOG
    with pytest.raises(AdbError) as error:
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert str(error.value) == android_files.MARKERS_FULL
    assert "persist.sys.fuse.backup.nextrowid_enabled" in str(error.value)
    assert android_files.list_files("profile_020", client=phone)["files"] == []


@pytest.mark.parametrize("log, full", [(MARKERS_FULL_LOG, True), (PHONE_LOG, False), ("", False)])
def test_the_full_marker_cause_is_read_from_the_log(log, full):
    assert android_files.gallery_markers_full(log) is full


def test_other_save_failures_are_not_retried(account):
    _, photo = account
    phone = FakePhone()
    phone.media_state = "failed"
    with pytest.raises(AdbError, match="could not save the file: disk full"):
        android_files.copy_file("profile_020", photo, "a.jpg", client=phone)
    assert len(phone.written) == 1


@pytest.mark.parametrize("log, cause", [
    (PHONE_LOG, 'NumberFormatException: For input string: "abc"'),
    ("", None),
    ("E/MediaProvider( 900): java.lang.IllegalStateException: Failed to set xattr", "IllegalStateException: Failed to set xattr"),
])
def test_the_phones_reason_is_read_from_its_log(log, cause):
    assert android_files.gallery_cause(log) == cause


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


def test_a_dropped_file_is_copied_from_its_path_on_this_computer(tmp_path, monkeypatch):
    photo = tmp_path / "Profile Pic.JPG"
    photo.write_bytes(b"jpeg")
    copied = []
    monkeypatch.setattr(android_files, "copy_file", lambda pid, local, name, client=None: copied.append((pid, local, name)) or {"id": "x"})
    assert android_files.copy_local_file("profile_001", str(photo)) == {"id": "x"}
    assert copied == [("profile_001", photo.resolve(), "Profile Pic.JPG")]


@pytest.mark.parametrize("path, message", [
    ("relative/photo.jpg", "location could not be read"),
    ("/no/such/dir/photo.jpg", "could not be found"),
    ("/etc/passwd", "not a JPEG, PNG or MP4"),
])
def test_a_dropped_path_must_be_an_existing_photo_or_video(path, message, monkeypatch):
    monkeypatch.setattr(android_files, "copy_file", lambda *args, **kwargs: pytest.fail("nothing may be copied"))
    with pytest.raises(ValueError, match=message):
        android_files.copy_local_file("profile_001", path)


def test_a_dropped_folder_is_refused(tmp_path, monkeypatch):
    folder = tmp_path / "album.mp4"
    folder.mkdir()
    monkeypatch.setattr(android_files, "copy_file", lambda *args, **kwargs: pytest.fail("nothing may be copied"))
    with pytest.raises(ValueError, match="not a file"):
        android_files.copy_local_file("profile_001", str(folder))


def test_the_phones_log_is_filtered_before_it_is_cut(monkeypatch):
    """A phone with many users logs hundreds of lines a second, so `logcat -t` would cut the gallery
    errors away before the tag filter runs. Only the last filtered lines are kept."""
    monkeypatch.setattr(android_files, "CAUSE_LOG_LINES", 3)
    asked = []

    class Phone:
        def shell(self, serial, *command, timeout=None):
            asked.append(command)
            return "\n".join(f"E/MediaProvider( 1): line {n}" for n in range(10))

    log = android_files._read_gallery_log(Phone(), "PIXEL")
    assert "-t" not in asked[0] and "MediaProvider:E" in asked[0]
    assert log.splitlines() == [f"E/MediaProvider( 1): line {n}" for n in (7, 8, 9)]
