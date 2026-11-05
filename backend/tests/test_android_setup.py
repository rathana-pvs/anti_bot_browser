import base64
import hashlib
import json
import shutil

import pytest

from backend.services import (adb_client, android_devices, android_network, android_ownership, android_setup,
                              proxy_service)
from backend.services.adb_client import AdbError, AndroidUser

PROXY = {"id": "la", "host": "9.142.198.36", "port": 5703, "username": "u1", "password": "secret",
         "type": "socks5", "exit_ip": "9.142.198.36", "timezone": "America/Los_Angeles",
         "assigned": True, "profile_id": "profile_020"}
AGENT = "com.isolatedbrowser.agent"
FACEBOOK = adb_client.FACEBOOK_PACKAGE


def reply(payload):
    return "Result: Bundle[{json=" + json.dumps(payload) + "}]\n"


class FakeAgent:
    """The companion app in one Android user."""

    def __init__(self):
        self.proxy = None
        self.active = None
        self.account = {}
        self.pin = None
        self.reachable = {("9.142.198.36", 5703, "u1", "secret")}

    def call(self, phone, user_id, method, arg):
        key = lambda p: (p["host"], int(p["port"]), p.get("username") or "", p.get("password") or "")
        if method == "status":
            running = phone.vpn_running(user_id)
            return {"ok": True, "version": "0.3.0", "service_running": running, "tunnel_up": running,
                    "engine_running": running, "proxy": self.active if running else None,
                    "always_on": running, "lockdown": running,
                    "saved_proxy": f"{self.proxy['host']}:{self.proxy['port']}" if self.proxy else None,
                    "saved_username": (self.proxy or {}).get("username"),
                    "account": {**self.account, "pin_set": self.pin is not None}}
        if method == "set_config":
            self.proxy, self.active = arg, f"{arg['host']}:{arg['port']}"
            return {"ok": True}
        if method == "same_proxy":
            return {"ok": True, "same": bool(self.proxy) and key(self.proxy) == key(arg)}
        if method == "try_config":
            if key(arg) in self.reachable:
                self.proxy, self.active = arg, f"{arg['host']}:{arg['port']}"
                return {"ok": True, "applied": True}
            return {"ok": True, "applied": False, "reverted": True}
        if method == "check":
            exit_ip = self.proxy["host"] if self.proxy and key(self.proxy) in self.reachable else None
            return {"ok": True, "exit": {"ip": exit_ip, "timezone": "America/Los_Angeles"} if exit_ip else {"error": "timeout"},
                    "trace_ipv4": {"ip": exit_ip}, "trace_ipv6": {"error": "unreachable"}}
        if method == "set_account":
            self.account.update(arg)
            return {"ok": True}
        if method == "export":
            if self.pin and arg.get("pin") != self.pin:
                return {"ok": False, "error": "wrong_pin"}
            return {"ok": True, "account": self.account, "proxy": self.proxy}
        if method in ("set_pin", "clear_pin"):
            if self.pin and arg.get("current_pin") != self.pin and not (arg.get("proxy") and key(arg["proxy"]) == key(self.proxy)):
                return {"ok": False, "error": "wrong_pin"}
            self.pin = arg.get("pin") if method == "set_pin" else None
            return {"ok": True}
        raise AssertionError(method)


class FakePhone:
    """Enough of AdbClient for setup and ownership, with one phone's state."""

    def __init__(self):
        self.users_running = {0: True, 11: True}
        self.current = 0
        self.packages = {(0, FACEBOOK)}
        self.agents = {11: FakeAgent()}
        self.settings = {}
        self.appops = set()
        self.files = {}
        self.installs = []
        self.next_user = 12

    def devices(self):
        return [{"serial": "PIXEL", "state": "device", "connection": "usb", "model": "Pixel"}]

    # Users
    def users(self, serial):
        return [AndroidUser(uid, f"User {uid}", 0, running) for uid, running in self.users_running.items()]

    def user_serials(self, serial):
        return {uid: 100 + uid for uid in self.users_running}

    def current_user(self, serial):
        return self.current

    def focused_window(self, serial):
        return "com.google.android.apps.nexuslauncher/.NexusLauncherActivity"

    def switch_user(self, serial, user_id):
        self.current = user_id

    def screen_awake(self, serial):
        return True

    def lock_screen_showing(self, serial):
        return False

    def start_user(self, serial, user_id):
        self.users_running[user_id] = True

    def stop_user(self, serial, user_id):
        assert user_id != self.current, "Android refuses to stop the user on screen"
        self.users_running[user_id] = False

    def create_user(self, serial, name):
        uid, self.next_user = self.next_user, self.next_user + 1
        self.users_running[uid] = False
        self.agents[uid] = FakeAgent()
        return uid

    def max_users(self, serial):
        return 8

    # Packages and settings
    def properties(self, serial):
        return {"manufacturer": "Google", "model": "Pixel", "android_version": "17", "sdk": 37}

    def package_on_phone(self, serial, package=FACEBOOK):
        return any(pkg == package for _, pkg in self.packages)

    def package_installed(self, serial, user_id, package=FACEBOOK):
        return (user_id, package) in self.packages

    def package_version(self, serial, package=FACEBOOK):
        return "0.3.0" if any(pkg == package for _, pkg in self.packages) else None

    def install_apk(self, serial, user_id, apk):
        if getattr(self, "verify_background_installs", False) and self.current != user_id:
            raise AdbError("Failure [INSTALL_FAILED_VERIFICATION_FAILURE: Install not allowed]")
        self.installs.append(user_id)
        self.packages.add((user_id, AGENT))

    def install_existing(self, serial, user_id, package):
        self.packages.add((user_id, package))

    def exempt_battery(self, serial, package):
        pass

    def appop_vpn_allowed(self, serial, user_id, package):
        return user_id in self.appops

    def allow_vpn(self, serial, user_id, package):
        self.appops.add(user_id)

    def setting(self, serial, namespace, key, user_id=None):
        return self.settings.get((user_id, namespace, key))

    def put_setting(self, serial, namespace, key, value, user_id=None):
        self.settings[(user_id, namespace, key)] = value

    def revoke_permission(self, serial, user_id, package, permission):
        pass

    def deny_permission(self, serial, user_id, package, permission):
        self.denied = getattr(self, "denied", set()) | {(user_id, package, permission)}

    def permission_state(self, serial, user_id, package, permission):
        if (user_id, package, permission) in getattr(self, "denied", set()) and not getattr(self, "denial_ignored", False):
            return False, {"USER_SET", "USER_FIXED"}
        return False, {"USER_SET"}

    def grant_permission(self, serial, user_id, package, permission):
        self.granted = getattr(self, "granted", set()) | {(user_id, package, permission)}

    def read_file(self, serial, path):
        return self.files.get(path)

    def write_file(self, serial, path, text):
        self.files[path] = text

    def vpn_running(self, user_id):
        return (self.users_running.get(user_id)
                and self.settings.get((user_id, "secure", "always_on_vpn_lockdown")) == "1"
                and user_id in self.appops)

    # Companion app over `content call`
    def shell(self, serial, *command, timeout=None):
        user_id = int(command[command.index("--user") + 1])
        method = command[command.index("--method") + 1]
        arg = json.loads(base64.b64decode(command[command.index("--arg") + 1])) if "--arg" in command else {}
        if not self.users_running.get(user_id) or (user_id, AGENT) not in self.packages:
            return "Error while accessing provider"
        return reply(self.agents[user_id].call(self, user_id, method, arg))


@pytest.fixture
def phone(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "agent.apk").write_bytes(b"apk")
    (vendor / "manifest.json").write_text(json.dumps({"version_name": "0.3.0", "version_code": 4,
                                                      "sha256": hashlib.sha256(b"apk").hexdigest()}))
    pool = tmp_path / "pool.json"
    pool.write_text(json.dumps([PROXY]))
    fake = FakePhone()
    for module in (android_devices, android_network, android_ownership, android_setup):
        monkeypatch.setattr(module, "PROFILES_DIR", root)
        monkeypatch.setattr(module, "_client", lambda: fake)
    monkeypatch.setattr(android_devices, "DEVICES_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(android_ownership, "IDENTITY_FILE", tmp_path / "identity.json")
    monkeypatch.setattr(android_setup, "AGENT_APK", vendor / "agent.apk")
    monkeypatch.setattr(android_setup, "AGENT_MANIFEST", vendor / "manifest.json")
    monkeypatch.setattr(android_setup, "POLL_S", 0)
    monkeypatch.setattr(android_network, "OPEN_RETRY_S", 0)
    monkeypatch.setattr(android_network, "OPEN_VERIFY_TIMEOUT_S", 0)
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool)
    fake.get_ready_runs = []
    monkeypatch.setattr(android_setup, "start_get_ready", lambda pid: fake.get_ready_runs.append(pid))
    (root / "profile_020").mkdir(parents=True)
    (root / "profile_020" / "config.json").write_text(json.dumps({
        "id": "profile_020", "name": "Anna", "group": "Shop",
        "network": android_devices._reserved_network(PROXY),
        "runtime": {"kind": "android_user", "device_serial": "PIXEL", "android_user_id": 11, "android_user_serial": 111},
        "android_network": {"status": "not_set_up"}}))
    return fake


def run_setup(profile_id="profile_020"):
    android_setup._run(android_setup._Context(profile_id))
    return android_setup.progress(profile_id)


def statuses(progress):
    return {step["key"]: step["status"] for step in progress["steps"]}


def test_setup_runs_every_step_then_waits_for_the_login(phone):
    progress = run_setup()
    assert progress["state"] == "waiting_login"
    assert all(status == "passed" for key, status in statuses(progress).items() if key not in ("login", "facebook_ready"))
    agent = phone.agents[11]
    assert agent.proxy["password"] == "secret" and agent.account["name"] == "Anna"
    assert (11, FACEBOOK) in phone.packages and phone.settings[(11, "secure", "location_mode")] == "0"
    assert (11, FACEBOOK, "android.permission.READ_MEDIA_IMAGES") in phone.granted
    assert phone.settings[(11, "system", "screen_off_timeout")] == "900000"
    assert phone.settings[(11, "secure", "user_setup_complete")] == "1"
    assert android_ownership.role_of(json.loads(phone.files[android_ownership.PHONE_FILE])) == "primary"
    assert android_setup.confirm_login("profile_020")["state"] == "done"
    assert phone.get_ready_runs == ["profile_020"]  # Facebook's first screens, in the background


def test_camera_microphone_and_notifications_are_denied_for_good(phone):
    run_setup()
    for name in ("CAMERA", "RECORD_AUDIO", "POST_NOTIFICATIONS"):
        assert (11, FACEBOOK, f"android.permission.{name}") in phone.denied


def test_a_denial_the_phone_did_not_keep_fails_the_facebook_step(phone):
    phone.denial_ignored = True
    progress = run_setup()
    assert statuses(progress)["facebook"] == "failed"
    assert "did not keep CAMERA denied" in next(s["reason"] for s in progress["steps"] if s["key"] == "facebook")


def test_get_ready_result_is_reported_on_its_step(phone, monkeypatch):
    from backend.services import android_jobs
    run_setup()
    android_setup.confirm_login("profile_020")
    monkeypatch.setattr(android_jobs, "run_get_ready", lambda pid: {"status": "failed", "reason": "picker: no Gallery"})
    android_setup._running.add("profile_020")
    android_setup._get_ready("profile_020")
    step = next(s for s in android_setup.progress("profile_020")["steps"] if s["key"] == "facebook_ready")
    assert (step["status"], step["reason"]) == ("failed", "picker: no Gallery")
    assert android_setup.progress("profile_020")["state"] == "done"  # only reported
    monkeypatch.setattr(android_jobs, "run_get_ready", lambda pid: {"status": "ready"})
    android_setup._running.add("profile_020")
    android_setup._get_ready("profile_020")
    assert statuses(android_setup.progress("profile_020"))["facebook_ready"] == "passed"


def test_a_copy_in_another_user_is_added_without_installing(phone):
    phone.packages.add((12, AGENT))  # the companion app, same version, set up for another account
    progress = run_setup()
    assert statuses(progress)["companion_app"] == "passed"
    assert phone.installs == [] and (11, AGENT) in phone.packages


def test_an_install_refused_in_the_background_is_retried_on_screen(phone):
    phone.verify_background_installs = True
    progress = run_setup()
    assert statuses(progress)["companion_app"] == "passed"
    assert phone.installs == [11]  # the second try, made with this user on screen


def test_retry_skips_steps_that_are_already_done(phone):
    run_setup()
    run_setup()
    assert phone.installs == [11]


def test_recheck_repairs_what_changed_and_keeps_the_login(phone):
    run_setup()
    android_setup.confirm_login("profile_020")
    phone.settings[(11, "secure", "location_mode")] = "3"
    progress = run_setup()
    assert phone.settings[(11, "secure", "location_mode")] == "0"
    assert progress["state"] == "done" and statuses(progress)["login"] == "passed"


def test_recheck_reports_a_step_that_no_longer_passes_and_keeps_the_login(phone, monkeypatch):
    run_setup()
    android_setup.confirm_login("profile_020")

    def network_down(_ctx):
        raise android_setup.StepFailed("The network check did not pass.")

    with monkeypatch.context() as patch:
        patch.setitem(android_setup.ACTIONS, "network", network_down)
        progress = run_setup()
    assert progress["state"] == "failed" and statuses(progress)["network"] == "failed"
    assert statuses(progress)["login"] == "passed"
    assert run_setup()["state"] == "done"


def test_facebook_is_never_installed_before_the_network_passes(phone):
    phone.agents[11].reachable = set()
    progress = run_setup()
    assert progress["state"] == "failed" and statuses(progress)["network"] == "failed"
    assert (11, FACEBOOK) not in phone.packages


def test_setup_fails_clearly_without_a_bundled_companion_app(phone, tmp_path, monkeypatch):
    monkeypatch.setattr(android_setup, "AGENT_MANIFEST", tmp_path / "missing.json")
    progress = run_setup()
    step = next(step for step in progress["steps"] if step["key"] == "phone_ready")
    assert step["status"] == "failed" and "isn't bundled" in step["reason"]


def test_kill_switch_restarts_the_user_from_the_main_user(phone):
    phone.current = 11
    run_setup()
    assert phone.current == 0 and phone.users_running[11]


def test_a_set_up_phone_reads_as_secondary_on_another_pc(phone, tmp_path, monkeypatch):
    run_setup()
    # Another PC: new identity, empty pool and no accounts.
    monkeypatch.setattr(android_ownership, "IDENTITY_FILE", tmp_path / "other.json")
    (tmp_path / "pool.json").write_text("[]")
    shutil.rmtree(tmp_path / "profiles" / "profile_020")
    scan = android_devices.scan_device("PIXEL")
    row = next(row for row in scan["last_scan"]["users"] if row["user_id"] == 11)
    assert scan["role"] == "secondary" and row["agent"]["account"]["name"] == "Anna"
    assert row["agent"]["proxy"] == {"host": "9.142.198.36", "port": 5703, "username": "u1"}

    profile = android_ownership.import_from_phone("PIXEL", 11)
    assert profile["id"] == "profile_020" and profile["name"] == "Anna"
    entry = proxy_service.find_proxy("9.142.198.36", 5703, "u1")
    assert entry["held_by_phone"] and entry["password"] == "" and entry["profile_id"] == "profile_020"
    with pytest.raises(ValueError, match="managed by"):
        android_setup.start("profile_020")


@pytest.fixture
def taken(phone, tmp_path, monkeypatch):
    """The phone set up with a PIN by PC 1, now on PC 2 with the account imported."""
    run_setup()
    phone.agents[11].pin = "1234"
    monkeypatch.setattr(android_ownership, "IDENTITY_FILE", tmp_path / "other.json")
    (tmp_path / "pool.json").write_text("[]")
    shutil.rmtree(tmp_path / "profiles" / "profile_020")
    android_devices.scan_device("PIXEL")
    android_ownership.import_from_phone("PIXEL", 11)
    return phone


def test_make_primary_needs_the_right_pin(taken):
    with pytest.raises(android_ownership.WrongPin):
        android_ownership.make_primary("PIXEL", "0000")
    assert android_ownership.phone_role(taken, "PIXEL")["role"] == "secondary"
    result = android_ownership.make_primary("PIXEL", "1234")
    assert result["accounts"][0]["status"] == "ok"
    assert proxy_service.find_proxy("9.142.198.36", 5703, "u1")["password"] == "secret"
    assert android_ownership.phone_role(taken, "PIXEL")["role"] == "primary"
    assert android_ownership.stored_pin("PIXEL") == "1234"


def test_forgot_pin_links_the_matching_pool_proxy_and_clears_the_pin(taken, tmp_path):
    proxy_service.import_proxies_from_text("9.142.198.36:5703:u1:secret")
    pool = json.loads((tmp_path / "pool.json").read_text())
    assert len(pool) == 1  # The placeholder already holds that address.
    pool[0]["password"] = "secret"
    (tmp_path / "pool.json").write_text(json.dumps(pool))
    result = android_ownership.make_primary("PIXEL", forgot_pin=True)
    assert result["accounts"][0]["status"] == "ok"
    assert taken.agents[11].pin is None


def test_forgot_pin_with_a_wrong_pool_password_keeps_the_phone_working(taken, tmp_path):
    pool = json.loads((tmp_path / "pool.json").read_text())
    pool[0].update(password="old", held_by_phone=False)
    (tmp_path / "pool.json").write_text(json.dumps(pool))
    result = android_ownership.make_primary("PIXEL", forgot_pin=True)
    assert result["accounts"][0]["status"] == "needs_proxy"
    assert taken.agents[11].proxy["password"] == "secret"


def test_a_proxy_held_by_another_account_is_refused(phone):
    with pytest.raises(ValueError, match="already used"):
        proxy_service.hold_phone_proxy("profile_999", "9.142.198.36", 5703, "u1", None)


def test_agent_call_sends_its_argument_base64_encoded(phone):
    phone.packages.add((11, AGENT))
    android_network.agent_call(phone, "PIXEL", 11, "set_account", 5, {"name": "Ann's 'shop'"})
    assert phone.agents[11].account["name"] == "Ann's 'shop'"


def test_refusals_carry_the_app_s_error_code(phone):
    phone.packages.add((11, AGENT))
    phone.agents[11].pin = "1234"
    with pytest.raises(android_network.AgentRefused) as error:
        android_network.agent_call(phone, "PIXEL", 11, "export", 5, {"pin": "9999"})
    assert error.value.code == "wrong_pin" and isinstance(error.value, AdbError)


def test_parsers_for_new_adb_output():
    assert adb_client.parse_created_user("Success: created user id 12\n") == 12
    assert adb_client.parse_appop_allowed("ACTIVATE_VPN: allow; time=+3m\n")
    assert not adb_client.parse_appop_allowed("ACTIVATE_VPN: ignore\n")


def test_replacing_a_placeholder_proxy_tries_it_on_the_phone_and_drops_the_placeholder(taken, tmp_path):
    android_ownership.make_primary("PIXEL", forgot_pin=True)  # No match in the empty pool: needs a proxy.
    pool = json.loads((tmp_path / "pool.json").read_text())
    pool.append({"id": "ny", "host": "72.1.183.109", "port": 1080, "username": "u2", "password": "pw",
                 "type": "socks5", "exit_ip": "72.1.183.109", "assigned": False, "profile_id": None})
    (tmp_path / "pool.json").write_text(json.dumps(pool))
    taken.agents[11].reachable.add(("72.1.183.109", 1080, "u2", "pw"))
    result = android_ownership.replace_proxy("profile_020", "ny")
    assert result["status"] == "verified" and taken.agents[11].proxy["host"] == "72.1.183.109"
    assert [p["id"] for p in json.loads((tmp_path / "pool.json").read_text())] == ["ny"]


def test_a_proxy_that_does_not_work_leaves_the_phone_on_its_old_one(phone, tmp_path):
    run_setup()
    pool = json.loads((tmp_path / "pool.json").read_text())
    pool.append({"id": "dead", "host": "192.0.2.1", "port": 1080, "username": "", "password": "x",
                 "type": "socks5", "assigned": False, "profile_id": None})
    (tmp_path / "pool.json").write_text(json.dumps(pool))
    with pytest.raises(AdbError, match="kept its old one"):
        android_ownership.replace_proxy("profile_020", "dead")
    assert phone.agents[11].proxy["host"] == "9.142.198.36"


def test_a_proxy_that_stopped_working_can_be_swapped_during_setup_on_a_new_phone(phone, tmp_path, monkeypatch):
    def proxy_down(_ctx):
        raise android_setup.StepFailed("The exit IP could not be measured: Connection reset.")

    with monkeypatch.context() as patch:
        patch.setitem(android_setup.ACTIONS, "network", proxy_down)
        progress = run_setup()
    assert progress["state"] == "failed" and statuses(progress)["network"] == "failed"
    assert statuses(progress)["save_details"] == "pending"  # The phone isn't claimed yet.
    pool = json.loads((tmp_path / "pool.json").read_text())
    pool.append({"id": "ny", "host": "72.1.183.109", "port": 1080, "username": "u2", "password": "pw",
                 "type": "socks5", "exit_ip": "72.1.183.109", "assigned": False, "profile_id": None})
    (tmp_path / "pool.json").write_text(json.dumps(pool))
    phone.agents[11].reachable.add(("72.1.183.109", 1080, "u2", "pw"))

    assert android_ownership.replace_proxy("profile_020", "ny")["status"] == "verified"
    saved = {p["id"]: p for p in json.loads((tmp_path / "pool.json").read_text())}
    assert saved["ny"]["profile_id"] == "profile_020"
    assert saved["la"]["profile_id"] is None and saved["la"]["assigned"] is False  # One account, one proxy.

    progress = run_setup()
    assert progress["state"] == "waiting_login" and phone.agents[11].proxy["host"] == "72.1.183.109"


def test_permission_state_is_read_from_the_right_user():
    from backend.services.adb_client import parse_permission_state
    dumpsys = """
    User 0: ceDataInode=1
      runtime permissions:
        android.permission.CAMERA: granted=true, flags=[ USER_SET]
    User 11: ceDataInode=2
      runtime permissions:
        android.permission.POST_NOTIFICATIONS: granted=false, flags=[ USER_SET|USER_FIXED|USER_SENSITIVE_WHEN_DENIED]
        android.permission.CAMERA: granted=false, flags=[ USER_SET|USER_SENSITIVE_WHEN_GRANTED]
    """
    assert parse_permission_state(dumpsys, 11, "android.permission.CAMERA") == (False, {"USER_SET", "USER_SENSITIVE_WHEN_GRANTED"})
    granted, flags = parse_permission_state(dumpsys, 11, "android.permission.POST_NOTIFICATIONS")
    assert not granted and "USER_FIXED" in flags
    assert parse_permission_state(dumpsys, 0, "android.permission.CAMERA") == (True, {"USER_SET"})
    assert parse_permission_state(dumpsys, 12, "android.permission.CAMERA") is None
