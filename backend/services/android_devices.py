"""Inventory of physical Android phones and import of Android users as profiles.

A phone is a device; each Android user on it can become one profile. Milestone 1
covers inventory, scanning and import only: Android profiles are created with
automation disabled and never reach the Docker lifecycle.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR, PROFILES_DIR
from backend.services.adb_client import AdbClient, AdbError, FACEBOOK_PACKAGE, validate_serial
from backend.services.profile_service import (
    DEFAULT_RESOURCE_LIMITS, PROFILE_SCHEMA_VERSION, allocate_profile_identity, atomic_write_json,
    compatibility_fingerprint, creation_lock, resolve_requested_environment, utc_now,
)

DEVICES_FILE = DATA_DIR / "android_devices.json"
ANDROID_RUNTIME = "android_user"
CONTROL_USER_ID = 0
AUTOMATION_UNAVAILABLE = "android_automation_unavailable"
SWITCH_READY_TIMEOUT_S = 15.0
SWITCH_POLL_S = 0.5
_store_lock = threading.Lock()
_switch_locks: dict[str, threading.Lock] = {}


class DeviceNotFound(LookupError):
    pass


def _client() -> AdbClient:
    return AdbClient()


def load_store() -> dict[str, Any]:
    try:
        data = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("devices"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"devices": {}}


def _save_store(store: dict[str, Any]) -> None:
    atomic_write_json(DEVICES_FILE, store)


def read_profile_runtime(profile_id: str, root: Path | None = None) -> dict | None:
    try:
        config = json.loads(((root or PROFILES_DIR) / profile_id / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    runtime = config.get("runtime")
    return runtime if isinstance(runtime, dict) else None


def is_android_profile(profile_id: str) -> bool:
    runtime = read_profile_runtime(profile_id)
    return bool(runtime and runtime.get("kind") == ANDROID_RUNTIME)


def ensure_not_android(profile_id: str, action: str) -> None:
    """Android profiles have no container; refuse container and automation paths."""
    if is_android_profile(profile_id):
        raise ValueError(f"{action} is not available for Android phone profiles yet.")


def linked_profiles(root: Path | None = None) -> list[dict]:
    root = root or PROFILES_DIR
    linked = []
    if not root.exists():
        return linked
    for entry in root.iterdir():
        config_path = entry / "config.json"
        if not entry.is_dir() or not config_path.exists():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runtime = config.get("runtime") or {}
        if runtime.get("kind") == ANDROID_RUNTIME:
            linked.append({"profile_id": config.get("id") or entry.name, "name": config.get("name") or entry.name,
                           "runtime": runtime})
    return linked


def _profile_for(links: list[dict], serial: str, user_id: int, user_serial: int | None) -> dict | None:
    for link in links:
        runtime = link["runtime"]
        if runtime.get("device_serial") != serial or runtime.get("android_user_id") != user_id:
            continue
        stored_serial = runtime.get("android_user_serial")
        # A recreated Android user can reuse an id; it must not inherit the old profile.
        if stored_serial is not None and user_serial is not None and stored_serial != user_serial:
            continue
        return link
    return None


def _health(client: AdbClient, serial: str) -> dict:
    health: dict[str, Any] = {}
    for key, read in (("battery", client.battery), ("free_storage_mb", client.free_storage_mb),
                      ("current_user", client.current_user), ("max_users", client.max_users),
                      ("facebook_version", client.package_version)):
        try:
            health[key] = read(serial)
        except AdbError:
            health[key] = None
    return health


def list_devices() -> dict:
    client = _client()
    adb_error = None
    connected: dict[str, dict] = {}
    try:
        connected = {device["serial"]: device for device in client.devices()}
    except AdbError as exc:
        adb_error = str(exc)
    with _store_lock:
        store = load_store()
        changed = False
        for serial, device in connected.items():
            if device["state"] == "device" and serial not in store["devices"]:
                store["devices"][serial] = {"serial": serial, "label": device.get("model") or serial,
                                            "first_seen": utc_now()}
                changed = True
            if serial in store["devices"] and device["state"] == "device":
                store["devices"][serial]["last_seen"] = utc_now()
                changed = True
        if changed:
            _save_store(store)
    links = linked_profiles()
    devices = []
    serials = list(store["devices"]) + [s for s in connected if s not in store["devices"]]
    for serial in serials:
        saved = store["devices"].get(serial, {"serial": serial, "label": serial})
        live = connected.get(serial)
        state = live["state"] if live else "offline"
        entry = {**saved, "state": state, "connection": (live or {}).get("connection"),
                 "model": saved.get("model") or (live or {}).get("model"),
                 "profile_count": sum(1 for link in links if link["runtime"].get("device_serial") == serial),
                 "health": None}
        if state == "device":
            entry["health"] = _health(client, serial)
        devices.append(entry)
    return {"adb_available": adb_error is None, "adb_error": adb_error, "devices": devices}


def scan_device(serial: str) -> dict:
    validate_serial(serial)
    client = _client()
    connected = {device["serial"]: device for device in client.devices()}
    live = connected.get(serial)
    if not live:
        raise DeviceNotFound("This phone is not connected. Check the USB cable and that USB debugging is on.")
    if live["state"] != "device":
        raise AdbError("Allow USB debugging on the phone, then scan again." if live["state"] == "unauthorized"
                       else f"The phone is {live['state']}. Reconnect it and scan again.")
    properties = client.properties(serial)
    users = client.users(serial)
    user_serials = client.user_serials(serial)
    current = client.current_user(serial)
    links = linked_profiles()
    rows = []
    for user in users:
        user_serial = user_serials.get(user.user_id)
        try:
            facebook_installed = client.package_installed(serial, user.user_id, FACEBOOK_PACKAGE)
        except AdbError:
            facebook_installed = None
        link = _profile_for(links, serial, user.user_id, user_serial)
        rows.append({
            "user_id": user.user_id, "name": user.name, "user_serial": user_serial,
            "running": user.running, "current": user.user_id == current,
            "control_user": user.user_id == CONTROL_USER_ID,
            "facebook_installed": facebook_installed,
            "profile_id": link["profile_id"] if link else None,
            "profile_name": link["name"] if link else None,
        })
    scan = {"scanned_at": utc_now(), "current_user": current, "users": rows}
    with _store_lock:
        store = load_store()
        saved = store["devices"].setdefault(serial, {"serial": serial, "first_seen": utc_now()})
        saved.update({key: value for key, value in properties.items() if value is not None})
        saved.setdefault("label", properties.get("model") or serial)
        saved.update(last_seen=utc_now(), last_scan=scan)
        _save_store(store)
    return {**saved, "state": live["state"], "connection": live["connection"]}


def rename_device(serial: str, label: str) -> dict:
    validate_serial(serial)
    label = label.strip()
    if not label or len(label) > 80:
        raise ValueError("Device name must be 1-80 characters")
    with _store_lock:
        store = load_store()
        if serial not in store["devices"]:
            raise DeviceNotFound("Device not found")
        store["devices"][serial]["label"] = label
        _save_store(store)
        return store["devices"][serial]


def forget_device(serial: str) -> None:
    validate_serial(serial)
    if any(link["runtime"].get("device_serial") == serial for link in linked_profiles()):
        raise ValueError("Delete or move this phone's accounts before removing the phone.")
    with _store_lock:
        store = load_store()
        if store["devices"].pop(serial, None) is None:
            raise DeviceNotFound("Device not found")
        _save_store(store)


def user_readiness(client: AdbClient, serial: str, user_id: int) -> dict:
    """Ready means: user N is current and running, the screen is awake, and no lock screen shows.

    `am get-current-user` reports the new user before it is running, and a
    switched-to user starts with the screen off and the lock screen up, so all
    four are checked. An unreadable value counts as not ready.
    """
    current = client.current_user(serial)
    running = next((user.running for user in client.users(serial) if user.user_id == user_id), False)
    awake = client.screen_awake(serial)
    locked = client.lock_screen_showing(serial) if awake else None
    if current != user_id:
        reason = "The phone has not switched to this user yet."
    elif not running:
        reason = "The Android user is still starting."
    elif awake is None:
        reason = "Couldn’t read whether the phone’s screen is on."
    elif not awake:
        reason = "The phone’s screen is off."
    elif locked is None:
        reason = "Couldn’t read the phone’s lock screen state."
    elif locked:
        reason = "The lock screen is showing. Remove the PIN or pattern lock for this user; automation never enters it."
    else:
        reason = None
    return {"current_user": current, "running": running, "screen_awake": awake,
            "lock_screen_showing": locked, "ready": reason is None, "reason": reason}


def switch_user(serial: str, user_id: int) -> dict:
    validate_serial(serial)
    client = _client()
    if user_id not in {user.user_id for user in client.users(serial)}:
        raise DeviceNotFound("This Android user does not exist on the phone")
    with _store_lock:
        lock = _switch_locks.setdefault(serial, threading.Lock())
    if not lock.acquire(blocking=False):
        raise AdbError("This phone is already switching users. Wait for it to finish.")
    try:
        client.switch_user(serial, user_id)
        deadline = time.monotonic() + SWITCH_READY_TIMEOUT_S
        while True:
            state = user_readiness(client, serial, user_id)
            if state["ready"] or time.monotonic() >= deadline:
                return state
            if state["current_user"] == user_id and state["running"]:
                if state["screen_awake"] is False:
                    client.wake_screen(serial)
                elif state["lock_screen_showing"]:
                    client.dismiss_lock_screen(serial)
            time.sleep(SWITCH_POLL_S)
    finally:
        lock.release()


def _android_profile(profile_id: str, name: str, group: str, serial: str, user: dict) -> dict:
    requested = resolve_requested_environment(
        {"screen_resolution": "1920x1080", "timezone_policy": "host", "language": "en-US",
         "user_agent_policy": "browser_default", "user_agent": None, "rendering_mode": "host_gpu"},
        network_mode="direct", require_timezone_confirmation=False,
    )
    now = utc_now()
    return {
        "schema_version": PROFILE_SCHEMA_VERSION, "configuration_revision": 1,
        "id": profile_id, "name": name, "group": group, "status": "stopped",
        "created_at": now, "updated_at": now,
        "runtime": {
            "kind": ANDROID_RUNTIME, "device_serial": serial,
            "android_user_id": user["user_id"], "android_user_serial": user.get("user_serial"),
            "android_user_name": user.get("name"),
        },
        # The phone's own network is used until the companion app sets up a proxy.
        "network": {"mode": "direct", "proxy_id": None, "proxy_type": "socks5", "proxy_host": "",
                    "proxy_port": None, "proxy_user": "", "proxy_pass": ""},
        "android_network": {"status": "not_set_up"},
        "requested_environment": requested,
        "effective_environment": None, "observed_environment": None,
        "restart_required": False,
        "resources": dict(DEFAULT_RESOURCE_LIMITS), "effective_resources": None,
        "behavior_mode": "medium", "behavior_seed": secrets.randbits(63) or 1,
        "automation": {"reel_template": "auto", "enabled": False, "reason": AUTOMATION_UNAVAILABLE},
        "fingerprint": compatibility_fingerprint(requested),
        "container": {"id": None, "vnc_port": None, "ws_port": None, "volume_path": None},
        "account": {
            "platform": "facebook", "email": "", "notes": "",
            "warming_start_date": None, "warming_complete": False,
            "warming_week": 1, "posts_today": 0, "last_post_date": None,
        },
    }


def import_user(serial: str, user_id: int, name: str, group: str = "") -> dict:
    validate_serial(serial)
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError("Account name must be 1-120 characters")
    if user_id == CONTROL_USER_ID:
        raise ValueError("The phone owner (user 0) is the control user and cannot hold an account.")
    store = load_store()
    scan = (store["devices"].get(serial) or {}).get("last_scan")
    if not scan:
        raise DeviceNotFound("Scan the phone before importing its users.")
    user = next((row for row in scan["users"] if row["user_id"] == user_id), None)
    if not user:
        raise DeviceNotFound("This Android user was not found in the last scan. Scan the phone again.")
    with creation_lock():
        if _profile_for(linked_profiles(), serial, user_id, user.get("user_serial")):
            raise ValueError("This Android user is already linked to an account.")
        profile_id, _, _ = allocate_profile_identity(PROFILES_DIR)
        profile_dir = PROFILES_DIR / profile_id
        profile = _android_profile(profile_id, name, group.strip(), serial, user)
        profile_dir.mkdir(parents=True, exist_ok=False)
        atomic_write_json(profile_dir / "config.json", profile)
    return profile
