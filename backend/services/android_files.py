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


def list_files(profile_id: str, client: AdbClient | None = None) -> dict:
    """The files copied for this account, and whether it is on the phone's screen now (None when the
    phone can't be asked), since only then can more be sent."""
    runtime = _runtime(profile_id)
    with _records_lock:
        files = _load(profile_id)
    try:
        on_screen = _on_screen(client or android_devices._client(), runtime["device_serial"], int(runtime["android_user_id"]))
    except AdbError:
        on_screen = None
    return {"files": sorted(files, key=lambda item: item.get("copied_at") or "", reverse=True), "on_screen": on_screen}


def _phone_lock(serial: str) -> threading.Lock:
    with _phone_locks_guard:
        return _phone_locks.setdefault(serial, threading.Lock())


NOT_ON_SCREEN = ("This account isn't on the phone's screen. Switch to it first (Devices page, Switch to), "
                 "then send the files.")


def _on_screen(client: AdbClient, serial: str, user_id: int) -> bool:
    return client.current_user(serial) == user_id


def _ensure_on_screen(client: AdbClient, serial: str, user_id: int) -> None:
    """Files go only to the user on screen. A stopped user cannot take them (its storage is locked and
    its gallery is not running), and starting one in the background could wake its Facebook on
    another account's time zone, so it is never started for a copy."""
    if not any(user.user_id == user_id for user in client.users(serial)):
        raise AdbError("This account's Android user is no longer on the phone.")
    if not _on_screen(client, serial, user_id):
        raise AdbError(NOT_ON_SCREEN)


def _ensure_phone_free(serial: str) -> None:
    if android_devices.job_on(serial):
        raise AdbError("The phone is running a job. Copy the files when it finishes.")


class GalleryRefused(AdbError):
    """The phone's gallery (MediaProvider) refused the companion app's save."""


# How the phone's gallery database reports a Java error raised inside one of its own SQL functions.
# The Java exception itself only goes to the phone's log (tag SQLiteConnection), so it is read from there.
_GALLERY_DB_ERROR = re.compile(r"custom scalar function|SQLITE_|SQLiteException", re.IGNORECASE)
_JAVA_EXCEPTION = re.compile(r"((?:[a-z][\w$]*\.)+[A-Z][\w$]*(?:Exception|Error))(?::\s*(.*))?")
CAUSE_LOG_LINES = 600
RETRY_DELAY_S = 2.0


def gallery_cause(log: str) -> str | None:
    """The Java exception the phone's media provider logged for the last failed gallery write, from
    `logcat -v brief SQLiteConnection:E MediaProvider:E *:S`. Only what follows the last report of
    the error counts, so an older unrelated error is not blamed. Stack frames (`at ...`) are skipped;
    the last exception wins, which is the innermost `Caused by`."""
    lines = log.splitlines()
    marks = [index for index, line in enumerate(lines) if "custom scalar function" in line]
    if marks:
        lines = lines[marks[-1] + 1:]
    cause = None
    for line in lines:
        text = line.split("): ", 1)[1] if "): " in line else line
        text = text.strip()
        if text.startswith("at ") or text.startswith("..."):
            continue
        match = _JAVA_EXCEPTION.search(text)
        if match:
            kind, message = match.group(1).rsplit(".", 1)[-1], (match.group(2) or "").strip()
            cause = f"{kind}: {message}"[:300] if message else kind
    return cause


def _read_gallery_cause(client: AdbClient, serial: str) -> str | None:
    try:
        log = client.shell(serial, "logcat", "-d", "-t", str(CAUSE_LOG_LINES), "-v", "brief",
                           "SQLiteConnection:E", "MediaProvider:E", "'*:S'", timeout=15)
    except AdbError:
        return None
    return gallery_cause(log)


def _wait_saved(client: AdbClient, serial: str, user_id: int, name: str) -> dict:
    deadline = time.monotonic() + SAVE_TIMEOUT_S
    while time.monotonic() < deadline:
        status = agent_call(client, serial, user_id, "media_status", AGENT_TIMEOUT_S, {"name": name})
        if status.get("state") == "saved":
            return status
        if status.get("state") == "failed":
            error = status.get("error") or "unknown error"
            if _GALLERY_DB_ERROR.search(error):
                raise GalleryRefused(error)
            raise AdbError(f"The phone could not save the file: {error}")
        time.sleep(0.5)
    raise AdbError("The phone did not finish saving the file.")


def _save_to_gallery(client: AdbClient, serial: str, user_id: int, local: Path, original_name: str,
                     mime: str, size: int) -> tuple[str, str, dict]:
    """Streams the file to the companion app and waits for the gallery to take it. A gallery database
    error is tried once more under a fresh name (the app deletes the half-made entry itself); if it
    happens again, the phone's own logged reason is reported, since the database error alone says
    nothing about the cause."""
    timeout = max(120.0, size / (2 * 1024 ** 2))
    for attempt in (1, 2):
        token = secrets.token_hex(6)
        name = phone_name(original_name, token)
        uri = f"content://{AGENT_AUTHORITY}/media?name={name}&mime={mime}"
        client.write_content(serial, user_id, uri, str(local), timeout=timeout)
        try:
            return token, name, _wait_saved(client, serial, user_id, name)
        except GalleryRefused as error:
            if attempt == 2:
                cause = _read_gallery_cause(client, serial)
                detail = f"{error} (phone's reason: {cause})" if cause else str(error)
                raise AdbError(f"The phone's gallery refused the file twice: {detail}") from None
            time.sleep(RETRY_DELAY_S)
    raise AssertionError("unreachable")


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
    lock = _phone_lock(serial)
    # One copy at a time per phone; a second waits its turn rather than failing.
    if not lock.acquire(timeout=SAVE_TIMEOUT_S * 2):
        raise AdbError("Another copy to this phone is taking too long. Try again shortly.")
    try:
        _ensure_phone_free(serial)
        _ensure_on_screen(client, serial, user_id)
        free = client.free_storage_mb(serial)
        if free is not None and free - size / 1024 ** 2 < SPARE_MB:
            raise AdbError(f"The phone has only {free} MB free. Make room on it first.")
        token, name, saved = _save_to_gallery(client, serial, user_id, local, original_name, mime, size)
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


def copy_local_file(profile_id: str, path: str, client: AdbClient | None = None) -> dict:
    """A photo or video dropped onto the app from this computer, by its path: Linux's web view hands a
    dropped file over as a file:// address only. Only an existing regular JPEG, PNG or MP4 is copied."""
    local = Path(path)
    if not local.is_absolute():
        raise ValueError("The dropped file's location could not be read. Click the box to choose it instead.")
    mime_for(local.name)
    try:
        resolved = local.resolve(strict=True)
    except OSError:
        raise ValueError(f"{local.name} could not be found on this computer.") from None
    if not resolved.is_file():
        raise ValueError(f"{local.name} is not a file.")
    return copy_file(profile_id, resolved, local.name, client)


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
        _ensure_on_screen(client, serial, user_id)
        reply = agent_call(client, serial, user_id, "delete_media", AGENT_TIMEOUT_S, {"uri": record["uri"]})
    finally:
        lock.release()
    with _records_lock:
        _save(profile_id, [item for item in _load(profile_id) if item.get("id") != file_id])
    return {"removed": file_id, "deleted_on_phone": bool(reply.get("deleted"))}
