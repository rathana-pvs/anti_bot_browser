"""Cleaning a phone account: its Android user is kept, but goes back to an empty slot.

Every app's data in that user is cleared (Facebook, the companion app, Chrome, the gallery apps),
the gallery's photos and videos are deleted, and this PC forgets the account and frees its proxy.
The user then looks like one no account was ever set up in, and a new account can be created on
it with a fresh login.

The Android user itself is not recreated, so apps that read the per-user Android ID (SSAID) see
the same value as before.
"""
from __future__ import annotations

import re
import shutil
import threading

from backend.config import PROFILES_DIR
from backend.services import android_devices, android_ownership, android_setup, proxy_service
from backend.services.adb_client import AdbClient, AdbError, validate_package
from backend.services.android_devices import ANDROID_RUNTIME, CONTROL_USER_ID, DeviceNotFound, _client

# System apps that keep an account's traces (logins opened in Chrome, the gallery's own cache).
# Cleared only when installed in that user; every app installed in the user itself is cleared too.
SYSTEM_APPS = ("com.android.chrome", "com.google.android.apps.photos", "com.android.gallery3d",
               "com.sec.android.gallery3d", "com.miui.gallery", "com.coloros.gallery3d", "com.oppo.gallery3d",
               "com.vivo.gallery", "com.huawei.photos")
MEDIA_URI = "content://media/external/file"
DIRECTORY_FORMAT = 12289  # MTP "association": the gallery's own folder rows, which stay.
CLEAR_TIMEOUT_S = 60
_PACKAGE_LINE = re.compile(r"^package:(\S+)$", re.MULTILINE)
_ROW = re.compile(r"^Row: \d+ ", re.MULTILINE)


def parse_packages(output: str) -> list[str]:
    return _PACKAGE_LINE.findall(output or "")


def count_rows(output: str) -> int:
    return len(_ROW.findall(output or ""))


def _media_where(user_id: int) -> str:
    return f"_data LIKE '/storage/emulated/{int(user_id)}/%' AND format!={DIRECTORY_FORMAT}"


def _account(profile_id: str) -> tuple[int, str]:
    runtime = android_devices.read_profile_runtime(profile_id)
    if not runtime or runtime.get("kind") != ANDROID_RUNTIME:
        raise DeviceNotFound("Phone account not found")
    user_id = int(runtime.get("android_user_id"))
    if user_id == CONTROL_USER_ID:
        raise ValueError("The phone owner (user 0) is the control user and is never cleaned.")
    return user_id, runtime["device_serial"]


def _user_gone(client: AdbClient, serial: str, user_id: int, profile_id: str) -> str | None:
    """Why this account's Android user is no longer on the phone, or None while it is."""
    if user_id not in {user.user_id for user in client.users(serial)}:
        return "The Android user is no longer on the phone"
    expected = (android_devices.read_profile_runtime(profile_id) or {}).get("android_user_serial")
    actual = client.user_serials(serial).get(user_id)
    if expected is not None and actual is not None and expected != actual:
        # A recreated user reused the id: wiping it would wipe someone else's account.
        return "The Android user was removed and created again, so it was left alone"
    return None


def _packages(client: AdbClient, serial: str, user_id: int) -> list[str]:
    user = str(int(user_id))
    own = parse_packages(client.shell(serial, "pm", "list", "packages", "-3", "--user", user))
    installed = set(parse_packages(client.shell(serial, "pm", "list", "packages", "--user", user)))
    return [validate_package(name) for name in dict.fromkeys([*own, *(app for app in SYSTEM_APPS if app in installed)])]


def _clear_app(client: AdbClient, serial: str, user_id: int, package: str) -> bool:
    reply = client.shell(serial, "pm", "clear", "--user", str(int(user_id)), package, timeout=CLEAR_TIMEOUT_S)
    return reply.strip().startswith("Success")


def _delete_media(client: AdbClient, serial: str, user_id: int) -> int:
    """Deletes the user's photos, videos and downloads through its gallery database, which also
    removes the files (the shell cannot open another user's storage directly). Returns how many are left."""
    user, where = str(int(user_id)), _media_where(user_id)
    try:
        client.shell(serial, f'content delete --user {user} --uri {MEDIA_URI} --where "{where}"', timeout=120)
    except AdbError:
        pass  # Counted below.
    left = client.shell(serial, f'content query --user {user} --uri {MEDIA_URI} --projection _id --where "{where}"')
    if "Error while accessing provider" in left or "Exception" in left:
        raise AdbError("The phone's gallery could not be read to check that its files were deleted.")
    return count_rows(left)


def _forget_account(profile_id: str) -> None:
    """The same as deleting the account on this PC: its proxy is freed and its folder removed."""
    proxy_service.release_proxy(profile_id)
    shutil.rmtree(PROFILES_DIR / profile_id, ignore_errors=True)


STEPS = (
    ("check", "Check the phone"),
    ("start_user", "Start the Android user"),
    ("apps", "Clear app data"),
    ("gallery", "Delete photos and videos"),
    ("forget", "Delete the account on this PC"),
)
APP_NAMES = {"com.facebook.katana": "Facebook", "com.isolatedbrowser.agent": "Companion app",
             "com.android.chrome": "Chrome", "com.google.android.apps.photos": "Google Photos"}
# profile_id -> progress. In memory: the account's folder is gone once a clean finishes.
_progress: dict[str, dict] = {}
_progress_lock = threading.RLock()


def app_name(package: str) -> str:
    return APP_NAMES.get(package) or ("Gallery" if "gallery" in package or "photos" in package else package)


def _new_progress(profile_id: str, user_id: int, serial: str) -> dict:
    return {"profile_id": profile_id, "device_serial": serial, "android_user_id": user_id,
            "state": "running", "reason": None, "result": None,
            "steps": [{"key": key, "label": label, "status": "pending", "detail": None} for key, label in STEPS]}


def _set(profile_id: str, key: str, status: str, detail: str | None = None) -> None:
    with _progress_lock:
        step = next(step for step in _progress[profile_id]["steps"] if step["key"] == key)
        step.update(status=status, detail=detail)


def _finish(profile_id: str, **changes) -> None:
    with _progress_lock:
        _progress[profile_id].update(**changes)


def progress(profile_id: str) -> dict:
    with _progress_lock:
        state = _progress.get(profile_id)
        if state is None:
            raise DeviceNotFound("No clean has run for this account.")
        return {**state, "steps": [dict(step) for step in state["steps"]]}


def start(profile_id: str, client: AdbClient | None = None, background: bool = True) -> dict:
    """Checks what can be told at once, then cleans in the background; returns progress at once."""
    user_id, serial = _account(profile_id)
    if profile_id in android_setup._running:
        raise ValueError("This account's setup is running. Wait for it to finish, then clean it.")
    if android_devices.job_on(serial) is not None:
        raise android_devices.PhoneBusy(android_devices.busy_message(serial))
    client = client or _client()
    android_ownership.ensure_primary(serial, client)
    with _progress_lock:
        if (_progress.get(profile_id) or {}).get("state") == "running":
            return progress(profile_id)
        _progress[profile_id] = _new_progress(profile_id, user_id, serial)
    if background:
        threading.Thread(target=_run, args=(profile_id, user_id, serial, client),
                         name=f"android-clean-{profile_id}", daemon=True).start()
    else:
        _run(profile_id, user_id, serial, client)
    return progress(profile_id)


def _wipe(profile_id: str, user_id: int, serial: str, client: AdbClient) -> tuple[list[str], int]:
    """Clears every app and the gallery in the user. Hold `phone_lock`."""
    started = False
    try:
        # A stopped user has no gallery to delete from and may not clear cleanly, so start it,
        # and stop it again afterwards unless it is on screen.
        _set(profile_id, "start_user", "running")
        started = android_ownership._ensure_running(client, serial, user_id)
        _set(profile_id, "start_user", "passed", None if started else "Already running")
        _set(profile_id, "apps", "running", "Finding apps")
        packages = _packages(client, serial, user_id)
        failed = []
        for index, package in enumerate(packages, 1):
            _set(profile_id, "apps", "running", f"{app_name(package)} ({index} of {len(packages)})")
            if not _clear_app(client, serial, user_id, package):
                failed.append(app_name(package))
        if failed:
            raise AdbError(f"The phone did not clear {', '.join(failed)}. The account was kept; try Clean again.")
        _set(profile_id, "apps", "passed", ", ".join(app_name(package) for package in packages) or "No apps")
        _set(profile_id, "gallery", "running")
        media_left = _delete_media(client, serial, user_id)
        _set(profile_id, "gallery", "passed", f"{media_left} file{'' if media_left == 1 else 's'} stayed on the phone"
             if media_left else None)
        return packages, media_left
    except AdbError as exc:
        running = next(step["key"] for step in progress(profile_id)["steps"] if step["status"] == "running")
        raise StepFailed(running, str(exc)) from exc
    finally:
        if started and client.current_user(serial) != user_id:
            try:
                client.stop_user(serial, user_id)
            except AdbError:
                pass


class StepFailed(Exception):
    def __init__(self, key: str, reason: str):
        super().__init__(reason)
        self.key = key


def _run(profile_id: str, user_id: int, serial: str, client: AdbClient) -> None:
    key = STEPS[0][0]
    try:
        with android_devices.phone_lock(serial):
            _set(profile_id, key, "running")
            gone = _user_gone(client, serial, user_id, profile_id)
            _set(profile_id, key, "passed", gone)
            packages, media_left = [], 0
            if gone:
                # Nothing left to wipe on the phone: only the account on this PC is removed.
                for key in ("start_user", "apps", "gallery"):
                    _set(profile_id, key, "passed", "Skipped")
            else:
                packages, media_left = _wipe(profile_id, user_id, serial, client)
        key = "forget"
        _set(profile_id, key, "running")
        _forget_account(profile_id)
        if not gone:
            # The scan would otherwise show the companion's old account and proxy, saved before.
            android_devices.forget_saved_agent(serial, user_id)
        try:
            android_devices.scan_device(serial)
        except (AdbError, ValueError, DeviceNotFound):
            pass  # The next scan shows the user as empty.
        _set(profile_id, key, "passed")
        _finish(profile_id, state="done", result={"cleared_apps": packages, "media_left": media_left})
    except Exception as exc:  # Every failure ends the run with its reason on the step.
        if isinstance(exc, StepFailed):
            key = exc.key
        reason = str(exc) if isinstance(exc, (StepFailed, AdbError, ValueError, DeviceNotFound)) else (
            f"Unexpected error: {exc.__class__.__name__}")
        _set(profile_id, key, "failed", reason)
        _finish(profile_id, state="failed", reason=reason)
