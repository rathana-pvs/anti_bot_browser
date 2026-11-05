"""Which PC manages a phone, and moving phones between PCs.

The phone is the source of truth: each Android user's companion app keeps its account's details
and proxy, and a small file on the phone names the primary PC. The primary PC set the phone up
and may change it. Any other PC is secondary: it reads the accounts from the phone and uses them,
but changes nothing until it is made primary. One PC is connected to a phone at a time.

The optional 4-digit PIN is checked by the companion app before it exports a proxy password. It
guards against mistakes, not against someone with ADB access to the phone.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import uuid
from typing import Any

from backend.config import DATA_DIR, PROFILES_DIR
from backend.services import android_devices, android_network, proxy_service
from backend.services.adb_client import AdbClient, AdbError
from backend.services.android_devices import CONTROL_USER_ID, DeviceNotFound, _client
from backend.services.android_network import AGENT_PACKAGE, STATUS_TIMEOUT_S, AgentRefused, agent_call
from backend.services.profile_service import atomic_write_json, utc_now

IDENTITY_FILE = DATA_DIR / "pc_identity.json"
PHONE_FILE = "/data/local/tmp/isolatedbrowser/phone.json"
# try_config waits up to 30 s for a reply through the new proxy.
TRY_TIMEOUT_S = 60.0
PIN_RE = re.compile(r"^\d{4}$")
_identity_lock = threading.Lock()


class WrongPin(ValueError):
    pass


def pc_identity() -> dict:
    """This install's stable ID and a readable name, written to phones this PC manages."""
    with _identity_lock:
        try:
            identity = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
            if isinstance(identity, dict) and identity.get("id"):
                return identity
        except (OSError, ValueError):
            pass
        identity = {"id": uuid.uuid4().hex, "name": socket.gethostname() or "This PC"}
        atomic_write_json(IDENTITY_FILE, identity)
        return identity


def read_phone(client: AdbClient, serial: str) -> dict:
    text = client.read_file(serial, PHONE_FILE)
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def write_phone(client: AdbClient, serial: str, **changes: Any) -> dict:
    phone = {**read_phone(client, serial), **changes, "updated_at": utc_now()}
    client.write_file(serial, PHONE_FILE, json.dumps(phone))
    return phone


def role_of(phone: dict) -> str:
    """primary (this PC), secondary (another PC is primary) or unclaimed."""
    primary = phone.get("primary_id")
    if not primary:
        return "unclaimed"
    return "primary" if primary == pc_identity()["id"] else "secondary"


def phone_role(client: AdbClient, serial: str) -> dict:
    phone = read_phone(client, serial)
    return {"role": role_of(phone), "primary_name": phone.get("primary_name")}


def ensure_primary(serial: str, client: AdbClient | None = None) -> None:
    """Changes are refused on a phone another PC manages. An unclaimed phone is claimed on setup."""
    state = phone_role(client or _client(), serial)
    if state["role"] == "secondary":
        raise ValueError(f"This phone is managed by {state['primary_name'] or 'another PC'}. "
                         "Make this PC primary to change it.")


def ensure_primary_offline_safe(serial: str) -> None:
    """Like `ensure_primary`, but an unplugged phone is judged by the role last seen."""
    try:
        ensure_primary(serial)
    except AdbError:
        saved = android_devices.load_store()["devices"].get(android_devices.canonical_serial(serial)) or {}
        if saved.get("role") == "secondary":
            raise ValueError(f"This phone is managed by {saved.get('primary_name') or 'another PC'}. "
                             "Make this PC primary to change it.") from None


def claim(client: AdbClient, serial: str) -> dict:
    identity = pc_identity()
    saved = android_devices.load_store()["devices"].get(android_devices.canonical_serial(serial)) or {}
    return write_phone(client, serial, primary_id=identity["id"], primary_name=identity["name"],
                       label=saved.get("label"),
                       one_account_at_a_time=saved.get("one_account_at_a_time", True),
                       switch_from_app_only=saved.get("switch_from_app_only", True))


def stored_pin(serial: str) -> str | None:
    return (android_devices.load_store()["devices"].get(android_devices.canonical_serial(serial)) or {}).get("pin")


def _store_pin(serial: str, pin: str | None) -> None:
    android_devices.update_device_record(serial, pin=pin)


# --- Reading accounts from the phone -------------------------------------------------------------

def _ensure_running(client: AdbClient, serial: str, user_id: int) -> bool:
    """Start a stopped user so its companion app can answer. True when it was started here."""
    running = next((user.running for user in client.users(serial) if user.user_id == user_id), False)
    if not running:
        client.start_user(serial, user_id)
    return not running


def _agent_installed(client: AdbClient, serial: str, user_id: int) -> bool:
    try:
        return client.package_installed(serial, user_id, AGENT_PACKAGE)
    except AdbError:
        return False


def read_user(client: AdbClient, serial: str, user_id: int) -> dict | None:
    """What the companion app in this user knows: its account and saved proxy (no password).
    None when the app is not installed. A user stopped to read it is stopped again."""
    if user_id == CONTROL_USER_ID or not _agent_installed(client, serial, user_id):
        return None
    started = _ensure_running(client, serial, user_id)
    try:
        status = agent_call(client, serial, user_id, "status", STATUS_TIMEOUT_S)
    except AdbError as exc:
        return {"readable": False, "reason": str(exc)}
    finally:
        if started and client.current_user(serial) != user_id:
            try:
                client.stop_user(serial, user_id)
            except AdbError:
                pass
    account = status.get("account") or {}
    saved = status.get("saved_proxy") or ""
    host, _, port = saved.rpartition(":")
    return {
        "readable": True, "version": status.get("version"),
        "account": {key: account.get(key) for key in ("profile_id", "name", "group", "exit_ip", "timezone")},
        "pin_set": bool(account.get("pin_set")),
        "proxy": {"host": host, "port": int(port), "username": status.get("saved_username") or ""}
        if host and port.isdigit() else None,
    }


def sync_details(profile_id: str) -> None:
    """Writes the account's details to its phone (primary PC only). Never the proxy."""
    config, runtime, proxy = android_network._account(profile_id)
    client = _client()
    serial, user_id = runtime["device_serial"], runtime["android_user_id"]
    ensure_primary(serial, client)
    checked = config.get("android_network") or {}
    agent_call(client, serial, user_id, "set_account", STATUS_TIMEOUT_S, {
        "profile_id": profile_id, "name": config.get("name") or "", "group": config.get("group") or "",
        "exit_ip": checked.get("exit_ip") or (proxy or {}).get("exit_ip"),
        "timezone": checked.get("exit_timezone") or (proxy or {}).get("timezone"),
    })


def try_sync_details(profile_id: str) -> bool:
    """For renames and folder moves: the phone may be unplugged, and the next setup run or
    takeover writes the details again."""
    try:
        sync_details(profile_id)
        return True
    except (AdbError, ValueError, DeviceNotFound):
        return False


# --- Importing on a secondary PC -----------------------------------------------------------------

def _free_profile_id(wanted: str | None) -> str | None:
    if wanted and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wanted) and not (PROFILES_DIR / wanted).exists():
        return wanted
    return None


def _link_proxy(profile_id: str, entry: dict) -> None:
    """Point the account's network at its reserved pool entry."""
    path = PROFILES_DIR / profile_id / "config.json"
    with android_network._write_lock:
        config = json.loads(path.read_text(encoding="utf-8"))
        config["network"] = android_devices._reserved_network(entry)
        atomic_write_json(path, config)


def import_from_phone(serial: str, user_id: int) -> dict:
    """Creates this PC's account for an Android user another PC set up, from the phone's details.

    Its proxy is matched in this PC's pool by host, port and username; with no match a
    placeholder without a password is reserved, so this PC never gives that proxy to another account.
    """
    client = _client()
    info = read_user(client, serial, user_id)
    if not info or not info.get("readable"):
        raise ValueError((info or {}).get("reason") or "This Android user has no companion app, so it was not set up by a PC.")
    account = info["account"]
    scan = (android_devices.load_store()["devices"].get(android_devices.canonical_serial(serial)) or {}).get("last_scan") or {}
    if not any(row.get("user_id") == user_id for row in scan.get("users", [])):
        android_devices.scan_device(serial)
    profile = android_devices.import_user(serial, user_id, account.get("name") or f"User {user_id}",
                                          account.get("group") or "", profile_id=_free_profile_id(account.get("profile_id")))
    proxy = info.get("proxy")
    if proxy:
        match = proxy_service.find_proxy(proxy["host"], proxy["port"], proxy["username"])
        entry = proxy_service.hold_phone_proxy(
            profile["id"], proxy["host"], proxy["port"], proxy["username"],
            (match or {}).get("password") or None, account.get("exit_ip"), account.get("timezone"))
        _link_proxy(profile["id"], entry)
    return profile


# --- Taking a phone over -------------------------------------------------------------------------

def _set_up_users(client: AdbClient, serial: str) -> list[tuple[int, dict]]:
    users = []
    for user in client.users(serial):
        info = read_user(client, serial, user.user_id)
        if info and info.get("readable") and (info["account"].get("profile_id") or info.get("proxy")):
            users.append((user.user_id, info))
    return users


def _local_profile(serial: str, user_id: int) -> str | None:
    link = next((link for link in android_devices.linked_profiles()
                 if link["runtime"].get("device_serial") == serial and link["runtime"].get("android_user_id") == user_id), None)
    return link["profile_id"] if link else None


def _with_user(client: AdbClient, serial: str, user_id: int, action):
    started = _ensure_running(client, serial, user_id)
    try:
        return action()
    finally:
        if started and client.current_user(serial) != user_id:
            try:
                client.stop_user(serial, user_id)
            except AdbError:
                pass


def _proxy_args(entry: dict) -> dict:
    return {"host": entry["host"], "port": int(entry["port"]),
            "username": entry.get("username") or "", "password": entry.get("password") or ""}


def make_primary(serial: str, pin: str | None = None, forgot_pin: bool = False) -> dict:
    """Takes the phone over. With the PIN (or none set) every account's proxy is exported with its
    password. With `forgot_pin`, each proxy is matched in this PC's pool instead; an account whose
    proxy cannot be matched asks for another proxy (`replace_proxy`)."""
    if pin is not None and not PIN_RE.fullmatch(pin):
        raise ValueError("The PIN is 4 digits.")
    client = _client()
    with android_devices.phone_lock(serial):
        users = _set_up_users(client, serial)
        exports: dict[int, dict | None] = {}
        for user_id, _ in users:
            try:
                exports[user_id] = _with_user(client, serial, user_id, lambda uid=user_id: agent_call(
                    client, serial, uid, "export", STATUS_TIMEOUT_S, {"pin": pin or ""}))
            except AgentRefused as exc:
                if exc.code != "wrong_pin":
                    raise
                if not forgot_pin:
                    raise WrongPin("Wrong PIN." if pin else "This phone has a PIN. Enter it, or choose Forgot PIN.")
                exports[user_id] = None
        results = [_take_over_account(client, serial, user_id, info, exports[user_id]) for user_id, info in users]
        identity = pc_identity()
        write_phone(client, serial, primary_id=identity["id"], primary_name=identity["name"])
    # A PIN that opened every account is kept, so accounts added later get it too.
    _store_pin(serial, pin if pin and all(exports.values()) else None)
    android_devices.update_device_record(serial, role="primary")
    return {"role": "primary", "accounts": results}


def _take_over_account(client: AdbClient, serial: str, user_id: int, info: dict, export: dict | None) -> dict:
    profile_id = _local_profile(serial, user_id) or import_from_phone(serial, user_id)["id"]
    result = {"profile_id": profile_id, "user_id": user_id, "status": "ok", "reason": None}
    account = info["account"]
    if export and export.get("proxy"):
        full = export["proxy"]
        try:
            entry = proxy_service.hold_phone_proxy(profile_id, full["host"], full["port"], full.get("username") or "",
                                                   full.get("password") or "", account.get("exit_ip"), account.get("timezone"))
        except ValueError as exc:
            return {**result, "status": "needs_proxy", "reason": f"{exc} Choose another proxy for this account."}
        _link_proxy(profile_id, entry)
        return result
    proxy = info.get("proxy")
    match = proxy_service.find_proxy(proxy["host"], proxy["port"], proxy["username"]) if proxy else None
    if not match or not match.get("password") or (match.get("profile_id") not in (None, profile_id)):
        return {**result, "status": "needs_proxy",
                "reason": "This PC's pool has no matching proxy. Choose a proxy for this account."}
    same = _with_user(client, serial, user_id, lambda: agent_call(
        client, serial, user_id, "same_proxy", STATUS_TIMEOUT_S, _proxy_args(match)))
    if not same.get("same"):
        tried = _with_user(client, serial, user_id, lambda: agent_call(
            client, serial, user_id, "try_config", TRY_TIMEOUT_S, _proxy_args(match)))
        if not tried.get("applied"):
            return {**result, "status": "needs_proxy",
                    "reason": "This PC's version of the proxy did not work, so the phone kept its old one. Choose another proxy."}
    entry = proxy_service.hold_phone_proxy(profile_id, match["host"], match["port"], match.get("username") or "",
                                           match["password"], account.get("exit_ip"), account.get("timezone"))
    _link_proxy(profile_id, entry)
    # The PIN protected this proxy, which this PC now holds; clear it with proof of the proxy.
    try:
        _with_user(client, serial, user_id, lambda: agent_call(
            client, serial, user_id, "clear_pin", STATUS_TIMEOUT_S, {"proxy": _proxy_args(entry)}))
    except AdbError:
        pass
    return result


def replace_proxy(profile_id: str, proxy_id: str) -> dict:
    """Gives a phone account another pool proxy. The phone keeps it only if traffic gets through."""
    config, runtime, previous = android_network._account(profile_id)
    serial, user_id = runtime["device_serial"], runtime["android_user_id"]
    client = _client()
    ensure_primary(serial, client)
    entry = next((p for p in proxy_service.load_proxy_pool() if p.get("id") == proxy_id), None)
    if not entry:
        raise ValueError("Selected proxy does not exist")
    if not entry.get("password") and entry.get("held_by_phone"):
        raise ValueError("This PC doesn't have this proxy's password.")
    if entry.get("profile_id") not in (None, profile_id):
        raise ValueError("Selected proxy is already assigned")
    with android_devices.phone_lock(serial):
        tried = _with_user(client, serial, user_id, lambda: agent_call(
            client, serial, user_id, "try_config", TRY_TIMEOUT_S, _proxy_args(entry)))
    if not tried.get("applied"):
        raise AdbError("Traffic didn't get through this proxy, so the phone kept its old one.")
    entry = proxy_service.assign_proxy_by_id(profile_id, proxy_id)
    _link_proxy(profile_id, entry)
    # A placeholder only kept the old proxy from being given away; it can't be used without its password.
    if previous and previous.get("held_by_phone") and not previous.get("password") and previous["id"] != proxy_id:
        proxy_service.delete_proxy(previous["id"])
    try_sync_details(profile_id)
    return android_network.verify(profile_id)


# --- PIN -----------------------------------------------------------------------------------------

def set_pin(serial: str, pin: str | None) -> dict:
    """Sets (or with None clears) the PIN in every set-up Android user of the phone."""
    if pin is not None and not PIN_RE.fullmatch(pin):
        raise ValueError("The PIN is 4 digits.")
    client = _client()
    ensure_primary(serial, client)
    current = stored_pin(serial)
    with android_devices.phone_lock(serial):
        for user_id, _ in _set_up_users(client, serial):
            args = {"current_pin": current or ""}
            if pin is None:
                _with_user(client, serial, user_id, lambda uid=user_id: agent_call(
                    client, serial, uid, "clear_pin", STATUS_TIMEOUT_S, args))
            else:
                _with_user(client, serial, user_id, lambda uid=user_id: agent_call(
                    client, serial, uid, "set_pin", STATUS_TIMEOUT_S, {**args, "pin": pin}))
    _store_pin(serial, pin)
    return {"pin_set": pin is not None}


def apply_pin(client: AdbClient, serial: str, user_id: int) -> None:
    """Gives a newly set-up account the phone's PIN, which this primary PC keeps."""
    pin = stored_pin(serial)
    if pin:
        agent_call(client, serial, user_id, "set_pin", STATUS_TIMEOUT_S, {"pin": pin, "current_pin": pin})
