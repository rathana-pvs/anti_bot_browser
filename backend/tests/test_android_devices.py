import json

import pytest
from fastapi import HTTPException

from backend.models.profile import ProfileUpdateRequest
from backend.routers import devices, profiles
from backend.services import adb_client, android_devices, docker_service
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


@pytest.mark.parametrize("serial", ["", "a b", "x;rm -rf /", "a" * 65])
def test_serial_is_validated(serial):
    with pytest.raises(ValueError):
        adb_client.validate_serial(serial)


class FakeAdb:
    def __init__(self):
        self.switched = []
        self.users_list = adb_client.parse_users(USERS_OUTPUT)
        self.serials = adb_client.parse_user_serials(DUMPSYS_USER)

    def devices(self):
        return adb_client.parse_devices(DEVICES_OUTPUT)

    def properties(self, serial):
        return {"manufacturer": "samsung", "model": "SM-G973F", "android_version": "12", "sdk": 31}

    def users(self, serial):
        return self.users_list

    def user_serials(self, serial):
        return self.serials

    def current_user(self, serial):
        return 10

    def max_users(self, serial):
        return 4

    def package_installed(self, serial, user_id, package=adb_client.FACEBOOK_PACKAGE):
        return user_id != 11

    def package_version(self, serial, package=adb_client.FACEBOOK_PACKAGE):
        return "450.0.0.40.109"

    def battery(self, serial):
        return {"level": 80, "temperature_c": 30.0, "charging": True}

    def free_storage_mb(self, serial):
        return 2048

    def switch_user(self, serial, user_id):
        self.switched.append(user_id)


@pytest.fixture
def phone(tmp_path, monkeypatch):
    fake = FakeAdb()
    root = tmp_path / "profiles"
    root.mkdir()
    monkeypatch.setattr(android_devices, "_client", lambda: fake)
    monkeypatch.setattr(android_devices, "DEVICES_FILE", tmp_path / "android_devices.json")
    monkeypatch.setattr(android_devices, "PROFILES_DIR", root)
    monkeypatch.setattr(profiles, "PROFILES_DIR", root)
    return fake


def test_list_devices_registers_authorized_phones_with_health(phone):
    result = android_devices.list_devices()
    assert result["adb_available"] is True
    by_serial = {device["serial"]: device for device in result["devices"]}
    assert set(by_serial) == {"R58M12345ABC", "192.168.1.40:5555", "0123456789ABCDEF"}
    assert by_serial["R58M12345ABC"]["health"]["battery"]["level"] == 80
    # Unauthorized phones are shown but not remembered or queried.
    assert by_serial["0123456789ABCDEF"]["health"] is None
    stored = json.loads(android_devices.DEVICES_FILE.read_text())["devices"]
    assert "0123456789ABCDEF" not in stored


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


def test_switch_user_checks_the_user_exists(phone):
    assert devices.switch_user("R58M12345ABC", devices.SwitchUserRequest(user_id=11)) == {"current_user": 10}
    assert phone.switched == [11]
    with pytest.raises(HTTPException) as error:
        devices.switch_user("R58M12345ABC", devices.SwitchUserRequest(user_id=42))
    assert error.value.status_code == 404


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
