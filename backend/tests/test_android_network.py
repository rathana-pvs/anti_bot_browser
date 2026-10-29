import json

import pytest

from backend.services import adb_client, android_devices, android_network, proxy_service

PROXY = {"id": "la", "host": "9.142.198.36", "port": 5703, "exit_ip": "9.142.198.36", "assigned": False, "profile_id": None}
STATUS = {"ok": True, "version": "0.2.0-poc", "service_running": True, "tunnel_up": True, "engine_running": True,
          "proxy": "9.142.198.36:5703", "always_on": True, "lockdown": True, "last_error": None}
# Recorded from Android user 11 on a Pixel 10 Pro XL.
CHECK = {"ok": True, "dns": {"address": "104.20.44.133", "via_tunnel": False},
         "exit": {"ip": "9.142.198.36", "country": "United States", "timezone": "America/Los_Angeles"},
         "trace_ipv4": {"ip": "9.142.198.36", "location": "US"}, "trace_ipv6": {"error": "Connection reset"}}


def reply(payload):
    return "Result: Bundle[{json=" + json.dumps(payload) + "}]\n"


class FakePhone:
    def __init__(self):
        self.replies = {"status": reply(STATUS), "check": reply(CHECK)}
        self.calls = []

    def shell(self, serial, *command, timeout=None):
        method = command[command.index("--method") + 1]
        self.calls.append((serial, command[command.index("--user") + 1], method))
        return self.replies[method]


@pytest.fixture
def account(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    (root / "profile_020").mkdir(parents=True)
    (root / "profile_020" / "config.json").write_text(json.dumps({
        "id": "profile_020", "name": "1", "network": {"mode": "pool", "proxy_id": "la"},
        "runtime": {"kind": "android_user", "device_serial": "PIXEL", "android_user_id": 11},
        "android_network": {"status": "not_set_up"}}))
    pool = tmp_path / "pool.json"
    pool.write_text(json.dumps([PROXY]))
    monkeypatch.setattr(android_network, "PROFILES_DIR", root)
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool)
    phone = FakePhone()
    monkeypatch.setattr(android_network, "_client", lambda: phone)
    return phone, root / "profile_020" / "config.json"


def test_verify_checks_inside_the_android_user_and_saves_the_result(account):
    phone, config = account
    assert android_network.network_status("profile_020")["state"] == "tunnel_on"
    result = android_network.verify("profile_020")
    assert (result["status"], result["exit_ip"], result["exit_timezone"]) == ("verified", "9.142.198.36", "America/Los_Angeles")
    assert ("PIXEL", "11", "check") in phone.calls
    assert json.loads(config.read_text())["android_network"]["status"] == "verified"


@pytest.mark.parametrize("change, reason", [
    ({"proxy": "1.2.3.4:1080"}, "uses proxy 1.2.3.4:1080"),
    ({"lockdown": False}, "kill switch"),
    ({"tunnel_up": False}, "not running"),
])
def test_wrong_setup_is_blocked_without_running_the_check(account, change, reason):
    phone, _ = account
    phone.replies["status"] = reply({**STATUS, **change})
    result = android_network.verify("profile_020")
    assert result["status"] == "blocked" and reason in result["reason"]
    assert all(method != "check" for *_, method in phone.calls)


@pytest.mark.parametrize("change, reason", [
    ({"exit": {"ip": "203.0.113.9"}}, "leaves from 203.0.113.9"),
    ({"trace_ipv4": {"ip": "203.0.113.9"}}, "second check"),
    ({"trace_ipv6": {"ip": "2001:db8::1"}}, "IPv6"),
    ({"exit": {"error": "timeout"}}, "could not be measured"),
])
def test_any_traffic_outside_the_proxy_blocks(account, change, reason):
    phone, _ = account
    phone.replies["check"] = reply({**CHECK, **change})
    result = android_network.verify("profile_020")
    assert result["status"] == "blocked" and reason in result["reason"]


def test_missing_companion_app_is_not_set_up(account):
    phone, config = account
    phone.replies["status"] = "Error while accessing provider:com.isolatedbrowser.agent.control\n"
    result = android_network.verify("profile_020")
    assert result["status"] == "not_set_up"
    assert json.loads(config.read_text())["android_network"]["status"] == "not_set_up"


def test_bad_account_ids_are_rejected(account):
    with pytest.raises(android_devices.DeviceNotFound):
        android_network.network_status("../etc")


class FakeTwoUserPhone(FakePhone):
    """Users 11 and 12 running; 12 is on screen and the phone is on another account's time."""

    def __init__(self):
        super().__init__()
        self.running = {0, 11, 12}
        self.current = 12
        self.zone, self.auto = "America/New_York", False
        self.stopped = []
        self.dns_ready_after = 0

    def users(self, serial):
        return [adb_client.AndroidUser(uid, str(uid), 0, uid in self.running) for uid in (0, 11, 12)]

    def switch_user(self, serial, user_id):
        self.running.add(user_id)
        self.current = user_id

    def current_user(self, serial):
        return self.current

    def screen_awake(self, serial):
        return True

    def lock_screen_showing(self, serial):
        return False

    def stop_user(self, serial, user_id):
        assert user_id != self.current, "Android refuses to stop the user on screen"
        self.running.discard(user_id)
        self.stopped.append(user_id)

    def timezone_state(self, serial):
        return {"zone": self.zone, "auto": self.auto}

    switcher = True  # someone turned the phone's switcher back on in Settings

    def user_switcher_enabled(self, serial):
        return self.switcher

    def set_user_switcher(self, serial, enabled):
        self.switcher = enabled

    def set_timezone(self, serial, zone):
        self.zone, self.auto = zone, False

    def shell(self, serial, *command, timeout=None):
        if command[command.index("--method") + 1] == "check" and self.dns_ready_after > 0:
            self.dns_ready_after -= 1
            self.calls.append((serial, "11", "check"))
            return reply({**CHECK, "exit": {"error": 'Unable to resolve host "ipwho.is"'}})
        return super().shell(serial, *command, timeout=timeout)


@pytest.fixture
def two_users(account, tmp_path, monkeypatch):
    phone = FakeTwoUserPhone()
    monkeypatch.setattr(android_network, "_client", lambda: phone)
    monkeypatch.setattr(android_devices, "DEVICES_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(android_devices, "SWITCH_POLL_S", 0)
    monkeypatch.setattr(android_network, "OPEN_RETRY_S", 0)
    return phone


def test_open_account_stops_other_users_sets_time_and_verifies(two_users, account):
    _, config = account
    pool = json.loads(proxy_service.PROXY_POOL_FILE.read_text())
    pool[0]["timezone"] = "America/Los_Angeles"
    proxy_service.PROXY_POOL_FILE.write_text(json.dumps(pool))
    two_users.dns_ready_after = 2  # the first checks run before Private DNS is back

    result = android_network.open_account("profile_020")

    assert result["ready"] is True and result["switch"]["current_user"] == 11
    assert two_users.stopped == [12] and two_users.running == {0, 11}
    assert result["timezone_set"] == "America/Los_Angeles" and two_users.zone == "America/Los_Angeles"
    assert result["network"]["status"] == "verified"
    assert json.loads(config.read_text())["android_network"]["status"] == "verified"
    assert two_users.switcher is False


def test_open_account_side_by_side_leaves_other_users_and_time(two_users, account):
    android_devices._save_store({"devices": {"PIXEL": {"serial": "PIXEL", "label": "Pixel", "one_account_at_a_time": False}}})
    result = android_network.open_account("profile_020")
    assert result["ready"] is True and two_users.stopped == [] and result["timezone_set"] is None
    assert two_users.zone == "America/New_York"


def test_wrong_proxy_fails_at_once_without_waiting(two_users, account):
    two_users.replies["status"] = reply({**STATUS, "proxy": "1.2.3.4:1080"})
    result = android_network.open_account("profile_020")
    assert result["ready"] is False and "1.2.3.4:1080" in result["reason"]
    assert all(method != "check" for *_, method in two_users.calls)
