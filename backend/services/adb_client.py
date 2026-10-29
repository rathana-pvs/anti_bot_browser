"""Thin ADB wrapper for physical Android phones.

Parsers are pure functions so they can be tested with recorded output. Every
command runs against one explicit serial; nothing here talks to "the" device.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
FACEBOOK_PACKAGE = "com.facebook.katana"
DEFAULT_TIMEOUT = 15.0

_USER_RE = re.compile(r"UserInfo\{(\d+):(.*):([0-9a-fA-F]+)\}(\s+running)?")
_USER_SERIAL_RE = re.compile(r"UserInfo\{(\d+):.*?\}\s+serialNo=(\d+)")


class AdbError(RuntimeError):
    pass


class AdbUnavailable(AdbError):
    pass


@dataclass(frozen=True)
class AndroidUser:
    user_id: int
    name: str
    flags: int
    running: bool


def adb_path() -> str | None:
    configured = os.environ.get("ADB_PATH")
    if configured:
        return configured if os.path.isfile(configured) else None
    return shutil.which("adb")


def validate_serial(serial: str) -> str:
    if not SERIAL_RE.fullmatch(serial or ""):
        raise ValueError("Invalid device serial")
    return serial


def parse_devices(output: str) -> list[dict]:
    """Parse `adb devices -l`. State is device, unauthorized, offline, etc."""
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2 or not SERIAL_RE.fullmatch(parts[0]):
            continue
        details = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
        devices.append({
            "serial": parts[0],
            "state": parts[1],
            "connection": "usb" if "usb" in details else ("wifi" if ":" in parts[0] else "unknown"),
            "model": (details.get("model") or "").replace("_", " ") or None,
        })
    return devices


def parse_users(output: str) -> list[AndroidUser]:
    users = []
    for match in _USER_RE.finditer(output):
        users.append(AndroidUser(
            user_id=int(match.group(1)), name=match.group(2),
            flags=int(match.group(3), 16), running=bool(match.group(4)),
        ))
    return users


def parse_user_serials(dumpsys_user: str) -> dict[int, int]:
    return {int(uid): int(serial) for uid, serial in _USER_SERIAL_RE.findall(dumpsys_user)}


def parse_package_installed(output: str, package: str) -> bool:
    return any(line.strip() == f"package:{package}" for line in output.splitlines())


def parse_version_name(dumpsys_package: str) -> str | None:
    match = re.search(r"versionName=(\S+)", dumpsys_package)
    return match.group(1) if match else None


def parse_battery(output: str) -> dict:
    values = {}
    for line in output.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip().casefold()] = value.strip()
    level = values.get("level")
    temperature = values.get("temperature")
    charging = any(values.get(key) == "true" for key in ("ac powered", "usb powered", "wireless powered"))
    return {
        "level": int(level) if level and level.isdigit() else None,
        "temperature_c": int(temperature) / 10 if temperature and temperature.lstrip("-").isdigit() else None,
        "charging": charging,
    }


def parse_free_storage_mb(df_output: str) -> int | None:
    lines = [line for line in df_output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    if len(parts) < 4 or not parts[3].isdigit():
        return None
    return int(parts[3]) // 1024


def parse_total_storage_mb(df_output: str) -> int | None:
    lines = [line for line in df_output.splitlines() if line.strip()]
    parts = lines[-1].split() if len(lines) >= 2 else []
    return int(parts[1]) // 1024 if len(parts) >= 2 and parts[1].isdigit() else None


def parse_memory(meminfo: str) -> dict | None:
    """RAM in use, counted like the PC: total minus what Android reports as available."""
    values = {}
    for line in meminfo.splitlines():
        match = re.match(r"(MemTotal|MemAvailable):\s+(\d+)\s*kB", line.strip())
        if match:
            values[match.group(1)] = int(match.group(2)) // 1024
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return {"used_mb": values["MemTotal"] - values["MemAvailable"], "total_mb": values["MemTotal"]}


def parse_cpu_percent(top_output: str) -> int | None:
    """Share of all cores in use, from toybox top's summary line, e.g. `800%cpu ... 622%idle`."""
    match = re.search(r"(\d+)%cpu\b.*?(\d+)%idle", top_output)
    if not match or int(match.group(1)) <= 0:
        return None
    total, idle = int(match.group(1)), int(match.group(2))
    return max(0, min(100, round((total - idle) * 100 / total)))


def parse_screen_awake(dumpsys_power: str) -> bool | None:
    """Screen state from `dumpsys power`; None when the format is not recognised.

    Android 17 prints `mWakefulness=Awake` globally and `mWakefulness=1` per
    display group; the named value wins, and 1 is WAKEFULNESS_AWAKE.
    """
    values = re.findall(r"mWakefulness=(\w+)", dumpsys_power)
    named = [value for value in values if not value.isdigit()]
    if named:
        return named[0] == "Awake"
    if values:
        return values[0] == "1"
    match = re.search(r"Display Power: state=(\w+)", dumpsys_power)
    return match.group(1) == "ON" if match else None


def parse_lock_screen_showing(output: str) -> bool | None:
    """Lock screen state from the keyguard lines of `dumpsys activity`/`window`.

    `mKeyguardShowing` (activity KeyguardController) wins; the window policy
    flags are a fallback for ROMs that do not print it. None when neither appears.
    """
    primary = re.findall(r"mKeyguardShowing=(true|false)", output)
    if primary:
        return "true" in primary
    fallback = re.findall(r"(?:isKeyguardShowing|mShowingLockscreen|mDreamingLockscreen|isStatusBarKeyguard)=(true|false)", output)
    return "true" in fallback if fallback else None


def parse_int(output: str) -> int | None:
    match = re.search(r"(-?\d+)", output)
    return int(match.group(1)) if match else None


class AdbClient:
    def __init__(self, binary: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.binary = binary or adb_path()
        self.timeout = timeout

    def _run(self, args: list[str], timeout: float | None = None) -> str:
        if not self.binary:
            raise AdbUnavailable("ADB is not installed. Install Android platform-tools and reopen the app.")
        try:
            result = subprocess.run(
                [self.binary, *args], capture_output=True, text=True,
                timeout=timeout or self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB timed out: {' '.join(args[:3])}") from exc
        except OSError as exc:
            raise AdbUnavailable(f"ADB could not be started: {exc}") from exc
        if result.returncode != 0:
            raise AdbError((result.stderr or result.stdout).strip() or f"ADB failed: {' '.join(args[:3])}")
        return result.stdout

    def shell(self, serial: str, *command: str, timeout: float | None = None) -> str:
        return self._run(["-s", validate_serial(serial), "shell", *command], timeout)

    def devices(self) -> list[dict]:
        return parse_devices(self._run(["devices", "-l"]))

    def properties(self, serial: str) -> dict:
        names = ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk")
        values = [line.strip() for line in self.shell(serial, " ; ".join(f"getprop {name}" for name in names)).splitlines()]
        values += [""] * (len(names) - len(values))
        return {
            "manufacturer": values[0] or None, "model": values[1] or None,
            "android_version": values[2] or None,
            "sdk": int(values[3]) if values[3].isdigit() else None,
        }

    def users(self, serial: str) -> list[AndroidUser]:
        return parse_users(self.shell(serial, "pm", "list", "users"))

    def user_serials(self, serial: str) -> dict[int, int]:
        return parse_user_serials(self.shell(serial, "dumpsys", "user"))

    def current_user(self, serial: str) -> int | None:
        return parse_int(self.shell(serial, "am", "get-current-user"))

    def max_users(self, serial: str) -> int | None:
        return parse_int(self.shell(serial, "pm", "get-max-users"))

    def package_installed(self, serial: str, user_id: int, package: str = FACEBOOK_PACKAGE) -> bool:
        return parse_package_installed(
            self.shell(serial, "pm", "list", "packages", "--user", str(int(user_id)), package), package)

    def package_version(self, serial: str, package: str = FACEBOOK_PACKAGE) -> str | None:
        return parse_version_name(self.shell(serial, "dumpsys", "package", package))

    def battery(self, serial: str) -> dict:
        return parse_battery(self.shell(serial, "dumpsys", "battery"))

    def free_storage_mb(self, serial: str) -> int | None:
        return parse_free_storage_mb(self.shell(serial, "df", "-k", "/data"))

    def total_storage_mb(self, serial: str) -> int | None:
        return parse_total_storage_mb(self.shell(serial, "df", "-k", "/data"))

    def memory(self, serial: str) -> dict | None:
        return parse_memory(self.shell(serial, "cat", "/proc/meminfo"))

    def cpu_percent(self, serial: str) -> int | None:
        return parse_cpu_percent(self.shell(serial, "top", "-b", "-n", "1", "-m", "1"))

    def switch_user(self, serial: str, user_id: int) -> None:
        self.shell(serial, "am", "switch-user", str(int(user_id)), timeout=30)

    def push(self, serial: str, local: str, remote: str) -> None:
        self._run(["-s", validate_serial(serial), "push", local, remote], timeout=60)

    def forward(self, serial: str, remote: str) -> int:
        """Forward a free loopback port to `remote` and return the port."""
        port = parse_int(self._run(["-s", validate_serial(serial), "forward", "tcp:0", remote]))
        if not port:
            raise AdbError("ADB did not report a forwarded port")
        return port

    def remove_forward(self, serial: str, port: int) -> None:
        self._run(["-s", validate_serial(serial), "forward", "--remove", f"tcp:{int(port)}"])

    def screen_awake(self, serial: str) -> bool | None:
        return parse_screen_awake(self.shell(serial, "dumpsys", "power"))

    def lock_screen_showing(self, serial: str) -> bool | None:
        return parse_lock_screen_showing(self.shell(serial,
            "dumpsys activity activities | grep -i keyguardshowing; "
            "dumpsys window | grep -iE 'isKeyguardShowing|ShowingLockscreen|DreamingLockscreen|isStatusBarKeyguard'; true"))

    def wake_screen(self, serial: str) -> None:
        self.shell(serial, "input", "keyevent", "KEYCODE_WAKEUP")

    def dismiss_lock_screen(self, serial: str) -> None:
        """Dismiss a swipe/none lock screen. A secure lock stays up; we never enter PINs."""
        self.shell(serial, "wm", "dismiss-keyguard")
