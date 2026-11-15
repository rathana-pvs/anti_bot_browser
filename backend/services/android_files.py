"""Photos and videos a person copies by hand into one phone account's gallery.

This has nothing to do with posting: the file is for the person to use on the phone themselves,
for example as a profile picture or cover photo. The companion app saves it into the account's
own Android user (other accounts on the phone cannot see it), and it stays until removed here.
Posting cleanup only deletes a post's own media, never these files.

Copying never switches the phone's screen. A stopped Android user is started in the background
for the copy (its always-on VPN in lockdown keeps traffic on the account's proxy) and stopped
again afterwards, so the phone is left as it was. Nothing is copied while the phone runs a job.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path

from backend.config import PROFILES_DIR
from backend.services import android_devices
from backend.services.adb_client import AdbClient, AdbError
from backend.services.android_agent import AGENT_AUTHORITY, agent_call
from backend.services.android_devices import ANDROID_RUNTIME, DeviceNotFound
from backend.services.profile_service import atomic_write_json, utc_now

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}
MAX_BYTES = 2 * 1024 ** 3
# Room left on the phone after the copy, so it never fills up.
SPARE_MB = 500
AGENT_TIMEOUT_S = 15.0
SAVE_TIMEOUT_S = 120.0
FILES_NAME = "phone_files.json"
_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_FILE_ID = re.compile(r"[0-9a-f]{12}")
_records_lock = threading.Lock()
_phone_locks: dict[str, threading.Lock] = {}
_phone_locks_guard = threading.Lock()


def mime_for(filename: str) -> str:
    mime = MIME.get(Path(filename).suffix.lower())
    if not mime:
        raise ValueError(f"{Path(filename).name or 'This file'} is not a JPEG, PNG or MP4. Only photos and videos can be copied.")
    return mime


def phone_name(original: str, token: str) -> str:
    """A gallery name the companion app accepts (letters, digits, `._-`, at most 80) and that is
    unique, since the app tracks each write by its name."""
    path = Path(original.replace("\\", "/"))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._-")[:60] or "file"
    return f"{stem}_{token[:6]}{path.suffix.lower()}"


def _runtime(profile_id: str) -> dict:
    if not _PROFILE_ID.fullmatch(profile_id or ""):
        raise DeviceNotFound("Account not found")
    runtime = android_devices.read_profile_runtime(profile_id)
    if runtime is None:
        if not (PROFILES_DIR / profile_id / "config.json").is_file():
            raise DeviceNotFound("Account not found")
        raise ValueError("This account is not on a phone.")
    if runtime.get("kind") != ANDROID_RUNTIME or runtime.get("android_user_id") is None:
        raise ValueError("This account is not on a phone.")
    return runtime


def _records_path(profile_id: str) -> Path:
    return PROFILES_DIR / profile_id / FILES_NAME


def _load(profile_id: str) -> list[dict]:
    try:
        data = json.loads(_records_path(profile_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    return [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []


def _save(profile_id: str, files: list[dict]) -> None:
    atomic_write_json(_records_path(profile_id), {"files": files})


def list_files(profile_id: str) -> dict:
    _runtime(profile_id)
    with _records_lock:
        files = _load(profile_id)
    return {"files": sorted(files, key=lambda item: item.get("copied_at") or "", reverse=True)}


def _phone_lock(serial: str) -> threading.Lock:
    with _phone_locks_guard:
        return _phone_locks.setdefault(serial, threading.Lock())


class _UserAwake:
    """Holds the account's Android user running for the copy, and stops it again afterwards
    if it was stopped before and is not the one on screen by then."""

    def __init__(self, client: AdbClient, serial: str, user_id: int):
        self.client, self.serial, self.user_id = client, serial, user_id
        self.started = False

    def __enter__(self):
        running = next((user.running for user in self.client.users(self.serial) if user.user_id == self.user_id), None)
        if running is None:
            raise AdbError("This account's Android user is no longer on the phone.")
        if not running:
            self.client.start_user(self.serial, self.user_id)
            self.started = True
        return self

    def __exit__(self, *_exc):
        if not self.started:
            return False
        try:
            if self.client.current_user(self.serial) != self.user_id:
                self.client.stop_user(self.serial, self.user_id)
        except AdbError:
            pass  # A job may have switched or stopped users meanwhile; it owns the phone now.
        return False


def _ensure_phone_free(serial: str) -> None:
    if android_devices.job_on(serial):
        raise AdbError("The phone is running a job. Copy the files when it finishes.")


def _wait_saved(client: AdbClient, serial: str, user_id: int, name: str) -> dict:
    deadline = time.monotonic() + SAVE_TIMEOUT_S
    while time.monotonic() < deadline:
        status = agent_call(client, serial, user_id, "media_status", AGENT_TIMEOUT_S, {"name": name})
        if status.get("state") == "saved":
            return status
        if status.get("state") == "failed":
            raise AdbError(f"The phone could not save the file: {status.get('error') or 'unknown error'}")
        time.sleep(0.5)
    raise AdbError("The phone did not finish saving the file.")


def copy_file(profile_id: str, local: Path, original_name: str, client: AdbClient | None = None) -> dict:
    """Copies one photo or video into the account's gallery and remembers it."""
    runtime = _runtime(profile_id)
    mime = mime_for(original_name)
    size = local.stat().st_size
    if size <= 0:
        raise ValueError(f"{original_name} is empty.")
    if size > MAX_BYTES:
        raise ValueError(f"{original_name} is larger than 2 GB.")
    serial, user_id = runtime["device_serial"], int(runtime["android_user_id"])
    client = client or android_devices._client()
    token = secrets.token_hex(6)
    name = phone_name(original_name, token)
    lock = _phone_lock(serial)
    # One copy at a time per phone; a second waits its turn rather than failing.
    if not lock.acquire(timeout=SAVE_TIMEOUT_S * 2):
        raise AdbError("Another copy to this phone is taking too long. Try again shortly.")
    try:
        _ensure_phone_free(serial)
        free = client.free_storage_mb(serial)
        if free is not None and free - size / 1024 ** 2 < SPARE_MB:
            raise AdbError(f"The phone has only {free} MB free. Make room on it first.")
        with _UserAwake(client, serial, user_id):
            uri = f"content://{AGENT_AUTHORITY}/media?name={name}&mime={mime}"
            client.write_content(serial, user_id, uri, str(local), timeout=max(120.0, size / (2 * 1024 ** 2)))
            saved = _wait_saved(client, serial, user_id, name)
    finally:
        lock.release()
    record = {"id": token, "name": name, "original_name": Path(original_name.replace("\\", "/")).name[:200],
              "mime": mime, "size_bytes": int(saved.get("size") or size), "uri": saved["uri"],
              "album": "Movies" if mime.startswith("video/") else "Pictures", "copied_at": utc_now()}
    with _records_lock:
        files = _load(profile_id)
        files.append(record)
        _save(profile_id, files)
    return record


def remove_file(profile_id: str, file_id: str, client: AdbClient | None = None) -> dict:
    """Deletes a copied file from the phone and forgets it. A file already deleted on the phone
    itself is simply forgotten."""
    runtime = _runtime(profile_id)
    if not _FILE_ID.fullmatch(file_id or ""):
        raise DeviceNotFound("File not found")
    with _records_lock:
        record = next((item for item in _load(profile_id) if item.get("id") == file_id), None)
    if record is None:
        raise DeviceNotFound("File not found")
    serial, user_id = runtime["device_serial"], int(runtime["android_user_id"])
    client = client or android_devices._client()
    lock = _phone_lock(serial)
    if not lock.acquire(timeout=SAVE_TIMEOUT_S * 2):
        raise AdbError("Another copy to this phone is taking too long. Try again shortly.")
    try:
        _ensure_phone_free(serial)
        with _UserAwake(client, serial, user_id):
            reply = agent_call(client, serial, user_id, "delete_media", AGENT_TIMEOUT_S, {"uri": record["uri"]})
    finally:
        lock.release()
    with _records_lock:
        _save(profile_id, [item for item in _load(profile_id) if item.get("id") != file_id])
    return {"removed": file_id, "deleted_on_phone": bool(reply.get("deleted"))}
