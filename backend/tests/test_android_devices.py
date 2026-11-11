import json
from unittest.mock import Mock, call

import pytest
from fastapi import HTTPException

from backend.models.profile import ProfileUpdateRequest
from backend.routers import devices, profiles
from backend.services import adb_client, android_devices, android_network, android_ownership, docker_service, proxy_service
from backend.services.adb_client import AdbError, AndroidUser


DEVICES_OUTPUT = """List of devices attached
R58M12345ABC           device usb:1-1.2 product:beyond1 model:SM_G973F device:beyond1 transport_id:3
192.168.1.40:5555      device product:oriole model:Pixel_6 device:oriole transport_id:4
0123456789ABCDEF       unauthorized usb:1-1.3 transport_id:5

"""

USERS_OUTPUT = """Users:
\tUserInfo{0:Owner:c13} running
\tUserInfo{10:FB Page: News:410} running
\tUserInfo{11:Second:410}
"""

DUMPSYS_USER = """Users:
  UserInfo{0:Owner:c13} serialNo=0 isPrimary=true
  UserInfo{10:FB Page: News:410} serialNo=12
  UserInfo{11:Second:410} serialNo=13
"""


def test_parse_devices_reads_state_connection_and_model():
    parsed = adb_client.parse_devices(DEVICES_OUTPUT)
    assert parsed == [
        {"serial": "R58M12345ABC", "state": "device", "connection": "usb", "model": "SM G973F"},
        {"serial": "192.168.1.40:5555", "state": "device", "connection": "wifi", "model": "Pixel 6"},
        {"serial": "0123456789ABCDEF", "state": "unauthorized", "connection": "usb", "model": None},
    ]


def test_parse_users_keeps_colons_in_names_and_running_state():
    assert adb_client.parse_users(USERS_OUTPUT) == [
        AndroidUser(0, "Owner", 0xC13, True),
        AndroidUser(10, "FB Page: News", 0x410, True),
        AndroidUser(11, "Second", 0x410, False),
    ]
    assert adb_client.parse_user_serials(DUMPSYS_USER) == {0: 0, 10: 12, 11: 13}


def test_parse_health_outputs():
    battery = adb_client.parse_battery("Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  level: 87\n  temperature: 315\n")
    assert battery == {"level": 87, "temperature_c": 31.5, "charging": True}
    df = "Filesystem      1K-blocks    Used Available Use% Mounted on\n/dev/block/dm-5 115823616 40000000 75823616  35% /data\n"
    assert adb_client.parse_free_storage_mb(df) == 74046
    assert adb_client.parse_version_name("    versionCode=1 minSdk=28\n    versionName=450.0.0.40.109\n") == "450.0.0.40.109"
    assert adb_client.parse_package_installed("package:com.facebook.katana\npackage:com.facebook.orca\n", "com.facebook.katana")
    assert not adb_client.parse_package_installed("package:com.facebook.katana.lite\n", "com.facebook.katana")
    assert adb_client.parse_int("Maximum supported users: 4") == 4


def test_parsers_accept_pixel_android_17_output():
    # Recorded from a Pixel 10 Pro XL on Android 17 (SDK 37).
    assert adb_client.parse_devices(
        "List of devices attached\n57231FDCQ009EZ         device usb:3-1 product:mustang model:Pixel_10_Pro_XL device:mustang transport_id:1\n"
    ) == [{"serial": "57231FDCQ009EZ", "state": "device", "connection": "usb", "model": "Pixel 10 Pro XL"}]
    assert adb_client.parse_users("Users:\n\tUserInfo{0:Owner:4c13} running\n\tUserInfo{11:1:410}\n") == [
        AndroidUser(0, "Owner", 0x4C13, True), AndroidUser(11, "1", 0x410, False)]
    assert adb_client.parse_user_serials(
        "  UserInfo{0:null:4c13} serialNo=0 isPrimary=true\n  UserInfo{11:1:410} serialNo=11 isPrimary=false\n"
    ) == {0: 0, 11: 11}
    assert adb_client.parse_int("Maximum supported switchable users: 4") == 4
    battery = adb_client.parse_battery(
        "Current Battery Service state:\n  AC powered: false\n  USB powered: true\n"
        " Time when the latest updated value of the voltage was sent via battery changed broadcast: +1d5h36m6s276ms\n"
        "  level: 56\n  scale: 100\n  temperature: 324\n  Charging state: 1\n")
    assert battery == {"level": 56, "temperature_c": 32.4, "charging": True}
    assert adb_client.parse_free_storage_mb(
        "Filesystem       1K-blocks     Used Available Use% Mounted on\n"
        "/dev/block/dm-67 239543380 77177400 162234908  33% /data/user/0\n") == 158432
    assert adb_client.parse_total_storage_mb(
        "Filesystem       1K-blocks     Used Available Use% Mounted on\n"
        "/dev/block/dm-67 239543380 75635448 163776860  32% /data/user/0\n") == 233929
    assert adb_client.parse_memory("MemTotal:       15948992 kB\nMemFree:  451000 kB\nMemAvailable:    2330852 kB\n") == {
        "used_mb": 13299, "total_mb": 15575}
    assert adb_client.parse_cpu_percent(
        "Tasks: 912 total,   2 running, 910 sleeping,   0 stopped,   0 zombie\n"
        "  Mem:    15575M total,    15133M used,      441M free,        1M buffers\n"
        "800%cpu  44%user   3%nice 119%sys 622%idle   0%iow   8%irq   3%sirq   0%host\n") == 22
    assert adb_client.parse_cpu_percent("no summary") is None
    assert adb_client.parse_timezone_state("Asia/Phnom_Penh\ntrue\n") == {"zone": "Asia/Phnom_Penh", "auto": True}
    assert adb_client.parse_timezone_state("UTC\nfalse\n") == {"zone": "UTC", "auto": False}
    assert adb_client.parse_timezone_state("") == {"zone": None, "auto": None}
    assert [adb_client.parse_switch_flag(value) for value in ("1\n", "0", "null")] == [True, False, None]


@pytest.mark.parametrize("serial", ["", "a b", "x;rm -rf /", "a" * 65])
def test_serial_is_validated(serial):
    with pytest.raises(ValueError):
        adb_client.validate_serial(serial)


def test_parse_screen_and_lock_screen_state():
    assert adb_client.parse_screen_awake("POWER MANAGER (dumpsys power)\n  mWakefulness=Awake\n  mWakefulnessChanging=false\n") is True
    assert adb_client.parse_screen_awake("  mWakefulness=Asleep\n") is False
    assert adb_client.parse_screen_awake("  mWakefulness=Dozing\n") is False
    assert adb_client.parse_screen_awake("Display Power: state=ON\n") is True
    assert adb_client.parse_screen_awake("nothing useful") is None
    assert adb_client.parse_lock_screen_showing("  mKeyguardShowing=true\n") is True
    # The KeyguardController value wins over window policy flags.
    assert adb_client.parse_lock_screen_showing("  mKeyguardShowing=false\n    mDreamingLockscreen=true\n") is False
    assert adb_client.parse_lock_screen_showing("    mShowingLockscreen=false mShowingDream=false mDreamingLockscreen=true\n") is True
    assert adb_client.parse_lock_screen_showing("") is None
    # Recorded from a Pixel 10 Pro XL on Android 17.
    assert adb_client.parse_screen_awake(
        "  mWakefulness=Awake\n  mWakefulnessChanging=false\n"
        "Display Power: com.android.server.power.PowerManagerService$2@906ff57\nmWakefulness=1\n") is True
    assert adb_client.parse_screen_awake("mWakefulness=1\n  mWakefulness=Asleep\n") is False
    assert adb_client.parse_screen_awake("mWakefulness=0\n") is False
    assert adb_client.parse_lock_screen_showing(
        "    isKeyguardShowing=false\n  KeyguardController:\n    mKeyguardShowing=false\n    mKeyguardGoingAway=false\n"
        "   KeyguardShowing=false AodShowing=false KeyguardGoingAway=false at display=0\n") is False
    assert adb_client.parse_lock_screen_showing("    isKeyguardShowing=true\n") is True


class FakeAdb:
    """A phone that, like the Pixel, comes up asleep and locked after a switch."""

    def __init__(self):
        self.switched = []
        self.current = 10
        self.awake = True
        self.locked = False
        self.secure_lock = False
        self.starting = None
        self.woken = 0
        self.dismissed = 0
        self.users_list = adb_client.parse_users(USERS_OUTPUT)
        self.serials = adb_client.parse_user_serials(DUMPSYS_USER)
        self.settings = {}
        self.focus = "com.google.android.apps.nexuslauncher/.NexusLauncherActivity"
        self.shell_calls = []

    def setting(self, serial, namespace, key, user_id=None):
        return self.settings.get((user_id, namespace, key))

    def put_setting(self, serial, namespace, key, value, user_id=None):
        self.settings[(user_id, namespace, key)] = value

    def focused_window(self, serial):
        return self.focus

    def shell(self, *args):
        self.shell_calls.append(args)
        return ""

    def devices(self):
        return adb_client.parse_devices(DEVICES_OUTPUT)

    def properties(self, serial):
        return {"manufacturer": "samsung", "model": "SM-G973F", "android_version": "12", "sdk": 31}

    def users(self, serial):
        if self.starting is not None:
            # The switched-to user reports running one poll after the switch.
            self.users_list = [AndroidUser(u.user_id, u.name, u.flags, u.running or u.user_id == self.starting)
                               for u in self.users_list]
            self.starting = None
        return self.users_list

    def user_serials(self, serial):
        return self.serials

    def current_user(self, serial):
        return self.current

    def max_users(self, serial):
        return 4

    def package_installed(self, serial, user_id, package=adb_client.FACEBOOK_PACKAGE):
        # No companion app: these users were not set up by a PC.
        return user_id != 11 and package == adb_client.FACEBOOK_PACKAGE

    def read_file(self, serial, path):
        return None

    def package_version(self, serial, package=adb_client.FACEBOOK_PACKAGE):
        return "450.0.0.40.109"

    def battery(self, serial):
        return {"level": 80, "temperature_c": 30.0, "charging": True}

    def free_storage_mb(self, serial):
        return 2048

    def total_storage_mb(self, serial):
        return 65536

    zone = "Asia/Bangkok"
    auto_zone = True

    def timezone_state(self, serial):
        return {"zone": self.zone, "auto": self.auto_zone}

    def set_timezone(self, serial, zone):
        self.zone, self.auto_zone = zone, False

    switcher = True

    def user_switcher_enabled(self, serial):
        return self.switcher

    def set_user_switcher(self, serial, enabled):
        self.switcher = enabled

    def cpu_percent(self, serial):
        return 22

    def memory(self, serial):
        return {"used_mb": 13300, "total_mb": 15575}

    def switch_user(self, serial, user_id):
        self.switched.append(user_id)
        self.current = user_id
        self.starting = user_id
        self.awake = False
        self.locked = True

    def screen_awake(self, serial):
        return self.awake

    def lock_screen_showing(self, serial):
        return self.locked

    def wake_screen(self, serial):
        self.woken += 1
        self.awake = True

    def dismiss_lock_screen(self, serial):
        self.dismissed += 1
        if not self.secure_lock:
            self.locked = False


@pytest.fixture
def phone(tmp_path, monkeypatch):
    fake = FakeAdb()
    root = tmp_path / "profiles"
    root.mkdir()
    monkeypatch.setattr(android_devices, "_client", lambda: fake)
    monkeypatch.setattr(android_devices, "DEVICES_FILE", tmp_path / "android_devices.json")
    monkeypatch.setattr(android_devices, "PROFILES_DIR", root)
    monkeypatch.setattr(profiles, "PROFILES_DIR", root)
    monkeypatch.setattr(android_devices, "SWITCH_POLL_S", 0)
    monkeypatch.setattr(android_devices.time, "sleep", lambda _seconds: None)
    return fake


def test_list_devices_registers_authorized_phones_with_health(phone):
    result = android_devices.list_devices()
    assert result["adb_available"] is True
    by_serial = {device["serial"]: device for device in result["devices"]}
    assert set(by_serial) == {"R58M12345ABC", "192.168.1.40:5555", "0123456789ABCDEF"}
    assert by_serial["R58M12345ABC"]["health"]["battery"]["level"] == 80
    assert by_serial["R58M12345ABC"]["health"]["cpu_percent"] == 22
    assert by_serial["R58M12345ABC"]["health"]["memory"] == {"used_mb": 13300, "total_mb": 15575}
    # Unauthorized phones are shown but not remembered or queried.
    assert by_serial["0123456789ABCDEF"]["health"] is None
    stored = json.loads(android_devices.DEVICES_FILE.read_text())["devices"]
    assert "0123456789ABCDEF" not in stored


@pytest.mark.parametrize("saved", ["1", "0", None, "denied"])
def test_new_user_skips_only_its_own_welcome_setup_and_verifies_the_flag(phone, monkeypatch, saved):
    serial = "R58M12345ABC"
    writes = []
    monkeypatch.setattr(android_ownership, "ensure_primary", lambda *args: None)

    def create_user(device_serial, name):
        assert (device_serial, name) == (serial, "New account")
        phone.users_list.append(AndroidUser(12, name, 0x410, False))
        phone.serials[12] = 14
        return 12

    def put_setting(*args):
        writes.append(args)
        if saved == "denied":
            raise AdbError("Permission denied")

    def setting(*args):
        assert args == (serial, "secure", "user_setup_complete", 12)
        return saved

    monkeypatch.setattr(phone, "create_user", create_user, raising=False)
    monkeypatch.setattr(phone, "put_setting", put_setting, raising=False)
    monkeypatch.setattr(phone, "setting", setting, raising=False)
    if saved == "1":
        result = android_devices.create_android_user(serial, " New account ")
        assert result["user_id"] == 12
    else:
        with pytest.raises(AdbError, match="user 12 was created.*Use the existing user"):
            android_devices.create_android_user(serial, " New account ")
    assert writes == [(serial, "secure", "user_setup_complete", "1", 12)]
    assert phone.switched == []
    stored = json.loads(android_devices.DEVICES_FILE.read_text())
    assert any(user["user_id"] == 12 for user in stored["devices"][serial]["last_scan"]["users"])


@pytest.mark.parametrize("focus,close", [
    ("com.google.android.setupwizard/.user.UserWarningActivity", True),
    ("com.android.setupwizard/.SetupWizardActivity", True),
    ("com.facebook.katana/.LoginActivity", False),
    (None, False),
])
def test_existing_user_setup_closes_only_a_visible_welcome_wizard(focus, close):
    client = Mock(spec=adb_client.AdbClient)
    client.setting.return_value = "1"
    client.current_user.return_value = 10
    client.focused_window.return_value = focus
    android_devices.complete_android_user_setup(client, "PIXEL", 10)
    client.put_setting.assert_called_once_with("PIXEL", "secure", "user_setup_complete", "1", 10)
    if close:
        assert client.shell.call_args_list == [
            call("PIXEL", "am", "force-stop", "--user", "10", focus.split("/")[0]),
            call("PIXEL", "input", "keyevent", "3"),
        ]
    else:
        client.shell.assert_not_called()


def test_setup_skip_never_changes_owner_or_another_users_screen():
    client = Mock(spec=adb_client.AdbClient)
    with pytest.raises(AdbError, match="owner"):
        android_devices.complete_android_user_setup(client, "PIXEL", 0)
    client.put_setting.assert_not_called()
    client.setting.return_value = "1"
    client.current_user.return_value = 0
    android_devices.complete_android_user_setup(client, "PIXEL", 11)
    client.focused_window.assert_not_called()
    client.shell.assert_not_called()


def test_list_devices_reports_missing_adb(phone, monkeypatch):
    class Missing(FakeAdb):
        def devices(self):
            raise adb_client.AdbUnavailable("ADB is not installed.")
    monkeypatch.setattr(android_devices, "_client", lambda: Missing())
    result = android_devices.list_devices()
    assert result == {"adb_available": False, "adb_error": "ADB is not installed.", "devices": []}


def test_scan_lists_users_and_import_creates_disabled_android_profile(phone):
    device = android_devices.scan_device("R58M12345ABC")
    users = {row["user_id"]: row for row in device["last_scan"]["users"]}
    assert users[0]["control_user"] is True
    assert users[10]["current"] is True and users[10]["facebook_installed"] is True
    assert users[11]["facebook_installed"] is False
    assert device["android_version"] == "12"

    profile = android_devices.import_user("R58M12345ABC", 10, "  News page ", "Phones")
    assert profile["name"] == "News page" and profile["group"] == "Phones"
    assert profile["runtime"] == {"kind": "android_user", "device_serial": "R58M12345ABC",
                                  "android_user_id": 10, "android_user_serial": 12,
                                  "android_user_name": "FB Page: News"}
    assert profile["automation"]["enabled"] is False
    assert profile["network"]["mode"] == "direct"
    assert android_devices.is_android_profile(profile["id"])

    rescan = android_devices.scan_device("R58M12345ABC")
    linked = {row["user_id"]: row["profile_id"] for row in rescan["last_scan"]["users"]}
    assert linked[10] == profile["id"] and linked[11] is None
    assert android_devices.list_devices()["devices"][0]["profile_count"] == 1

    with pytest.raises(ValueError, match="already linked"):
        android_devices.import_user("R58M12345ABC", 10, "Again")


def test_recreated_user_with_reused_id_is_not_linked_to_old_profile(phone):
    android_devices.scan_device("R58M12345ABC")
    old = android_devices.import_user("R58M12345ABC", 10, "Old")
    phone.serials = {**phone.serials, 10: 99}
    rescan = android_devices.scan_device("R58M12345ABC")
    row = next(row for row in rescan["last_scan"]["users"] if row["user_id"] == 10)
    assert row["profile_id"] is None
    new = android_devices.import_user("R58M12345ABC", 10, "New")
    assert new["id"] != old["id"]


def test_control_user_and_unscanned_phone_cannot_be_imported(phone):
    with pytest.raises(android_devices.DeviceNotFound):
        android_devices.import_user("R58M12345ABC", 10, "Before scan")
    android_devices.scan_device("R58M12345ABC")
    with pytest.raises(ValueError, match="control user"):
        android_devices.import_user("R58M12345ABC", 0, "Owner")
    with pytest.raises(android_devices.DeviceNotFound):
        android_devices.import_user("R58M12345ABC", 42, "Missing")


def test_scan_rejects_unauthorized_and_disconnected_phones(phone):
    with pytest.raises(HTTPException) as error:
        devices.scan_device("0123456789ABCDEF")
    assert error.value.status_code == 409 and "Allow USB debugging" in error.value.detail
    with pytest.raises(HTTPException) as error:
        devices.scan_device("NOTCONNECTED")
    assert error.value.status_code == 404


def test_phone_with_accounts_cannot_be_forgotten(phone):
    android_devices.scan_device("R58M12345ABC")
    android_devices.import_user("R58M12345ABC", 10, "Account")
    with pytest.raises(ValueError, match="accounts"):
        android_devices.forget_device("R58M12345ABC")
    android_devices.list_devices()
    android_devices.forget_device("192.168.1.40:5555")
    assert "192.168.1.40:5555" not in android_devices.load_store()["devices"]


def test_switch_user_waits_until_user_is_running_awake_and_unlocked(phone):
    result = devices.switch_user("R58M12345ABC", devices.SwitchUserRequest(user_id=11))
    assert result == {"current_user": 11, "running": True, "screen_awake": True,
                      "lock_screen_showing": False, "ready": True, "reason": None}
    assert phone.switched == [11]
    assert (phone.woken, phone.dismissed) == (1, 1)
    assert phone.settings[(11, "secure", "user_setup_complete")] == "1"
    with pytest.raises(HTTPException) as error:
        devices.switch_user("R58M12345ABC", devices.SwitchUserRequest(user_id=42))
    assert error.value.status_code == 404


def test_opening_a_users_screen_clears_its_existing_welcome_wizard(phone):
    phone.focus = "com.google.android.setupwizard/.user.UserWarningActivity"
    result = android_devices.switch_user("R58M12345ABC", 11)
    assert result["ready"] is True
    assert phone.shell_calls == [
        ("R58M12345ABC", "am", "force-stop", "--user", "11", "com.google.android.setupwizard"),
        ("R58M12345ABC", "input", "keyevent", "3"),
    ]


def test_opening_owner_screen_leaves_its_setup_untouched(phone):
    result = android_devices.switch_user("R58M12345ABC", 0)
    assert result["ready"] is True
    assert phone.settings == {}
    assert phone.shell_calls == []


def test_manager_switch_uses_account_opening_for_a_linked_user(phone, monkeypatch):
    android_devices.scan_device("R58M12345ABC")
    profile = android_devices.import_user("R58M12345ABC", 11, "Account")
    opened = {"switch": {"current_user": 11, "ready": True, "reason": None},
              "ready": False, "reason": "Proxy did not verify"}
    safe_open = Mock(return_value=opened)
    monkeypatch.setattr(android_network, "open_account", safe_open)
    result = android_devices.switch_user("R58M12345ABC", 11)
    safe_open.assert_called_once_with(profile["id"])
    assert result["ready"] is False and result["reason"] == "Proxy did not verify"
    assert phone.switched == []


def test_focused_window_ignores_null_focus_from_another_display():
    client = adb_client.AdbClient()
    client.shell = Mock(return_value=("  mCurrentFocus=null\n"
                                    "  mCurrentFocus=Window{123 u11 com.google.android.setupwizard/.user.UserWarningActivity}\n"))
    assert client.focused_window("PIXEL") == "com.google.android.setupwizard/.user.UserWarningActivity"
    assert "-m1" not in client.shell.call_args.args[1]


def test_switch_user_reports_secure_lock_screen_instead_of_claiming_ready(phone, monkeypatch):
    monkeypatch.setattr(android_devices, "SWITCH_READY_TIMEOUT_S", 0.05)
    phone.secure_lock = True
    result = android_devices.switch_user("R58M12345ABC", 11)
    assert result["ready"] is False
    assert result["lock_screen_showing"] is True
    assert "lock screen" in result["reason"]


def test_unreadable_lock_screen_state_is_not_ready(phone, monkeypatch):
    monkeypatch.setattr(android_devices, "SWITCH_READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(phone, "lock_screen_showing", lambda serial: None)
    result = android_devices.switch_user("R58M12345ABC", 11)
    assert result["ready"] is False
    assert result["reason"] == "Couldn’t read the phone’s lock screen state."


def test_concurrent_switch_on_the_same_phone_is_rejected(phone):
    lock = android_devices._switch_locks.setdefault("R58M12345ABC", android_devices.threading.Lock())
    lock.acquire()
    try:
        with pytest.raises(HTTPException) as error:
            devices.switch_user("R58M12345ABC", devices.SwitchUserRequest(user_id=11))
        assert error.value.status_code == 409
        assert phone.switched == []
    finally:
        lock.release()


def test_android_profiles_never_reach_docker_or_edit_runtime_settings(phone, monkeypatch):
    android_devices.scan_device("R58M12345ABC")
    profile = android_devices.import_user("R58M12345ABC", 10, "Account")

    def fail(*args, **kwargs):
        raise AssertionError("Docker must not be called for Android profiles")
    monkeypatch.setattr(docker_service, "asyncio_subprocess_run", fail)
    import asyncio
    with pytest.raises(ValueError, match="Android phone"):
        asyncio.run(docker_service.run_profile_action(profile["id"], "start"))

    from backend.services import automation_service
    with pytest.raises(ValueError, match="Android phone"):
        asyncio.run(automation_service.run_profile_lifecycle_action(profile["id"], "start"))

    with pytest.raises(HTTPException) as error:
        profiles.update_profile(profile["id"], ProfileUpdateRequest(network={"mode": "direct"}))
    assert error.value.status_code == 400
    renamed = profiles.update_profile(profile["id"], ProfileUpdateRequest(name="Renamed", group="Phones"))
    assert renamed["name"] == "Renamed"
    assert renamed["automation"]["enabled"] is False
    assert renamed["runtime"]["kind"] == "android_user"


def test_import_reserves_one_free_proxy_for_the_phone_account(phone, tmp_path, monkeypatch):
    pool = tmp_path / "proxy_pool.json"
    pool.write_text(json.dumps([{"id": "p1", "host": "10.0.0.1", "port": 1080, "username": "u", "password": "pw",
                                 "type": "socks5", "assigned": False, "profile_id": None}]))
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool)
    android_devices.scan_device("R58M12345ABC")
    profile = android_devices.import_user("R58M12345ABC", 10, "News", proxy_id="p1")
    assert profile["network"]["mode"] == "pool" and profile["network"]["proxy_id"] == "p1"
    # Reserved only: the phone keeps its own network until the companion app is set up.
    assert profile["android_network"] == {"status": "not_set_up"}
    assert json.loads(pool.read_text())[0]["profile_id"] == profile["id"]

    with pytest.raises(ValueError, match="limit per proxy"):
        android_devices.import_user("R58M12345ABC", 11, "Second", proxy_id="p1")
    # The refused import leaves no account behind.
    assert len(android_devices.linked_profiles()) == 1


def _pool_with_timezones(tmp_path, monkeypatch, zones):
    pool = tmp_path / "proxy_pool.json"
    pool.write_text(json.dumps([{"id": proxy_id, "host": "10.0.0.1", "port": 1080, "type": "socks5",
                                 "assigned": False, "profile_id": None, "timezone": zone}
                                for proxy_id, zone in zones.items()]))
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool)


def test_phone_timezone_is_set_only_when_every_account_matches(phone, tmp_path, monkeypatch):
    _pool_with_timezones(tmp_path, monkeypatch, {"vn": "Asia/Ho_Chi_Minh", "us": "America/New_York"})
    android_devices.scan_device("R58M12345ABC")
    assert android_devices.list_devices()["devices"][0]["health"]["timezone"] == {"zone": "Asia/Bangkok", "auto": True}
    android_devices.import_user("R58M12345ABC", 10, "Vietnam", proxy_id="vn")

    # Phnom Penh shares Vietnam's UTC+7, so it matches; automatic detection is turned off.
    assert android_devices.set_timezone("R58M12345ABC", "Asia/Phnom_Penh") == {"zone": "Asia/Phnom_Penh", "auto": False}

    android_devices.import_user("R58M12345ABC", 11, "New York", proxy_id="us")
    # One account at a time (the default) sets the time per account, so a mixed phone is allowed.
    assert android_devices.list_devices()["devices"][0]["one_account_at_a_time"] is True
    android_devices.set_timezone("R58M12345ABC", "America/New_York")
    android_devices.set_timezone("R58M12345ABC", "Asia/Phnom_Penh")
    # With accounts running side by side, one phone time must fit them all.
    android_devices.update_device_settings("R58M12345ABC", False)
    with pytest.raises(ValueError, match="different time: Vietnam"):
        android_devices.set_timezone("R58M12345ABC", "America/New_York")
    assert phone.zone == "Asia/Phnom_Penh"
    with pytest.raises(ValueError, match="Unknown timezone"):
        android_devices.set_timezone("R58M12345ABC", "Mars/Olympus")


def test_switching_from_the_app_only_hides_the_phones_switcher(phone):
    android_devices.list_devices()
    entry = android_devices.list_devices()["devices"][0]
    assert entry["switch_from_app_only"] is True and entry["health"]["user_switcher"] is True
    android_devices.update_device_settings("R58M12345ABC", app_only=True)
    assert phone.switcher is False
    android_devices.update_device_settings("R58M12345ABC", app_only=False)
    assert phone.switcher is True
    assert android_devices.list_devices()["devices"][0]["switch_from_app_only"] is False


@pytest.mark.parametrize('usb_connected', [True, False])
def test_adb_groups_usb_and_wifi_by_hardware_serial_and_routes_commands(monkeypatch, usb_connected):
    client = adb_client.AdbClient(binary='adb')
    commands = []
    usb, wifi = 'PIXEL123', '192.168.1.40:5555'

    def execute(args, timeout=None):
        commands.append(args)
        if args == ['devices', '-l']:
            return ('List of devices attached\n' +
                    (f'{usb} device usb:1-2 model:Pixel_7_Pro\n' if usb_connected else '') +
                    f'{wifi} device model:Pixel_7_Pro\n')
        if args[-2:] == ['getprop', 'ro.serialno']:
            return usb + '\n'
        return '0\n'

    monkeypatch.setattr(client, '_execute', execute)
    devices_list = client.devices()
    assert len(devices_list) == 1
    assert devices_list[0]['serial'] == usb
    assert devices_list[0]['connection'] == ('usb' if usb_connected else 'wifi')
    assert wifi in devices_list[0]['aliases']
    client.current_user(usb)
    assert commands[-1] == ['-s', usb if usb_connected else wifi, 'shell', 'am', 'get-current-user']
    client.current_user(wifi)
    assert commands[-1][1] == (usb if usb_connected else wifi)


def test_same_model_phones_are_kept_separate_when_identity_is_unreadable(monkeypatch):
    client = adb_client.AdbClient(binary='adb')

    def execute(args, timeout=None):
        if args == ['devices', '-l']:
            return ('List of devices attached\n'
                    'USB123 device usb:1-2 model:Pixel_7_Pro\n'
                    '192.168.1.40:5555 device model:Pixel_7_Pro\n')
        raise AdbError('Cannot read identity')

    monkeypatch.setattr(client, '_execute', execute)
    assert len(client.devices()) == 2


def test_restart_reboots_the_phone(phone):
    restarted = []
    phone.reboot = restarted.append
    android_devices.restart_phone('R58M12345ABC')
    assert restarted == ['R58M12345ABC']


def test_restart_is_refused_while_a_job_holds_the_phone(phone):
    restarted = []
    phone.reboot = restarted.append
    assert android_devices.hold_for_job('R58M12345ABC', 'post:1')
    try:
        with pytest.raises(AdbError, match='queued post'):
            android_devices.restart_phone('R58M12345ABC')
    finally:
        android_devices.release_job('R58M12345ABC')
    assert restarted == []


def test_restart_is_refused_during_a_user_switch(phone):
    restarted = []
    phone.reboot = restarted.append
    with android_devices.phone_lock('R58M12345ABC'):
        with pytest.raises(AdbError, match='already switching'):
            android_devices.restart_phone('R58M12345ABC')
    assert restarted == []


def test_reboot_targets_only_the_chosen_phone(monkeypatch):
    client = adb_client.AdbClient(binary='adb')
    commands = []

    def execute(args, timeout=None):
        commands.append(args)
        return 'List of devices attached\nUSB123 device usb:1-2 model:Pixel\n' if args == ['devices', '-l'] else 'USB123\n'

    monkeypatch.setattr(client, '_execute', execute)
    client.reboot('USB123')
    assert commands[-1] == ['-s', 'USB123', 'reboot']


WIFI_NAME = 'adb-28131FDH300G60-UJrerS._adb-tls-connect._tcp'


def test_wifi_name_identifies_the_phone_when_its_serial_cannot_be_read(monkeypatch):
    client = adb_client.AdbClient(binary='adb')

    def execute(args, timeout=None):
        if args == ['devices', '-l']:
            return ('List of devices attached\n'
                    '28131FDH300G60 device usb:1-2 model:Pixel_7_Pro\n'
                    f'{WIFI_NAME} device model:Pixel_7_Pro\n')
        if args[1] == '28131FDH300G60':
            return '28131FDH300G60\n'
        raise AdbError('Cannot read identity')

    monkeypatch.setattr(client, '_execute', execute)
    devices_list = client.devices()
    assert [device['serial'] for device in devices_list] == ['28131FDH300G60']
    assert devices_list[0]['connection'] == 'usb' and WIFI_NAME in devices_list[0]['aliases']


@pytest.mark.parametrize('name', ['192.168.1.40:5555', 'adb-OTHER-abc._adb-tls-connect._tcp', 'adb-x', 'R58M12345ABC'])
def test_other_phones_are_not_folded_into_this_one(phone, monkeypatch, name):
    usb = '28131FDH300G60'
    android_devices.DEVICES_FILE.write_text(json.dumps({'devices': {
        usb: {'serial': usb, 'label': 'Mine'}, name: {'serial': name, 'label': 'Other'}}}))
    monkeypatch.setattr(phone, 'devices', lambda: [
        {'serial': usb, 'aliases': [usb], 'state': 'device', 'connection': 'usb', 'model': 'Pixel'}])
    android_devices.list_devices()
    assert set(android_devices.load_store()['devices']) == {usb, name}


def test_saved_wifi_entry_folds_into_its_phone_without_a_wifi_connection(phone, monkeypatch):
    usb = '28131FDH300G60'
    android_devices.DEVICES_FILE.write_text(json.dumps({'devices': {
        usb: {'serial': usb, 'label': 'My phone', 'role': 'primary', 'pin': None},
        WIFI_NAME: {'serial': WIFI_NAME, 'label': 'Pixel 7 Pro', 'switch_from_app_only': False},
    }}))
    profile = android_devices.PROFILES_DIR / 'profile_001'
    profile.mkdir()
    (profile / 'config.json').write_text(json.dumps(
        {'runtime': {'kind': 'android_user', 'device_serial': WIFI_NAME, 'android_user_id': 10}}))
    monkeypatch.setattr(phone, 'devices', lambda: [
        {'serial': usb, 'aliases': [usb], 'state': 'device', 'connection': 'usb', 'model': 'Pixel'}])
    result = android_devices.list_devices()['devices']
    assert [device['serial'] for device in result] == [usb]
    assert result[0]['label'] == 'My phone'
    assert result[0]['switch_from_app_only'] is False  # Only what the phone's record lacked is kept.
    assert result[0]['profile_count'] == 1
    saved = android_devices.load_store()['devices'][usb]
    assert saved['role'] == 'primary' and saved['aliases'] == sorted([usb, WIFI_NAME])
    assert android_devices.canonical_serial(WIFI_NAME) == usb


def test_inventory_merges_saved_transports_without_losing_accounts_or_settings(phone, monkeypatch):
    usb, wifi = 'R58M12345ABC', '192.168.1.40:5555'
    android_devices.DEVICES_FILE.write_text(json.dumps({'devices': {
        usb: {'serial': usb, 'label': 'My phone', 'pin': '1234'},
        wifi: {'serial': wifi, 'label': 'Pixel', 'switch_from_app_only': False},
    }}))
    profile = android_devices.PROFILES_DIR / 'profile_001'
    profile.mkdir()
    original = {'runtime': {'kind': 'android_user', 'device_serial': wifi, 'android_user_id': 10}}
    (profile / 'config.json').write_text(json.dumps(original))
    monkeypatch.setattr(phone, 'devices', lambda: [
        {'serial': usb, 'aliases': [usb, wifi], 'state': 'device', 'connection': 'usb', 'model': 'Pixel'}])
    result = android_devices.list_devices()['devices']
    assert len(result) == 1
    assert result[0]['label'] == 'My phone'
    assert result[0]['pin_set'] is True
    assert result[0]['switch_from_app_only'] is False
    assert result[0]['profile_count'] == 1
    assert set(android_devices.load_store()['devices']) == {usb}
    assert android_devices.read_profile_runtime('profile_001')['device_serial'] == usb
    assert json.loads((profile / 'config.json').read_text()) == original
    # Unplugging Wi-Fi must retain its remembered alias for accounts and locks.
    monkeypatch.setattr(phone, 'devices', lambda: [
        {'serial': usb, 'aliases': [usb], 'state': 'device', 'connection': 'usb', 'model': 'Pixel'}])
    assert android_devices.list_devices()['devices'][0]['profile_count'] == 1
    assert wifi in android_devices.load_store()['devices'][usb]['aliases']
    # A job accepted before alias discovery must still reserve the physical phone.
    android_devices._job_holders[wifi] = 'post:old'
    assert android_devices.job_on(usb) == 'post:old'
    assert not android_devices.hold_for_job(usb, 'post:new')
    android_devices.release_job(usb)
    assert wifi not in android_devices._job_holders
    assert android_devices.hold_for_job(wifi, 'post:1')
    try:
        assert android_devices.job_on(usb) == 'post:1'
        assert not android_devices.hold_for_job(usb, 'post:2')
        with pytest.raises(AdbError, match='queued post'):
            with android_devices.phone_lock(usb):
                pass
    finally:
        android_devices.release_job(wifi)


def test_owner_live_view_switches_to_user_zero(phone, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.services import android_live
    monkeypatch.setenv('MANAGER_API_TOKEN', 'owner-view-token')

    async def no_screen(serial):
        raise android_live.LiveViewError('test screen connection')

    monkeypatch.setattr(android_live, 'open_session', no_screen)
    app = FastAPI()
    app.include_router(devices.router)
    with TestClient(app).websocket_connect(
        '/api/devices/R58M12345ABC/live?user_id=0&access_token=owner-view-token'
    ) as ws:
        message = ws.receive_json()
        statuses = []
        while message['type'] == 'status':
            statuses.append(message['message'])
            message = ws.receive_json()
        assert message == {'type': 'error', 'message': 'test screen connection'}
    assert statuses[0] == 'Switching the phone to this user…'
    assert statuses[-1] == 'Starting the screen stream…'
    assert phone.switched == [0]
    assert phone.settings == {}
