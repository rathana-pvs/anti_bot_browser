"""In-app setup of a phone account.

Installs and configures the companion app in the account's Android user, proves the proxy and
the kill switch, and only then installs Facebook, so no Facebook traffic can leave through the
phone's own network. Each step checks first and acts only when needed, so Retry always resumes
safely. The run happens in the background under the phone lock; its progress is saved in the
account's config.json (`android_setup`).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable

from backend.config import PROFILES_DIR
from backend.services import android_devices, android_network, android_ownership, proxy_service
from backend.services.adb_client import FACEBOOK_PACKAGE, AdbClient, AdbError
from backend.services.android_devices import CONTROL_USER_ID, DeviceNotFound, _client
from backend.services.android_network import AGENT_PACKAGE, STATUS_TIMEOUT_S, agent_call
from backend.services.profile_service import atomic_write_json, utc_now

VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor" / "android-agent"
AGENT_APK = VENDOR_DIR / "agent.apk"
AGENT_MANIFEST = VENDOR_DIR / "manifest.json"
MIN_SDK = 33
KILL_SWITCH_TIMEOUT_S = 45.0
SCREEN_OFF_TIMEOUT_MS = str(15 * 60 * 1000)
POLL_S = 1.0
LOCATION_PERMISSIONS = ("android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION")
# Without these, Facebook drops a shared photo and opens its feed instead of the composer.
MEDIA_PERMISSIONS = ("android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO")
# Posts use gallery media only. Denied with "don't ask again", so their prompts never cover a job.
DENIED_PERMISSIONS = ("android.permission.CAMERA", "android.permission.RECORD_AUDIO",
                      "android.permission.POST_NOTIFICATIONS")

STEPS = (
    ("phone_ready", "Phone ready"),
    ("android_user", "Android user"),
    ("companion_app", "Install companion app"),
    ("allow_vpn", "Allow VPN"),
    ("proxy", "Send proxy"),
    ("kill_switch", "Always-on and kill switch"),
    ("phone_settings", "Phone settings"),
    ("network", "Check network"),
    ("facebook", "Install Facebook"),
    ("save_details", "Save to phone"),
    ("login", "Log in to Facebook"),
    # After the login: Facebook's own first screens (camera roll consent), answered once by taps.
    ("facebook_ready", "Get Facebook ready"),
)
LOGIN_INDEX = [key for key, _ in STEPS].index("login")
_running: set[str] = set()
_running_lock = threading.Lock()


class StepFailed(Exception):
    pass


class _Context:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.client: AdbClient = _client()
        self.reload()

    def reload(self) -> None:
        self.config, self.runtime, self.proxy = android_network._account(self.profile_id)
        self.serial = self.runtime["device_serial"]
        self.user_id = int(self.runtime["android_user_id"])

    def status(self) -> dict:
        return agent_call(self.client, self.serial, self.user_id, "status", STATUS_TIMEOUT_S)


def bundled_agent() -> dict:
    """The companion app shipped with this build, checked against its pinned SHA-256."""
    try:
        manifest = json.loads(AGENT_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StepFailed("The companion app isn't bundled with this build of the app.") from exc
    if not AGENT_APK.is_file() or hashlib.sha256(AGENT_APK.read_bytes()).hexdigest() != manifest.get("sha256"):
        raise StepFailed("The bundled companion app failed its integrity check. Reinstall the app.")
    return manifest


# --- Steps ---------------------------------------------------------------------------------------

def _phone_ready(ctx: _Context) -> None:
    sdk = ctx.client.properties(ctx.serial).get("sdk")
    if sdk is None or sdk < MIN_SDK:
        raise StepFailed("This phone needs Android 13 or newer.")
    if not ctx.client.package_on_phone(ctx.serial, FACEBOOK_PACKAGE):
        raise StepFailed("Facebook isn't installed on this phone. Install it from the Play Store in the phone's "
                         "main user, then retry.")
    bundled_agent()


def _android_user(ctx: _Context) -> None:
    if ctx.user_id == CONTROL_USER_ID:
        raise StepFailed("The phone owner (user 0) is the control user and cannot hold an account.")
    if ctx.user_id not in {user.user_id for user in ctx.client.users(ctx.serial)}:
        raise StepFailed("This Android user no longer exists on the phone.")
    expected = ctx.runtime.get("android_user_serial")
    actual = ctx.client.user_serials(ctx.serial).get(ctx.user_id)
    if expected is not None and actual is not None and expected != actual:
        raise StepFailed("This Android user was removed and created again; it is not this account's user any more.")
    if not next(user.running for user in ctx.client.users(ctx.serial) if user.user_id == ctx.user_id):
        ctx.client.start_user(ctx.serial, ctx.user_id)


def _companion_app(ctx: _Context) -> None:
    manifest = bundled_agent()
    installed = ctx.client.package_installed(ctx.serial, ctx.user_id, AGENT_PACKAGE)
    current = ctx.client.package_version(ctx.serial, AGENT_PACKAGE) == manifest["version_name"]
    if not installed and current and ctx.client.package_on_phone(ctx.serial, AGENT_PACKAGE):
        # Another user has this version: add the same copy, which needs no install verification.
        ctx.client.install_existing(ctx.serial, ctx.user_id, AGENT_PACKAGE)
    elif not installed or not current:
        try:
            _install_agent(ctx)
        except AdbError as exc:
            if "UPDATE_INCOMPATIBLE" in str(exc):
                raise StepFailed("The phone has a test build of the companion app signed with another key. "
                                 "Uninstall it from this user, then retry.") from exc
            raise
    if not ctx.client.package_installed(ctx.serial, ctx.user_id, AGENT_PACKAGE):
        raise StepFailed("The companion app did not install in this Android user.")
    ctx.client.exempt_battery(ctx.serial, AGENT_PACKAGE)


def _install_agent(ctx: _Context) -> None:
    """Play Protect checks ADB installs in the user on screen; for a user in the background it can
    refuse outright (INSTALL_FAILED_VERIFICATION_FAILURE). Then the user is shown and it is tried again."""
    try:
        ctx.client.install_apk(ctx.serial, ctx.user_id, str(AGENT_APK))
    except AdbError as exc:
        if "VERIFICATION_FAILURE" not in str(exc) or ctx.client.current_user(ctx.serial) == ctx.user_id:
            raise
        switch = android_devices.switch_and_wait(ctx.client, ctx.serial, ctx.user_id)
        if not switch["ready"]:
            raise StepFailed(f"Android refused the install, and this user could not be shown to retry it: "
                             f"{switch.get('reason')}") from exc
        ctx.client.install_apk(ctx.serial, ctx.user_id, str(AGENT_APK))


def _allow_vpn(ctx: _Context) -> None:
    if not ctx.client.appop_vpn_allowed(ctx.serial, ctx.user_id, AGENT_PACKAGE):
        ctx.client.allow_vpn(ctx.serial, ctx.user_id, AGENT_PACKAGE)
    if not ctx.client.appop_vpn_allowed(ctx.serial, ctx.user_id, AGENT_PACKAGE):
        raise StepFailed("The phone did not allow the companion app's VPN.")


def _proxy_args(proxy: dict) -> dict:
    return {"host": proxy["host"], "port": int(proxy["port"]),
            "username": proxy.get("username") or "", "password": proxy.get("password") or ""}


def _proxy(ctx: _Context) -> None:
    proxy = ctx.proxy
    if not proxy:
        raise StepFailed("This account has no proxy. Choose one, then retry.")
    if not proxy.get("password") and proxy.get("held_by_phone"):
        raise StepFailed("This PC doesn't have this proxy's password. Make this PC primary first.")
    if not all(part.isdigit() for part in str(proxy["host"]).split(".")):
        raise StepFailed("Phone accounts need a proxy given by its IP address, not a host name.")
    if (proxy.get("type") or "socks5") != "socks5":
        raise StepFailed("Phone accounts need a SOCKS5 proxy.")
    args = _proxy_args(proxy)
    if agent_call(ctx.client, ctx.serial, ctx.user_id, "same_proxy", STATUS_TIMEOUT_S, args).get("same"):
        return
    agent_call(ctx.client, ctx.serial, ctx.user_id, "set_config", STATUS_TIMEOUT_S, args)
    if not agent_call(ctx.client, ctx.serial, ctx.user_id, "same_proxy", STATUS_TIMEOUT_S, args).get("same"):
        raise StepFailed("The companion app did not keep the proxy.")


def _kill_switch_on(status: dict) -> bool:
    return bool(status.get("service_running") and status.get("always_on") and status.get("lockdown"))


def _kill_switch(ctx: _Context) -> None:
    if _kill_switch_on(ctx.status()):
        return
    serial, user_id = ctx.serial, ctx.user_id
    if ctx.client.setting(serial, "secure", "always_on_vpn_app", user_id) != AGENT_PACKAGE:
        ctx.client.put_setting(serial, "secure", "always_on_vpn_app", AGENT_PACKAGE, user_id)
    if ctx.client.setting(serial, "secure", "always_on_vpn_lockdown", user_id) != "1":
        ctx.client.put_setting(serial, "secure", "always_on_vpn_lockdown", "1", user_id)
    # Android applies these only when the user starts, and cannot stop the user on screen.
    if ctx.client.current_user(serial) == user_id:
        ctx.client.switch_user(serial, CONTROL_USER_ID)
        deadline = time.monotonic() + android_devices.SWITCH_READY_TIMEOUT_S
        while ctx.client.current_user(serial) != CONTROL_USER_ID:
            if time.monotonic() >= deadline:
                raise StepFailed("The phone did not switch to its main user, so this user could not restart.")
            time.sleep(POLL_S)
    ctx.client.stop_user(serial, user_id)
    ctx.client.start_user(serial, user_id)
    deadline = time.monotonic() + KILL_SWITCH_TIMEOUT_S
    while True:
        try:
            if _kill_switch_on(ctx.status()):
                return
        except AdbError:
            pass  # The app answers once the user has started.
        if time.monotonic() >= deadline:
            raise StepFailed("Android did not turn on Always-on VPN for this user. In this user's VPN settings, turn on "
                             "Always-on VPN and Block connections without VPN for the Network agent, then retry.")
        time.sleep(POLL_S)


def _phone_settings(ctx: _Context) -> None:
    wanted = ((ctx.user_id, "secure", "location_mode", "0"),
              # Long enough that a job never has to wake the screen midway.
              (ctx.user_id, "system", "screen_off_timeout", SCREEN_OFF_TIMEOUT_MS),
              (None, "global", "wifi_scan_always_enabled", "0"),
              (None, "global", "ble_scan_always_enabled", "0"))
    for user_id, namespace, key, value in wanted:
        if ctx.client.setting(ctx.serial, namespace, key, user_id) != value:
            ctx.client.put_setting(ctx.serial, namespace, key, value, user_id)
        if ctx.client.setting(ctx.serial, namespace, key, user_id) != value:
            raise StepFailed(f"The phone did not keep {key} = {value}.")


def _network(ctx: _Context) -> None:
    # The check compares the exit against the proxy's measured exit IP.
    if ctx.proxy and not ctx.proxy.get("exit_ip") and ctx.proxy.get("password"):
        try:
            proxy_service.refresh_proxy_geography(ctx.proxy["id"])
        except (RuntimeError, ValueError):
            pass  # The check below then reports what is missing.
    result = android_network._verify_until_ready(ctx.profile_id)
    if result["status"] != "verified":
        raise StepFailed(result.get("reason") or "The network check did not pass.")


def _facebook(ctx: _Context) -> None:
    if not ctx.client.package_installed(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE):
        ctx.client.install_existing(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE)
    if not ctx.client.package_installed(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE):
        raise StepFailed("Facebook did not install in this Android user.")
    for permission in LOCATION_PERMISSIONS:
        try:
            ctx.client.revoke_permission(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE, permission)
        except AdbError:
            pass  # Not requested by this Facebook version.
    for permission in MEDIA_PERMISSIONS:
        ctx.client.grant_permission(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE, permission)
    for permission in DENIED_PERMISSIONS:
        ctx.client.deny_permission(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE, permission)
        state = ctx.client.permission_state(ctx.serial, ctx.user_id, FACEBOOK_PACKAGE, permission)
        if state is not None and (state[0] or "USER_FIXED" not in state[1]):
            raise StepFailed(f"The phone did not keep {permission.rsplit('.', 1)[-1]} denied.")


def _save_details(ctx: _Context) -> None:
    if android_ownership.phone_role(ctx.client, ctx.serial)["role"] == "unclaimed":
        android_ownership.claim(ctx.client, ctx.serial)
        android_devices.update_device_record(ctx.serial, role="primary")
    android_ownership.sync_details(ctx.profile_id)
    android_ownership.apply_pin(ctx.client, ctx.serial, ctx.user_id)


ACTIONS: dict[str, Callable[[_Context], None]] = {
    "phone_ready": _phone_ready, "android_user": _android_user, "companion_app": _companion_app,
    "allow_vpn": _allow_vpn, "proxy": _proxy, "kill_switch": _kill_switch,
    "phone_settings": _phone_settings, "network": _network, "facebook": _facebook,
    "save_details": _save_details,
}


# --- Progress and runs ---------------------------------------------------------------------------

def _config_path(profile_id: str) -> Path:
    return PROFILES_DIR / profile_id / "config.json"


def _update(profile_id: str, change: Callable[[dict], None]) -> dict:
    with android_network._write_lock:
        path = _config_path(profile_id)
        config = json.loads(path.read_text(encoding="utf-8"))
        setup = config.get("android_setup") or {}
        setup.setdefault("steps", {})
        change(setup)
        setup["updated_at"] = utc_now()
        config["android_setup"] = setup
        atomic_write_json(path, config)
        return setup


def _set_step(profile_id: str, key: str, status: str, reason: str | None = None) -> None:
    def change(setup: dict) -> None:
        setup["steps"][key] = {"status": status, "reason": reason, "at": utc_now()}
        setup["current"] = key if status == "running" else setup.get("current")
    _update(profile_id, change)


def progress(profile_id: str) -> dict:
    config, _, _ = android_network._account(profile_id)
    setup = config.get("android_setup") or {}
    saved = setup.get("steps") or {}
    with _running_lock:
        running = profile_id in _running
    state = setup.get("state") or "not_started"
    steps = []
    for key, label in STEPS:
        step = {"key": key, "label": label, **{"status": "pending", "reason": None}, **(saved.get(key) or {})}
        # A run cut short by a restart left a step marked running.
        if step["status"] == "running" and not running:
            step.update(status="failed", reason="Setup was interrupted. Retry to continue.")
        steps.append(step)
    if state == "running" and not running:
        state = "failed"
    return {"profile_id": profile_id, "state": state, "steps": steps, "updated_at": setup.get("updated_at")}


def start(profile_id: str) -> dict:
    """Starts or resumes setup in the background; returns progress at once."""
    ctx = _Context(profile_id)
    android_ownership.ensure_primary(ctx.serial, ctx.client)
    with _running_lock:
        if profile_id in _running:
            return progress(profile_id)
        _running.add(profile_id)
    try:
        _update(profile_id, lambda setup: setup.update(state="running"))
        threading.Thread(target=_run, args=(ctx,), name=f"android-setup-{profile_id}", daemon=True).start()
    except Exception:
        with _running_lock:
            _running.discard(profile_id)
        raise
    return progress(profile_id)


def _run(ctx: _Context) -> None:
    key = STEPS[0][0]
    try:
        with android_devices.phone_lock(ctx.serial):
            for key, _ in STEPS[:LOGIN_INDEX]:
                _set_step(ctx.profile_id, key, "running")
                ctx.reload()
                ACTIONS[key](ctx)
                _set_step(ctx.profile_id, key, "passed")
        _update(ctx.profile_id, lambda setup: (
            setup.update(state="done" if (setup["steps"].get("login") or {}).get("status") == "passed" else "waiting_login"),
            setup["steps"].setdefault("login", {"status": "pending", "reason": None, "at": utc_now()})))
    except Exception as exc:  # Every failure ends the run with its reason on the step.
        reason = str(exc) if isinstance(exc, (StepFailed, AdbError, ValueError, DeviceNotFound)) else (
            f"Unexpected error: {exc.__class__.__name__}")
        _set_step(ctx.profile_id, key, "failed", reason)
        _update(ctx.profile_id, lambda setup: setup.update(state="failed"))
    finally:
        with _running_lock:
            _running.discard(ctx.profile_id)


def confirm_login(profile_id: str) -> dict:
    """The operator logged in to Facebook on the live screen. Setup is then done; Facebook's first
    screens are answered in the background, and a failure there is only reported, since jobs can
    still answer them themselves."""
    current = progress(profile_id)
    if any(step["status"] != "passed" for step in current["steps"][:LOGIN_INDEX]):
        raise ValueError("Finish the other setup steps before confirming the login.")
    _set_step(profile_id, "login", "passed")
    _update(profile_id, lambda setup: setup.update(state="done"))
    start_get_ready(profile_id)
    return progress(profile_id)


def start_get_ready(profile_id: str) -> dict:
    """Runs the "Get Facebook ready" step in the background (also its retry)."""
    with _running_lock:
        if profile_id in _running:
            return progress(profile_id)
        _running.add(profile_id)
    _set_step(profile_id, "facebook_ready", "running")
    threading.Thread(target=_get_ready, args=(profile_id,), name=f"android-ready-{profile_id}", daemon=True).start()
    return progress(profile_id)


def _get_ready(profile_id: str) -> None:
    from backend.services import android_jobs
    try:
        result = android_jobs.run_get_ready(profile_id)
        if result.get("status") == "ready":
            _set_step(profile_id, "facebook_ready", "passed")
        else:
            _set_step(profile_id, "facebook_ready", "failed", result.get("reason") or "Facebook did not get ready.")
    except Exception as exc:
        _set_step(profile_id, "facebook_ready", "failed", f"Unexpected error: {exc.__class__.__name__}")
    finally:
        with _running_lock:
            _running.discard(profile_id)


def is_set_up(config: dict) -> bool:
    return (config.get("android_setup") or {}).get("state") == "done"
