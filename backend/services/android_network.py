"""A phone account's network: reads the companion app in its Android user and verifies, from
inside that user, that traffic leaves through the account's reserved proxy.

Only a passing check marks the account verified. The companion app itself is installed and
configured outside the app for now (see android-agent/tools/agent_poc.py).
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from backend.config import PROFILES_DIR
from backend.services import android_devices, proxy_service
from backend.services.adb_client import AdbClient, AdbError
from backend.services.android_devices import ANDROID_RUNTIME, CONTROL_USER_ID, DeviceNotFound, _client
from backend.services.profile_service import atomic_write_json, utc_now

AGENT_PACKAGE = "com.isolatedbrowser.agent"
AGENT_AUTHORITY = f"{AGENT_PACKAGE}.control"
_REPLY = re.compile(r"json=(\{.*\})\}\]\s*$", re.S)
STATUS_TIMEOUT_S = 15.0
# The check makes HTTPS requests through the proxy; about 17 s on a Pixel 10 Pro XL.
CHECK_TIMEOUT_S = 60.0
# After a user starts, its VPN comes up in seconds, but Android re-validates Private DNS for
# 30-90 s (measured on a Pixel 10 Pro XL), so the first checks can fail to resolve names.
OPEN_VERIFY_TIMEOUT_S = 150.0
OPEN_RETRY_S = 10.0
_write_lock = threading.Lock()


def parse_agent_reply(output: str) -> dict:
    match = _REPLY.search(output.strip())
    if not match:
        raise AdbError("The companion app did not answer. It may not be installed in this Android user.")
    return json.loads(match.group(1))


def agent_call(client: AdbClient, serial: str, user_id: int, method: str, timeout: float) -> dict:
    output = client.shell(serial, "content", "call", "--user", str(int(user_id)),
                          "--uri", f"content://{AGENT_AUTHORITY}", "--method", method, timeout=timeout)
    reply = parse_agent_reply(output)
    if not reply.get("ok"):
        raise AdbError(f"The companion app refused {method}: {reply.get('error') or 'unknown error'}")
    return reply


def _account(profile_id: str) -> tuple[dict, dict, dict | None]:
    """The profile config, its phone runtime, and its reserved pool proxy."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id):
        raise DeviceNotFound("Account not found")
    try:
        config = json.loads((PROFILES_DIR / profile_id / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeviceNotFound("Account not found") from exc
    runtime = config.get("runtime") or {}
    if runtime.get("kind") != ANDROID_RUNTIME:
        raise ValueError("This account is not on a phone.")
    proxy_id = (config.get("network") or {}).get("proxy_id")
    proxy = next((p for p in proxy_service.load_proxy_pool() if p.get("id") == proxy_id), None) if proxy_id else None
    return config, runtime, proxy


def evaluate_status(status: dict, proxy: dict | None) -> tuple[str, str | None]:
    """(state, reason) from the companion app's status. "tunnel_on" means set up but not yet verified."""
    if not proxy:
        return "no_proxy", "This account has no proxy reserved."
    if not status.get("service_running") or not status.get("tunnel_up") or not status.get("engine_running"):
        return "blocked", status.get("last_error") or "The companion app's tunnel is not running."
    expected = f"{proxy.get('host')}:{proxy.get('port')}"
    if status.get("proxy") != expected:
        return "blocked", f"The phone uses proxy {status.get('proxy') or 'none'}, not this account's {expected}."
    if not status.get("always_on") or not status.get("lockdown"):
        return "blocked", "Always-on VPN or its lockdown (kill switch) is off, so traffic could leave without the proxy."
    return "tunnel_on", None


def evaluate_check(check: dict, proxy: dict) -> tuple[str, str | None]:
    """Verified only when every measured address is the proxy's exit and IPv6 does not get out."""
    expected = proxy.get("exit_ip") or proxy.get("host")
    exit_ip = (check.get("exit") or {}).get("ip")
    if not exit_ip:
        return "blocked", f"The exit IP could not be measured: {(check.get('exit') or {}).get('error') or 'no reply'}."
    if exit_ip != expected:
        if not proxy.get("exit_ip"):
            return "blocked", (f"Traffic leaves from {exit_ip}, but this proxy's exit IP isn't known yet. "
                               "Check its location on the Proxies page, then verify again.")
        return "blocked", f"Traffic leaves from {exit_ip}, not the proxy's exit {expected}."
    trace = (check.get("trace_ipv4") or {}).get("ip")
    if trace and trace != exit_ip:
        return "blocked", f"A second check left from {trace}, not {exit_ip}."
    if (check.get("trace_ipv6") or {}).get("ip"):
        return "blocked", "IPv6 traffic gets out around the proxy."
    return "verified", None


def network_status(profile_id: str) -> dict:
    """Quick read of the companion app (under a second). Nothing is saved."""
    _, runtime, proxy = _account(profile_id)
    client = _client()
    try:
        status = agent_call(client, runtime["device_serial"], runtime["android_user_id"], "status", STATUS_TIMEOUT_S)
    except AdbError as exc:
        return {"state": "not_set_up", "reason": str(exc), "agent": None}
    state, reason = evaluate_status(status, proxy)
    return {"state": state, "reason": reason, "agent": _safe_status(status)}


def verify(profile_id: str) -> dict:
    """Runs the companion app's check inside the account's Android user and saves the result."""
    config, runtime, proxy = _account(profile_id)
    client = _client()
    serial, user_id = runtime["device_serial"], runtime["android_user_id"]
    result: dict[str, Any] = {"checked_at": utc_now(), "exit_ip": None, "exit_timezone": None}
    try:
        status = agent_call(client, serial, user_id, "status", STATUS_TIMEOUT_S)
        state, reason = evaluate_status(status, proxy)
        result["agent_version"] = status.get("version")
        if state == "tunnel_on":
            check = agent_call(client, serial, user_id, "check", CHECK_TIMEOUT_S)
            exit_info = check.get("exit") or {}
            result.update(exit_ip=exit_info.get("ip"), exit_timezone=exit_info.get("timezone"))
            state, reason = evaluate_check(check, proxy)
    except AdbError as exc:
        state, reason = "not_set_up", str(exc)
    result.update(status=state, reason=reason)
    _save(profile_id, result)
    return result


def open_account(profile_id: str) -> dict:
    """Shows the account's Android user on screen and checks its proxy before anything uses it.

    With one account at a time (the phone's default), the phone's other users are stopped, so no
    other account runs in the background, and the phone's time is set to this account's proxy.
    The target is switched to first because Android cannot stop the user on screen.
    """
    _, runtime, proxy = _account(profile_id)
    serial, user_id = runtime["device_serial"], runtime["android_user_id"]
    isolate = android_devices.one_account_at_a_time(serial)
    client = _client()
    stopped: list[int] = []
    zone_set = None
    with android_devices.phone_lock(serial):
        # The phone's switcher may have been turned back on in Settings; opening hides it again.
        if android_devices.switch_from_app_only(serial) and client.user_switcher_enabled(serial) is not False:
            client.set_user_switcher(serial, False)
        switch = android_devices.switch_and_wait(client, serial, user_id)
        if not switch["ready"]:
            return {"ready": False, "reason": switch["reason"], "switch": switch,
                    "stopped_users": stopped, "timezone_set": zone_set, "network": None}
        if isolate:
            for user in client.users(serial):
                if user.running and user.user_id not in (CONTROL_USER_ID, user_id):
                    client.stop_user(serial, user.user_id)
                    stopped.append(user.user_id)
            zone = (proxy or {}).get("timezone")
            if zone:
                phone = client.timezone_state(serial)
                same = phone["zone"] and android_devices.utc_offset(phone["zone"]) == android_devices.utc_offset(zone)
                if not same or phone["auto"]:
                    client.set_timezone(serial, zone)
                    zone_set = zone
        network = _verify_until_ready(profile_id) if proxy else {"status": "no_proxy", "reason": None}
    ready = network["status"] in ("verified", "no_proxy")
    return {"ready": ready, "reason": None if ready else network.get("reason"), "switch": switch,
            "stopped_users": stopped, "timezone_set": zone_set, "network": network}


def _verify_until_ready(profile_id: str) -> dict:
    """Retries only while the user's VPN or DNS is still coming up; a wrong setup fails at once."""
    deadline = time.monotonic() + OPEN_VERIFY_TIMEOUT_S
    while True:
        result = verify(profile_id)
        reason = result.get("reason") or ""
        starting = result["status"] == "not_set_up" or (
            result["status"] == "blocked" and ("could not be measured" in reason or "not running" in reason))
        if not starting or time.monotonic() >= deadline:
            return result
        time.sleep(OPEN_RETRY_S)


def _safe_status(status: dict) -> dict:
    keys = ("version", "service_running", "tunnel_up", "engine_running", "proxy", "always_on", "lockdown", "last_error")
    return {key: status.get(key) for key in keys}


def _save(profile_id: str, result: dict) -> None:
    path = PROFILES_DIR / profile_id / "config.json"
    with _write_lock:
        config = json.loads(path.read_text(encoding="utf-8"))
        config["android_network"] = result
        atomic_write_json(path, config)
