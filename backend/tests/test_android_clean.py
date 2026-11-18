import json
from types import SimpleNamespace

import pytest

from backend.services import android_clean, android_devices, android_ownership, android_setup, proxy_service
from backend.services.adb_client import AdbError
from backend.services.android_devices import DeviceNotFound


class FakePhone:
    """Users 0 and 11 (the account's, serial 13); 11 is stopped and not on screen unless told."""

    def __init__(self, running=False, on_screen=False, fail=(), media_left=0, user_serial=13, missing=False):
        self.running = {0: True} if missing else {0: True, 11: running}
        self.current = 11 if on_screen else 0
        self.fail = set(fail)
        self.media_left = media_left
        self.user_serial = user_serial
        self.events = []

    def users(self, serial):
        return [SimpleNamespace(user_id=uid, running=on) for uid, on in self.running.items()]

    def user_serials(self, serial):
        return {0: 0, 11: self.user_serial}

    def current_user(self, serial):
        return self.current

    def start_user(self, serial, user_id):
        self.events.append(("start", user_id))
        self.running[user_id] = True

    def stop_user(self, serial, user_id):
        self.events.append(("stop", user_id))
        self.running[user_id] = False

    def shell(self, serial, *command, timeout=None):
        text = " ".join(command)
        if text.startswith("pm list packages -3"):
            return "package:com.facebook.katana\npackage:com.isolatedbrowser.agent\n"
        if text.startswith("pm list packages"):
            return "package:com.android.chrome\npackage:com.google.android.apps.photos\npackage:com.facebook.katana\n"
        assert self.running[11], "touched a stopped user"
        if text.startswith("pm clear --user 11 "):
            package = command[-1]
            self.events.append(("clear", package))
            return "Failed\n" if package in self.fail else "Success\n"
        if text.startswith("content delete"):
            assert "/storage/emulated/11/%" in text and "format!=12289" in text
            self.events.append(("delete_media",))
            return ""
        if text.startswith("content query"):
            return "".join(f"Row: {i} _id={i}\n" for i in range(self.media_left)) or "No result found.\n"
        raise AssertionError(text)


def clean(phone):
    return android_clean.start("profile_020", client=phone, background=False)


def statuses(progress):
    return {step["key"]: step["status"] for step in progress["steps"]}


@pytest.fixture
def account(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    (root / "profile_020").mkdir(parents=True)
    (root / "profile_020" / "config.json").write_text(json.dumps({
        "id": "profile_020", "runtime": {"kind": "android_user", "device_serial": "PIXEL",
                                         "android_user_id": 11, "android_user_serial": 13}}))
    (root / "profile_pc").mkdir()
    (root / "profile_pc" / "config.json").write_text(json.dumps({"id": "profile_pc"}))
    monkeypatch.setattr(android_clean, "PROFILES_DIR", root)
    monkeypatch.setattr(android_devices, "PROFILES_DIR", root)
    monkeypatch.setattr(android_devices, "job_on", lambda serial: None)
    monkeypatch.setattr(android_ownership, "ensure_primary", lambda serial, client=None: None)
    released = []
    monkeypatch.setattr(proxy_service, "release_proxy", released.append)
    scanned = []
    monkeypatch.setattr(android_devices, "scan_device", lambda serial, full=False: scanned.append(serial))
    monkeypatch.setattr(android_clean, "_progress", {})
    forgotten = []
    monkeypatch.setattr(android_devices, "forget_saved_agent", lambda serial, user_id: forgotten.append((serial, user_id)))
    return SimpleNamespace(root=root, released=released, scanned=scanned, forgotten=forgotten)


APPS = ["com.facebook.katana", "com.isolatedbrowser.agent", "com.android.chrome", "com.google.android.apps.photos"]


def test_clean_clears_every_app_and_the_gallery_then_forgets_the_account(account):
    phone = FakePhone()
    progress = clean(phone)
    assert progress["state"] == "done" and set(statuses(progress).values()) == {"passed"}
    assert progress["result"] == {"cleared_apps": APPS, "media_left": 0}
    apps = next(step for step in progress["steps"] if step["key"] == "apps")
    assert apps["detail"] == "Facebook, Companion app, Chrome, Google Photos"
    assert phone.events == [("start", 11), *(("clear", app) for app in APPS), ("delete_media",), ("stop", 11)]
    assert not (account.root / "profile_020").exists() and (account.root / "profile_pc").exists()
    assert account.released == ["profile_020"] and account.scanned == ["PIXEL"]
    # The scan reads the cleared companion again instead of showing its old account.
    assert account.forgotten == [("PIXEL", 11)]
    # Still readable once the account's folder is gone.
    assert android_clean.progress("profile_020")["state"] == "done"


def test_a_user_on_screen_stays_running(account):
    phone = FakePhone(running=True, on_screen=True)
    progress = clean(phone)
    assert not any(kind in ("start", "stop") for kind, *_ in phone.events)
    assert progress["steps"][1]["detail"] == "Already running"


def test_an_app_that_did_not_clear_keeps_the_account_for_a_retry(account):
    phone = FakePhone(fail={"com.facebook.katana"})
    progress = clean(phone)
    assert progress["state"] == "failed" and "Facebook" in progress["reason"]
    assert statuses(progress) == {"check": "passed", "start_user": "passed", "apps": "failed",
                                  "gallery": "pending", "forget": "pending"}
    assert (account.root / "profile_020" / "config.json").exists() and account.released == []
    assert ("delete_media",) not in phone.events and phone.events[-1] == ("stop", 11)
    # Clean can run again after a failure.
    assert clean(FakePhone())["state"] == "done"


def test_gallery_files_left_are_reported(account):
    progress = clean(FakePhone(media_left=2))
    assert progress["result"]["media_left"] == 2
    assert progress["steps"][3]["detail"] == "2 files stayed on the phone"


@pytest.mark.parametrize("phone, why", [(FakePhone(user_serial=99), "created again"), (FakePhone(missing=True), "no longer")],
                         ids=["recreated", "removed"])
def test_an_account_whose_user_is_gone_is_only_removed_here(account, phone, why):
    progress = clean(phone)
    assert progress["state"] == "done" and why in progress["steps"][0]["detail"]
    assert [step["detail"] for step in progress["steps"][1:4]] == ["Skipped"] * 3
    assert phone.events == []  # Never touches a user that is not this account's.
    assert not (account.root / "profile_020").exists() and account.released == ["profile_020"]
    assert account.forgotten == []


def test_forget_saved_agent_drops_only_that_users_companion_details(tmp_path, monkeypatch):
    path = tmp_path / "android_devices.json"
    agent = {"readable": True, "account": {"profile_id": "profile_085"}}
    path.write_text(json.dumps({"devices": {"PIXEL": {"last_scan": {"users": [
        {"user_id": 11, "agent": agent}, {"user_id": 12, "agent": agent}]}}}}))
    monkeypatch.setattr(android_devices, "DEVICES_FILE", path)
    android_devices.forget_saved_agent("PIXEL", 11)
    users = json.loads(path.read_text())["devices"]["PIXEL"]["last_scan"]["users"]
    assert users == [{"user_id": 11, "agent": None}, {"user_id": 12, "agent": agent}]


def test_no_progress_before_a_clean(account):
    with pytest.raises(DeviceNotFound):
        android_clean.progress("profile_020")


def test_pc_accounts_and_the_owner_user_are_refused(account):
    with pytest.raises(DeviceNotFound):
        android_clean.start("profile_pc", background=False, client=FakePhone())
    config = account.root / "profile_020" / "config.json"
    data = json.loads(config.read_text())
    data["runtime"]["android_user_id"] = 0
    config.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="control user"):
        clean(FakePhone())


def test_a_job_on_the_phone_or_a_running_setup_blocks_it(account, monkeypatch):
    monkeypatch.setattr(android_setup, "_running", {"profile_020"})
    with pytest.raises(ValueError, match="setup is running"):
        clean(FakePhone())
    monkeypatch.setattr(android_setup, "_running", set())
    monkeypatch.setattr(android_devices, "job_on", lambda serial: "post:abc")
    with pytest.raises(android_devices.PhoneBusy):
        clean(FakePhone())
    assert (account.root / "profile_020").exists()
