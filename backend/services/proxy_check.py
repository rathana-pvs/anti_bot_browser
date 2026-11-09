"""Proxy checks: a quick check, then short timed speed samples, run several proxies at once.

1. Quick check (about 2 s): connect to the proxy, then Facebook's response and the exit IP through
   it. A proxy that fails here is dead and skips the speed samples.
2. Speed: two timed samples per direction. A download stops after SAMPLE_SECONDS or its byte
   cap, whichever comes first, so a slow proxy still gives a number. An upload counts only once
   Cloudflare confirms it received the whole payload; one still sending at the limit timed out.
3. A run checks as many proxies at once as the account-browser limit, each proxy once.

Results are saved on the proxy as `check` (raw samples) and rated by proxy_quality.rate_check.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.services import proxy_service
from backend.services.proxy_quality import GRADES, rate_check

# The time limit also covers the proxy's connect and TLS set-up (1-2 s through a distant proxy).
SAMPLE_SECONDS = {"download": 5, "upload": 8}
SAMPLES = 2
DOWNLOAD_CAP = 2_000_000
# Small enough that a proxy uploading at about 0.6 Mbps or more finishes it within the limit.
# Only a finished upload counts: what curl reports as sent includes bytes still sitting in this
# PC's and the proxy's buffers, so a proxy that delivers nothing could look like 0.3 Mbps.
UPLOAD_CAP = 500_000
# Less than this got through: too little to measure a speed from.
MIN_SAMPLE_BYTES = 32_000
QUICK_TIMEOUT = 8
FRESH_FOR = timedelta(hours=24)
MAX_AT_ONCE = 10
HISTORY = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def network_of(proxy: dict) -> dict:
    return {"mode": "pool", "proxy_host": proxy["host"], "proxy_port": proxy["port"],
            "proxy_type": proxy.get("type") or "socks5",
            "proxy_user": proxy.get("username") or "", "proxy_pass": proxy.get("password") or ""}


def timed_sample(network: dict | None, direction: str, seconds: float | None = None, cap: int | None = None) -> dict:
    """One speed sample. `network` None measures this PC's own line, for the run's baseline."""
    if direction not in {"download", "upload"}:
        raise ValueError("Invalid speed test direction")
    seconds = seconds or SAMPLE_SECONDS[direction]
    cap = cap or (DOWNLOAD_CAP if direction == "download" else UPLOAD_CAP)
    quote = proxy_service._curl_config_value
    lines = (proxy_service._proxy_test_config(network, seconds) if network else
             ["silent", "connect-timeout = 6", f"max-time = {seconds}", f"output = {quote(os.devnull)}"])
    lines += ['write-out = "%{http_code} %{size_download} %{size_upload} %{time_pretransfer} %{time_starttransfer} %{time_total}"',
              'header = "Cache-Control: no-cache"', 'header = "Expect:"']
    with tempfile.TemporaryDirectory(prefix="proxy-check-") as folder:
        if direction == "download":
            lines.append(f'url = "https://speed.cloudflare.com/__down?bytes={cap}"')
        else:
            payload = Path(folder) / "payload.bin"
            payload.write_bytes(os.urandom(cap))
            lines += ['url = "https://speed.cloudflare.com/__up"', f"data-binary = {quote('@' + str(payload))}",
                      'header = "Content-Type: application/octet-stream"']
        try:
            result = subprocess.run(["curl", "--disable", "--config", "-"], input="\n".join(lines) + "\n",
                                    capture_output=True, text=True, timeout=seconds + 10, check=False,
                                    env=proxy_service._curl_environment())
        except (subprocess.TimeoutExpired, OSError):
            return {"mbps": None, "bytes": 0, "error": "Speed sample could not run"}
    return parse_sample(direction, result.returncode, result.stdout)


def parse_sample(direction: str, returncode: int, stdout: str) -> dict:
    """Speed from curl's write-out. A download cut off by the time limit (exit 28) still counts;
    an upload must have finished, with Cloudflare's 200 confirming the payload arrived."""
    failure = {"mbps": None, "bytes": 0}
    if returncode not in (0, 28):
        return {**failure, "error": "No traffic got through this proxy"}
    try:
        status, down, up, pretransfer, starttransfer, total = stdout.split()
        status, moved = int(status), int(down if direction == "download" else up)
        pretransfer, starttransfer, total = float(pretransfer), float(starttransfer), float(total)
    except ValueError:
        return {**failure, "error": "Speed sample returned no measurement"}
    timed_out = returncode == 28
    if timed_out and direction == "upload":
        return {**failure, "error": f"Upload did not finish within {SAMPLE_SECONDS['upload']} s"}
    if timed_out and moved == 0:
        return {**failure, "error": "Timed out before any data got through"}
    if status != 200:
        return {**failure, "error": f"Cloudflare returned HTTP {status}"}
    # Time from the first byte (download) or the start of sending until Cloudflare's answer
    # (upload): the proxy's connect and TLS set-up would otherwise count against short samples.
    start = starttransfer if direction == "download" and starttransfer > 0 else pretransfer
    elapsed = total - start
    if moved < MIN_SAMPLE_BYTES or elapsed <= 0.05:
        return {**failure, "bytes": moved, "error": "Too little got through to measure"}
    return {"mbps": round(moved * 8 / elapsed / 1_000_000, 2), "bytes": moved, "error": None}


def quick_check(proxy: dict) -> dict:
    """Connect, Facebook and exit IP. About 2 s for a working proxy."""
    ping = proxy_service.test_proxy_ping(proxy["host"], int(proxy["port"]), timeout_sec=4)
    check = {"checked_at": _now(), "reachable": bool(ping.get("success")), "connect_ms": ping.get("latency_ms"),
             "facebook_ok": False, "facebook_ms": None, "facebook_status": None, "exit_ip": None, "geo": None,
             "error": None if ping.get("success") else "Could not connect to the proxy"}
    if not check["reachable"]:
        return check
    with ThreadPoolExecutor(max_workers=2) as pool:
        facebook = pool.submit(proxy_service.check_facebook_response, network_of(proxy), QUICK_TIMEOUT)
        geo = pool.submit(proxy_service.lookup_proxy_geography, proxy)
        facebook = facebook.result()
        try:
            check["geo"] = geo.result()
            check["exit_ip"] = check["geo"]["exit_ip"]
        except Exception as exc:
            check["geo_error"] = str(exc)
    check.update(facebook_ok=bool(facebook.get("success")), facebook_ms=facebook.get("response_ms"),
                 facebook_status=facebook.get("http_status"))
    if not check["facebook_ok"] and not check["exit_ip"]:
        # The port answers but nothing gets through, usually a rejected username or password.
        check.update(reachable=False, error="No traffic got through. Check the proxy's username and password, or whether it expired.")
    return check


def measure(proxy: dict) -> dict:
    """The full check for one proxy: quick check, then timed samples if it works."""
    started = time.monotonic()
    check = quick_check(proxy)
    check["download"], check["upload"] = [], []
    if check["reachable"]:
        network = network_of(proxy)
        for direction in ("download", "upload"):
            for _ in range(SAMPLES):
                check[direction].append(timed_sample(network, direction))
            # Cloudflare's speed server stalls now and then while the proxy carries other sites
            # fine, so one more try before a direction counts as timed out.
            if not any(sample.get("mbps") is not None for sample in check[direction]):
                check[direction].append(timed_sample(network, direction))
    check["seconds"] = round(time.monotonic() - started, 1)
    return check


def _legacy_speed_test(check: dict) -> dict:
    """The older `speed_test` shape, still read by account and phone screens."""
    def direction(samples):
        good = [s for s in samples if s.get("mbps") is not None]
        if not good:
            return {"success": False, "mbps": None, "bytes": 0, "provider": "Cloudflare",
                    "error": (samples[0].get("error") if samples else None) or check.get("error") or "Not measured"}
        return {"success": True, "mbps": round(sum(s["mbps"] for s in good) / len(good), 2),
                "bytes": sum(s["bytes"] for s in good), "provider": "Cloudflare", "error": None}
    return {"latency": {"success": check["reachable"], "latency_ms": check.get("connect_ms")},
            "download": direction(check["download"]), "upload": direction(check["upload"]),
            "last_checked": check["checked_at"]}


def save_check(proxy_id: str, check: dict) -> dict | None:
    """Stores the check with a short history, and refreshes reachability and location."""
    with proxy_service._proxy_assignment_lock:
        pool = proxy_service.load_proxy_pool(raise_errors=True)
        proxy = next((p for p in pool if p.get("id") == proxy_id), None)
        if proxy is None:
            return None
        previous = proxy.get("check") if isinstance(proxy.get("check"), dict) else None
        rating = rate_check(check)
        known_ip = (previous or {}).get("exit_ip") or proxy.get("exit_ip")
        check["ip_changed"] = bool(check.get("exit_ip") and known_ip and check["exit_ip"] != known_ip)
        history = [*((previous or {}).get("history") or []),
                   {"at": check["checked_at"], "grade": rating["grade"], "down": rating["download_mbps"],
                    "up": rating["upload_mbps"], "facebook_ms": check.get("facebook_ms"), "exit_ip": check.get("exit_ip")}]
        check["history"] = history[-HISTORY:]
        geo = check.pop("geo", None)
        proxy["check"] = check
        proxy["speed_test"] = _legacy_speed_test(check)
        proxy.update(reachable=check["reachable"], latency_ms=check.get("connect_ms"), last_checked=check["checked_at"])
        if geo:
            proxy.update(geo)
        proxy_service.save_proxy_pool(pool, raise_errors=True)
        return dict(proxy)


def check_one(proxy_id: str) -> dict:
    proxy = next((p for p in proxy_service.load_proxy_pool() if p.get("id") == proxy_id), None)
    if not proxy:
        raise LookupError("Proxy not found")
    saved = save_check(proxy_id, measure(proxy))
    if saved is None:
        raise LookupError("Proxy removed during the check")
    return saved


def is_fresh(proxy: dict, now: datetime | None = None) -> bool:
    """A working result from the last 24 hours. A timed-out or unreachable proxy is never fresh,
    so every run checks it again and it can move back to Available."""
    check = proxy.get("check")
    if not isinstance(check, dict) or rate_check(check)["grade"] not in ("good", "slow"):
        return False
    try:
        at = datetime.fromisoformat(check["checked_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) - at < FRESH_FOR


def default_at_once() -> int:
    try:
        from backend.services.docker_service import get_scheduler_config
        return int(get_scheduler_config()["max_active_profile_containers"])
    except Exception:
        return 3


class CheckRun:
    """One run at a time; the Proxies page polls `status()`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._stop = threading.Event()

    def status(self) -> dict:
        with self._lock:
            if not self._state:
                return {"state": "idle"}
            return {**self._state, "running_ids": list(self._state["running_ids"]),
                    "grades": dict(self._state["grades"])}

    def start(self, proxy_ids: list[str] | None = None, force: bool = False, at_once: int | None = None,
              measure_fn=None, baseline_fn=None) -> dict:
        pool = proxy_service.load_proxy_pool(raise_errors=True)
        wanted = set(proxy_ids) if proxy_ids else None
        # A phone's placeholder has no password on this PC, so it cannot be checked from here.
        targets = [p for p in pool if (wanted is None or p.get("id") in wanted)
                   and not (p.get("held_by_phone") and not p.get("password"))]
        skipped = [] if force else [p["id"] for p in targets if is_fresh(p)]
        queue = deque(p["id"] for p in targets if p["id"] not in skipped)
        limit = max(1, min(at_once or default_at_once(), MAX_AT_ONCE, len(queue) or 1))
        with self._lock:
            if self._state and self._state["state"] == "running":
                raise RuntimeError("A proxy check is already running")
            self._stop.clear()
            self._state = {"run_id": uuid.uuid4().hex, "state": "running", "started_at": _now(), "finished_at": None,
                           "total": len(queue), "done": 0, "at_once": limit, "running_ids": [], "skipped": len(skipped),
                           "grades": {g: 0 for g in GRADES},
                           "baseline": None, "error": None}
        status = self.status()
        threading.Thread(target=self._run, args=(queue, limit, measure_fn or measure, baseline_fn or measure_baseline),
                         daemon=True, name="proxy-check").start()
        return status

    def stop(self) -> dict:
        self._stop.set()
        return self.status()

    def _count(self, grade: str) -> None:
        with self._lock:
            self._state["done"] += 1
            self._state["grades"][grade] += 1

    def _record(self, proxy_id: str, check: dict) -> None:
        saved = save_check(proxy_id, check)
        self._count(((saved or {}).get("rating") or rate_check(check))["grade"])

    def _work(self, queue: deque, measure_fn):
        while not self._stop.is_set():
            with self._lock:
                if not queue:
                    return
                proxy_id = queue.popleft()
                self._state["running_ids"].append(proxy_id)
            try:
                proxy = next((p for p in proxy_service.load_proxy_pool() if p.get("id") == proxy_id), None)
                if proxy is None:
                    with self._lock:
                        self._state["total"] -= 1
                    continue
                self._record(proxy_id, measure_fn(proxy))
            except Exception as exc:  # A failing proxy never stops the run.
                with self._lock:
                    self._state["error"] = str(exc)
                    self._state["done"] += 1
            finally:
                with self._lock:
                    if proxy_id in self._state["running_ids"]:
                        self._state["running_ids"].remove(proxy_id)

    def _run(self, queue: deque, limit: int, measure_fn, baseline_fn):
        try:
            try:
                baseline = baseline_fn()
            except Exception:
                baseline = None
            with self._lock:
                self._state["baseline"] = baseline
            workers = [threading.Thread(target=self._work, args=(queue, measure_fn), daemon=True) for _ in range(limit)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        finally:
            with self._lock:
                self._state.update(state="stopped" if self._stop.is_set() else "done", finished_at=_now(),
                                   running_ids=[])


def measure_baseline() -> dict:
    """This PC's own line without a proxy: how much all checks, or all accounts, share."""
    # Bigger caps: a home line is much faster than a proxy, and a 2 MB sample would end too soon to time.
    down = timed_sample(None, "download", cap=20_000_000)
    up = timed_sample(None, "upload", cap=6_000_000)
    return {"download_mbps": down["mbps"], "upload_mbps": up["mbps"], "checked_at": _now()}


runner = CheckRun()
