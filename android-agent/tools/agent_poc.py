"""Milestone 0 driver for the companion app: onboard one Android user and test for leaks.

Each subcommand is one step, so nothing changes on the phone implicitly:

  install    install the APK for the user, grant VPN permission, exempt from battery limits
  always-on  turn Always-on VPN with lockdown on or off for the user
  start      start the VPN service in the user (the user must be on screen)
  set-proxy  send a proxy from the app's pool (read-only) to the companion app
  status     companion app status
  check      DNS, exit IP and IPv6 measured inside the user, through the tunnel
  leak-test  point the tunnel at an unreachable proxy, confirm nothing gets out, restore
  remove     turn Always-on off and uninstall the app for the user

Proxy passwords are never printed. Run from the repository root:
  automation/venv/bin/python android-agent/tools/agent_poc.py --serial SERIAL --user 11 status
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PACKAGE = "com.isolatedbrowser.agent"
AUTHORITY = f"{PACKAGE}.control"
DEFAULT_APK = Path(__file__).resolve().parents[1] / "app/build/outputs/apk/debug/app-debug.apk"
# TEST-NET-1 (RFC 5737): never routed, so a tunnel pointed here cannot reach anything.
UNREACHABLE_PROXY = {"host": "192.0.2.1", "port": 1080, "username": "", "password": ""}
_RESULT_RE = re.compile(r"json=(\{.*\})\}\]\s*$", re.S)


class Phone:
    def __init__(self, serial: str, user: int):
        self.serial, self.user = serial, user

    def adb(self, *args: str, timeout: float = 60) -> str:
        result = subprocess.run(["adb", "-s", self.serial, *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise SystemExit(f"adb {' '.join(args[:3])} failed: {(result.stderr or result.stdout).strip()}")
        return result.stdout

    def shell(self, *args: str, timeout: float = 60) -> str:
        return self.adb("shell", " ".join(shlex.quote(a) for a in args), timeout=timeout)

    def call(self, method: str, arg: str | None = None, timeout: float = 60) -> dict:
        args = ["content", "call", "--user", str(self.user), "--uri", f"content://{AUTHORITY}", "--method", method]
        if arg is not None:
            args += ["--arg", arg]
        output = self.shell(*args, timeout=timeout)
        match = _RESULT_RE.search(output.strip())
        if not match:
            raise SystemExit(f"Unexpected reply from the companion app: {output.strip()[:300]}")
        return json.loads(match.group(1))


def proxy_from_pool(proxy_id: str | None) -> dict:
    root = Path(os.environ.get("AUTOMAT_FB_ROOT") or Path(__file__).resolve().parents[2])
    pool = json.loads((root / "proxies" / "proxy_pool.json").read_text(encoding="utf-8"))
    if proxy_id:
        proxy = next((p for p in pool if p.get("id") == proxy_id), None)
    else:
        proxy = next((p for p in pool if p.get("group") == "Available" and not (p.get("assigned") or p.get("profile_id"))), None)
    if not proxy:
        raise SystemExit("No matching proxy in the pool." if proxy_id else "No unassigned proxy in the Available folder.")
    if (proxy.get("type") or "socks5") != "socks5":
        raise SystemExit("Only SOCKS5 proxies are supported.")
    return {"id": proxy.get("id"), "host": str(proxy["host"]), "port": int(proxy["port"]),
            "username": proxy.get("username") or "", "password": proxy.get("password") or ""}


def encode(config: dict) -> str:
    fields = {key: config[key] for key in ("host", "port", "username", "password")}
    return base64.b64encode(json.dumps(fields).encode()).decode()


def host_public_ip() -> str | None:
    """The phone's own network normally shares this computer's public IP; used to spot a direct exit."""
    try:
        with urllib.request.urlopen("https://ipwho.is/", timeout=10) as response:
            return json.loads(response.read()).get("ip")
    except Exception:
        return None


def cmd_install(phone: Phone, args) -> dict:
    apk = Path(args.apk)
    if not apk.is_file():
        raise SystemExit(f"APK not found: {apk}. Build it with ./gradlew assembleDebug in android-agent/.")
    phone.adb("install", "--user", str(phone.user), "-r", str(apk), timeout=180)
    phone.shell("appops", "set", "--user", str(phone.user), PACKAGE, "ACTIVATE_VPN", "allow")
    phone.shell("dumpsys", "deviceidle", "whitelist", f"+{PACKAGE}")
    return {"installed": True, "vpn_permission": phone.shell("appops", "get", "--user", str(phone.user), PACKAGE, "ACTIVATE_VPN").strip()}


def cmd_always_on(phone: Phone, args) -> dict:
    user = str(phone.user)
    if args.state == "on":
        phone.shell("settings", "put", "--user", user, "secure", "always_on_vpn_app", PACKAGE)
        phone.shell("settings", "put", "--user", user, "secure", "always_on_vpn_lockdown", "1")
    else:
        phone.shell("settings", "delete", "--user", user, "secure", "always_on_vpn_app")
        phone.shell("settings", "delete", "--user", user, "secure", "always_on_vpn_lockdown")
    return {
        "always_on_vpn_app": phone.shell("settings", "get", "--user", user, "secure", "always_on_vpn_app").strip(),
        "always_on_vpn_lockdown": phone.shell("settings", "get", "--user", user, "secure", "always_on_vpn_lockdown").strip(),
        "note": "Android may apply this only after the user or phone restarts; status reports what is in effect.",
    }


def cmd_start(phone: Phone, args) -> dict:
    current = phone.shell("am", "get-current-user").strip()
    if current != str(phone.user):
        raise SystemExit(f"User {phone.user} is not on screen (current user is {current}). Switch to it first.")
    phone.shell("am", "start", "--user", str(phone.user), "-n", f"{PACKAGE}/.ControlActivity")
    time.sleep(3)
    return phone.call("status")


def cmd_set_proxy(phone: Phone, args) -> dict:
    proxy = proxy_from_pool(args.proxy_id)
    reply = phone.call("set_config", encode(proxy))
    return {"proxy_id": proxy["id"], "proxy": f"{proxy['host']}:{proxy['port']}", **reply}


def cmd_check(phone: Phone, args) -> dict:
    result = phone.call("check", timeout=90)
    direct = host_public_ip()
    exit_ip = (result.get("exit") or {}).get("ip")
    result["computer_public_ip"] = direct
    result["exit_differs_from_computer"] = None if not (direct and exit_ip) else direct != exit_ip
    return result


def cmd_leak_test(phone: Phone, args) -> dict:
    proxy = proxy_from_pool(args.proxy_id)
    report: dict = {}
    phone.call("set_config", encode(UNREACHABLE_PROXY))
    time.sleep(2)
    broken = phone.call("check", timeout=90)
    reached = {key: (broken.get(key) or {}).get("ip") for key in ("exit", "trace_ipv4", "trace_ipv6")}
    report["unreachable_proxy"] = {
        **{key: broken.get(key) for key in ("dns", "exit", "trace_ipv4", "trace_ipv6")},
        # With the proxy unreachable, any reply at all means traffic left outside the tunnel.
        "leaked": any(reached.values()),
    }
    phone.call("set_config", encode(proxy))
    restored = {}
    # Android re-validates Private DNS after the change, which took about 30 s on a Pixel.
    for _ in range(12):
        time.sleep(5)
        restored = cmd_check(phone, args)
        if (restored.get("exit") or {}).get("ip"):
            break
    report["restored"] = restored
    exits = {(restored.get(key) or {}).get("ip") for key in ("exit", "trace_ipv4")}
    report["passed"] = (not report["unreachable_proxy"]["leaked"] and None not in exits
                        and restored.get("computer_public_ip") not in exits)
    return report


def cmd_status(phone: Phone, args) -> dict:
    return phone.call("status")


def cmd_remove(phone: Phone, args) -> dict:
    cmd_always_on(phone, argparse.Namespace(state="off"))
    phone.adb("uninstall", "--user", str(phone.user), PACKAGE)
    return {"removed": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--user", type=int, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--apk", default=str(DEFAULT_APK))
    always = sub.add_parser("always-on")
    always.add_argument("state", choices=["on", "off"])
    sub.add_parser("start")
    for name in ("set-proxy", "leak-test", "check"):
        command = sub.add_parser(name)
        command.add_argument("--proxy-id", help="pool proxy id; default is the first unassigned Available proxy")
    sub.add_parser("status")
    sub.add_parser("remove")
    args = parser.parse_args(argv)
    if args.user == 0:
        raise SystemExit("User 0 is the control user; the companion app does not run there.")
    handlers = {"install": cmd_install, "always-on": cmd_always_on, "start": cmd_start, "set-proxy": cmd_set_proxy,
                "status": cmd_status, "check": cmd_check, "leak-test": cmd_leak_test, "remove": cmd_remove}
    print(json.dumps(handlers[args.command](Phone(args.serial, args.user), args), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
