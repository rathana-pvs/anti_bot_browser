import json
import os
import tempfile
import math
import time
import socket
import ipaddress
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from backend.config import ROOT_DIR
from backend.services.proxy_quality import normalize_proxy

PROXY_POOL_FILE = ROOT_DIR / "proxies" / "proxy_pool.json"
_proxy_assignment_lock = threading.Lock()
GEO_LOOKUP_URL = "https://ipwho.is/"

def load_proxy_pool(*, raise_errors: bool = False) -> list:
    try:
        if not PROXY_POOL_FILE.exists():
            PROXY_POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PROXY_POOL_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
        with open(PROXY_POOL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            needs_save = False
            for p in data:
                normalize_proxy(p)
                # Older imports fabricated Los Angeles metadata whenever the
                # proxy provider supplied no geography. Do not present those
                # defaults as observed facts.
                synthetic_legacy_geo = (
                    p.get("geo_source") in (None, "", "unknown")
                    and p.get("timezone") == "America/Los_Angeles"
                    and p.get("country") == "United States"
                    and p.get("region") == "California"
                    and p.get("city") in {"Los Angeles", "Palo Alto"}
                )
                if synthetic_legacy_geo:
                    for key in ("timezone", "country", "country_code", "region", "city"):
                        p[key] = None
                    p["geo_source"] = "unknown"
                    needs_save = True
                elif not p.get("geo_source"):
                    has_geo = any(p.get(key) for key in ("timezone", "country", "region", "city"))
                    p["geo_source"] = "stored" if has_geo else "unknown"
                    needs_save = True
            if needs_save:
                if raise_errors:
                    save_proxy_pool(data, raise_errors=True)
                else:
                    save_proxy_pool(data)
            return data
    except Exception as err:
        if raise_errors:
            raise
        print(f"Error loading proxy pool: {err}")
        return []

def save_proxy_pool(pool: list, *, raise_errors: bool = False):
    try:
        for proxy in pool:
            normalize_proxy(proxy)
        PROXY_POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{PROXY_POOL_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pool, f, indent=2)
        Path(tmp).replace(PROXY_POOL_FILE)
    except Exception as err:
        if raise_errors:
            raise
        print(f"Error saving proxy pool: {err}")

def import_proxies_from_text(text: str) -> dict:
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        existing_keys = {f"{p.get('host')}:{p.get('port')}" for p in pool}
        lines = text.splitlines()
        added_count = 0
        added_ids = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(":")
            if len(parts) >= 2:
                host = parts[0].strip()
                try:
                    port = int(parts[1].strip())
                except ValueError:
                    continue

                username = parts[2].strip() if len(parts) >= 4 else ""
                password = parts[3].strip() if len(parts) >= 4 else ""

                key = f"{host}:{port}"
                if key not in existing_keys and port > 0:
                    existing_keys.add(key)
                    now_id = int(time.time() * 1000)
                    proxy_id = f"proxy_{now_id}_{str(time.time())[-4:]}"
                    pool.append({
                        "id": proxy_id,
                        "host": host,
                        "port": port,
                        "username": username,
                        "password": password,
                        "type": "socks5",
                        "assigned": False,
                        "profile_id": None,
                        "profile_ids": [],
                        "latency_ms": None,
                        "last_checked": None,
                        "timezone": None,
                        "country": None,
                        "country_code": None,
                        "region": None,
                        "city": None,
                        "geo_source": "unknown",
                    })
                    added_ids.append(proxy_id)
                    added_count += 1

        save_proxy_pool(pool)
        return {"added": added_count, "total": len(pool), "added_ids": added_ids}

def update_proxy_metadata(proxy_ids: list[str], **changes) -> dict:
    """Merge metadata into the latest pool without changing reservations."""
    with _proxy_assignment_lock:
        pool = load_proxy_pool(raise_errors=True)
        wanted = set(proxy_ids)
        updated = []
        for proxy in pool:
            if proxy.get("id") in wanted:
                proxy.update(changes)
                updated.append(proxy["id"])
        if updated:
            save_proxy_pool(pool, raise_errors=True)
        return {"updated_ids": updated, "missing_ids": sorted(wanted - set(updated))}


DEFAULT_ACCOUNT_LIMIT = 1
MAX_ACCOUNT_LIMIT = 20


def _settings_file() -> Path:
    return PROXY_POOL_FILE.with_name("proxy_settings.json")


def account_limit() -> int:
    """How many accounts one proxy may serve. 1 keeps every account on its own proxy."""
    try:
        value = json.loads(_settings_file().read_text(encoding="utf-8")).get("accounts_per_proxy")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_ACCOUNT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ACCOUNT_LIMIT:
        return DEFAULT_ACCOUNT_LIMIT
    return value


def set_account_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ACCOUNT_LIMIT:
        raise ValueError(f"Accounts per proxy must be a whole number from 1 to {MAX_ACCOUNT_LIMIT}.")
    with _proxy_assignment_lock:
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"accounts_per_proxy": value}), encoding="utf-8")
        tmp.replace(path)
    return value


def holders(proxy: dict) -> list[str]:
    """The accounts using this proxy, oldest first."""
    ids = proxy.get("profile_ids")
    if isinstance(ids, list):
        return [pid for pid in ids if pid]
    return [proxy["profile_id"]] if proxy.get("profile_id") else []


def has_room(proxy: dict, profile_id: str, limit: int | None = None) -> bool:
    """True when the account already uses the proxy or the proxy is below the limit."""
    current = holders(proxy)
    return profile_id in current or len(current) < (account_limit() if limit is None else limit)


def _full_message(proxy: dict, limit: int) -> str:
    return (f"Proxy {proxy.get('host')}:{proxy.get('port')} already serves {len(holders(proxy))} "
            f"{'account' if limit == 1 else 'accounts'}, the limit per proxy. Choose another proxy "
            f"or raise the limit on the Proxies page.")


def _set_holders(proxy: dict, ids: list[str]) -> None:
    ids = list(dict.fromkeys(ids))
    proxy["profile_ids"] = ids
    proxy["profile_id"] = ids[0] if ids else None
    proxy["assigned"] = bool(ids)


def _add_holder(pool: list, entry: dict, profile_id: str) -> None:
    """Moves the account to `entry`, leaving every other proxy it held."""
    for proxy in pool:
        if proxy is not entry and profile_id in holders(proxy):
            _set_holders(proxy, [pid for pid in holders(proxy) if pid != profile_id])
    if profile_id not in holders(entry):
        _set_holders(entry, holders(entry) + [profile_id])


def assign_proxy(profile_id: str) -> dict | None:
    """Gives the account a pool proxy with room, preferring the least shared one."""
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        limit = account_limit()
        choices = [p for p in pool if p.get("group") != "Poor proxy" and not p.get("held_by_phone")
                   and has_room(p, profile_id, limit)]
        if not choices:
            return None
        proxy = min(choices, key=lambda p: len(holders(p)))
        _add_holder(pool, proxy, profile_id)
        save_proxy_pool(pool)
        return proxy

def assign_proxy_by_id(profile_id: str, proxy_id: str) -> dict:
    """Atomically reserve one explicit pool proxy for a profile, within the per-proxy limit."""
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        selected = next((p for p in pool if p.get("id") == proxy_id), None)
        if not selected:
            raise ValueError("Selected proxy does not exist")
        limit = account_limit()
        if not has_room(selected, profile_id, limit):
            raise ValueError(_full_message(selected, limit))
        _add_holder(pool, selected, profile_id)
        save_proxy_pool(pool)
        return dict(selected)

def find_proxy(host: str, port: int, username: str) -> dict | None:
    """The pool entry for a phone's proxy, matched on host, port and username: providers often
    serve many proxies from one gateway address that differ only by username."""
    return next((dict(p) for p in load_proxy_pool()
                 if p.get("host") == host and str(p.get("port")) == str(port)
                 and (p.get("username") or "") == (username or "")), None)


def hold_phone_proxy(profile_id: str, host: str, port: int, username: str, password: str | None,
                     exit_ip: str | None = None, timezone_name: str | None = None) -> dict:
    """Reserve a phone account's proxy for it, adding the proxy to the pool when it is missing.

    Without a password (a secondary PC) the entry is a placeholder marked `held_by_phone`: this
    PC cannot use it, but keeps it for this account. With a password it becomes a normal pool
    entry. An entry already serving as many accounts as the per-proxy limit allows is refused.
    """
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        entry = next((p for p in pool if p.get("host") == host and str(p.get("port")) == str(port)), None)
        limit = account_limit()
        if entry and not has_room(entry, profile_id, limit):
            raise ValueError(_full_message(entry, limit))
        if entry is None:
            entry = {"id": f"proxy_{int(time.time() * 1000)}_{str(time.time())[-4:]}", "host": host, "port": int(port),
                     "username": username or "", "password": "", "type": "socks5", "latency_ms": None,
                     "last_checked": None, "timezone": None, "country": None, "country_code": None,
                     "region": None, "city": None, "geo_source": "unknown"}
            pool.append(entry)
        if password:
            entry.update(username=username or "", password=password)
            entry.pop("held_by_phone", None)
        elif not entry.get("password"):
            entry.update(username=username or "", held_by_phone=True)
        if exit_ip and not entry.get("exit_ip"):
            entry["exit_ip"] = exit_ip
        if timezone_name and not entry.get("timezone"):
            entry["timezone"] = timezone_name
        _add_holder(pool, entry, profile_id)
        save_proxy_pool(pool)
        return dict(entry)


def release_proxy(profile_id: str) -> bool:
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        released = False
        for proxy in pool:
            if profile_id in holders(proxy):
                _set_holders(proxy, [pid for pid in holders(proxy) if pid != profile_id])
                released = True
        if released:
            save_proxy_pool(pool)
        return released

def sync_proxy_assignment(profile_id: str, host: str, port: int | str | None) -> dict | None:
    """Records which pool proxy the account's saved network uses. It mirrors the saved config,
    so it never refuses on the per-proxy limit."""
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        modified = False
        matched = None
        if host and port:
            matched = next((p for p in pool if p.get("host") == host and str(p.get("port")) == str(port)), None)
        for proxy in pool:
            if proxy is not matched and profile_id in holders(proxy):
                _set_holders(proxy, [pid for pid in holders(proxy) if pid != profile_id])
                modified = True
        if matched is not None and profile_id not in holders(matched):
            _set_holders(matched, holders(matched) + [profile_id])
            modified = True
        if modified:
            save_proxy_pool(pool)
        return matched

def delete_proxy(proxy_id: str) -> bool:
    result = delete_proxies([proxy_id])
    if result["blocked_ids"]:
        raise ValueError("Assigned proxies cannot be removed. Unassign the proxy first.")
    return bool(result["deleted_ids"])


def delete_proxies(proxy_ids: list[str]) -> dict:
    """Remove a selection in one write while protecting profile assignments."""
    wanted = set(proxy_ids)
    with _proxy_assignment_lock:
        pool = load_proxy_pool(raise_errors=True)
        deleted = []
        blocked = []
        kept = []
        found = set()
        for proxy in pool:
            proxy_id = proxy.get("id")
            if proxy_id not in wanted:
                kept.append(proxy)
                continue
            found.add(proxy_id)
            if holders(proxy):
                blocked.append(proxy_id)
                kept.append(proxy)
            else:
                deleted.append(proxy_id)
        if deleted:
            save_proxy_pool(kept, raise_errors=True)
        return {"deleted_ids": deleted, "blocked_ids": blocked, "missing_ids": sorted(wanted - found)}

def _curl_environment() -> dict[str, str]:
    """Keep PyInstaller's bundled OpenSSL out of the system curl process."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            env["LD_LIBRARY_PATH"] = original
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def _curl_config_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Proxy credentials contain unsupported line breaks")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _proxy_test_config(network: dict) -> list[str]:
    host = str(network.get("proxy_host") or "").strip()
    port = int(network.get("proxy_port") or 0)
    if not host or not 1 <= port <= 65535 or network.get("mode") == "direct":
        raise ValueError("A configured proxy is required")
    schemes = {"socks5": "socks5h", "socks5h": "socks5h", "socks4": "socks4a", "socks4a": "socks4a", "http": "http", "https": "https"}
    scheme = schemes.get(str(network.get("proxy_type") or "socks5").lower())
    if not scheme:
        raise ValueError("Unsupported proxy type")
    endpoint = f"[{host}]" if ":" in host and not host.startswith("[") else host
    lines = [f"proxy = {_curl_config_value(f'{scheme}://{endpoint}:{port}')}", 'noproxy = ""',
             "silent", "connect-timeout = 6", "max-time = 20", f"output = {_curl_config_value(os.devnull)}"]
    username = str(network.get("proxy_user") or "")
    password = str(network.get("proxy_pass") or "")
    if username or password:
        lines.append(f"proxy-user = {_curl_config_value(f'{username}:{password}')}")
    return lines


def check_facebook_response(network: dict) -> dict:
    """Measure time to Facebook's first response byte through the saved proxy."""
    lines = _proxy_test_config(network) + ['url = "https://www.facebook.com/"', "head",
                                         "max-time = 15", 'write-out = "%{http_code} %{time_starttransfer}"']
    try:
        result = subprocess.run(["curl", "--disable", "--config", "-"], input="\n".join(lines) + "\n",
                                capture_output=True, text=True, timeout=18, check=False, env=_curl_environment())
    except subprocess.TimeoutExpired:
        return {"success": False, "response_ms": None, "http_status": None, "error": "Facebook connection timed out"}
    except OSError:
        return {"success": False, "response_ms": None, "http_status": None, "error": "Facebook connection check could not start"}
    if result.returncode:
        error = "Facebook connection timed out" if result.returncode == 28 else "Could not reach Facebook through this proxy"
        return {"success": False, "response_ms": None, "http_status": None, "error": error}
    try:
        status_text, seconds = result.stdout.strip().split()
        status = int(status_text)
        milliseconds = round(float(seconds) * 1000)
        if status < 100 or status > 599 or milliseconds < 0:
            raise ValueError("Invalid response")
    except (ValueError, OverflowError):
        return {"success": False, "response_ms": None, "http_status": None, "error": "Invalid connection test result"}
    success = 200 <= status < 400
    return {"success": success, "response_ms": milliseconds, "http_status": status,
            "error": None if success else f"Facebook check returned HTTP {status}"}


def check_proxy_speed(network: dict, direction: str, *, sample_bytes: int | None = None) -> dict:
    """Bounded, single-transfer throughput estimate through the configured proxy."""
    if direction not in {"download", "upload"}:
        raise ValueError("Invalid speed test direction")
    lines = _proxy_test_config(network)
    size = 5_000_000 if direction == "download" else 2_000_000
    if sample_bytes is not None:
        if not isinstance(sample_bytes, int) or not 250_000 <= sample_bytes <= 5_000_000:
            raise ValueError("Speed sample must be between 250 KB and 5 MB")
        size = sample_bytes
    metric = "size_download" if direction == "download" else "size_upload"
    lines += [f'write-out = "%{{http_code}} %{{{metric}}} %{{time_total}}"',
              'header = "Cache-Control: no-cache"']
    with tempfile.TemporaryDirectory(prefix="proxy-speed-") as folder:
        if direction == "download":
            lines += [f'url = "https://speed.cloudflare.com/__down?bytes={size}"']
        else:
            payload = Path(folder) / "payload.bin"
            payload.write_bytes(os.urandom(size))
            lines += ['url = "https://speed.cloudflare.com/__up"',
                      f"data-binary = {_curl_config_value('@' + str(payload))}",
                      'header = "Content-Type: application/octet-stream"']
        failure = {"success": False, "mbps": None, "bytes": 0, "provider": "Cloudflare"}
        try:
            result = subprocess.run(["curl", "--disable", "--config", "-"], input="\n".join(lines) + "\n",
                                    capture_output=True, text=True, timeout=23, check=False, env=_curl_environment())
        except subprocess.TimeoutExpired:
            return {**failure, "error": f"{direction.capitalize()} test timed out"}
        except OSError:
            return {**failure, "error": "Speed test could not start"}
        if result.returncode:
            error = "Speed test timed out" if result.returncode == 28 else "Speed test failed through this proxy"
            return {**failure, "error": error}
        try:
            status, transferred, seconds = result.stdout.strip().split()
            status, transferred, seconds = int(status), int(transferred), float(seconds)
            if not 200 <= status < 300:
                return {**failure, "error": f"Cloudflare returned HTTP {status}"}
            if transferred != size or seconds <= 0:
                raise ValueError("Incomplete transfer")
            mbps = round(transferred * 8 / seconds / 1_000_000, 2)
            if not math.isfinite(mbps):
                raise ValueError("Invalid timing")
            return {"success": True, "mbps": mbps, "bytes": transferred, "provider": "Cloudflare", "error": None}
        except (ValueError, OverflowError):
            return {**failure, "error": "Speed test returned an incomplete measurement"}


def lookup_proxy_geography(proxy: dict) -> dict:
    """Resolve geography through the proxy without placing credentials in argv."""
    host = str(proxy.get("host") or "").strip()
    port = int(proxy.get("port") or 0)
    if not host or not port:
        raise ValueError("Proxy endpoint is incomplete")
    if host.casefold() == "localhost":
        raise ValueError("Local proxy endpoints cannot be geolocated")
    try:
        endpoint_ip = ipaddress.ip_address(host)
    except ValueError:
        endpoint_ip = None
    if endpoint_ip is not None and not endpoint_ip.is_global:
        raise ValueError("Private or reserved proxy endpoints cannot be geolocated")

    proxy_url = f"socks5h://{host}:{port}"
    username = str(proxy.get("username") or "")
    password = str(proxy.get("password") or "")
    config_lines = [
        f"url = {_curl_config_value(GEO_LOOKUP_URL)}",
        f"proxy = {_curl_config_value(proxy_url)}",
        "connect-timeout = 6",
        "max-time = 15",
        "max-filesize = 65536",
        "silent",
        "show-error",
        "fail-with-body",
    ]
    if username or password:
        config_lines.append(f"proxy-user = {_curl_config_value(f'{username}:{password}')}")
    result = subprocess.run(
        ["curl", "--config", "-"],
        input="\n".join(config_lines) + "\n",
        capture_output=True,
        text=True,
        timeout=18,
        check=False,
        env=_curl_environment(),
    )
    if result.returncode != 0:
        raise RuntimeError("Proxy geolocation request failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Geolocation service returned invalid data") from exc
    if data.get("success") is not True or not data.get("ip"):
        raise RuntimeError("Geolocation service could not identify the proxy exit")
    timezone_data = data.get("timezone") or {}
    timezone_name = timezone_data.get("id") if isinstance(timezone_data, dict) else timezone_data
    if not timezone_name:
        raise RuntimeError("Geolocation result did not include a timezone")
    return {
        "exit_ip": data.get("ip"),
        "country": data.get("country"),
        "country_code": data.get("country_code"),
        "region": data.get("region"),
        "city": data.get("city"),
        "timezone": timezone_name,
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "geo_source": "proxy_exit_lookup",
        "geo_checked_at": datetime.now(timezone.utc).isoformat(),
        "geo_last_error": None,
    }


def refresh_proxy_geographies(proxy_ids: list[str]) -> dict:
    wanted = set(proxy_ids)
    pool = load_proxy_pool()
    targets = [dict(proxy) for proxy in pool if proxy.get("id") in wanted]
    results: dict[str, dict] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(targets)))) as executor:
        futures = {executor.submit(lookup_proxy_geography, proxy): proxy["id"] for proxy in targets}
        for future in as_completed(futures):
            proxy_id = futures[future]
            try:
                results[proxy_id] = future.result()
            except Exception as exc:
                failures[proxy_id] = str(exc)

    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        for proxy in pool:
            proxy_id = proxy.get("id")
            if proxy_id in results:
                proxy.update(results[proxy_id])
            elif proxy_id in failures:
                proxy["geo_last_error"] = failures[proxy_id]
                proxy["geo_checked_at"] = datetime.now(timezone.utc).isoformat()
        save_proxy_pool(pool)
    return {"checked": len(results), "failed": len(failures), "failures": failures}


def refresh_proxy_geography(proxy_id: str) -> dict:
    outcome = refresh_proxy_geographies([proxy_id])
    pool = load_proxy_pool()
    proxy = next((item for item in pool if item.get("id") == proxy_id), None)
    if not proxy:
        raise ValueError("Proxy not found")
    if not outcome["checked"]:
        raise RuntimeError(outcome["failures"].get(proxy_id, "Proxy geolocation failed"))
    return proxy


def refresh_proxy_statuses(proxy_ids: list[str]) -> dict:
    """Refresh reachability, latency, exit geography, and timestamps together."""
    wanted = set(proxy_ids)
    targets = [dict(proxy) for proxy in load_proxy_pool() if proxy.get("id") in wanted]

    def check(proxy: dict) -> dict:
        ping = test_proxy_ping(proxy["host"], int(proxy["port"]))
        geo = None
        geo_error = None
        if ping.get("success"):
            try:
                geo = lookup_proxy_geography(proxy)
            except Exception as exc:
                geo_error = str(exc)
        else:
            geo_error = "Location lookup skipped because the proxy is unreachable"
        return {"ping": ping, "geo": geo, "geo_error": geo_error}

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(targets)))) as executor:
        futures = {executor.submit(check, proxy): proxy["id"] for proxy in targets}
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    now = datetime.now(timezone.utc).isoformat()
    with _proxy_assignment_lock:
        pool = load_proxy_pool()
        for proxy in pool:
            result = results.get(proxy.get("id"))
            if not result:
                continue
            ping = result["ping"]
            proxy["reachable"] = bool(ping.get("success"))
            proxy["latency_ms"] = ping.get("latency_ms")
            proxy["last_checked"] = now
            if result["geo"]:
                proxy.update(result["geo"])
            else:
                proxy["geo_last_error"] = result["geo_error"]
                proxy["geo_checked_at"] = now
        save_proxy_pool(pool)

    online = sum(1 for result in results.values() if result["ping"].get("success"))
    geolocated = sum(1 for result in results.values() if result["geo"])
    return {
        "total": len(results),
        "online": online,
        "offline": len(results) - online,
        "geo_checked": geolocated,
        "geo_failed": len(results) - geolocated,
    }

def test_proxy_ping(host: str, port: int, timeout_sec: float = 4.0) -> dict:
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_sec)
    try:
        s.connect((host, int(port)))
        latency = int((time.time() - start) * 1000)
        return {"success": True, "latency_ms": latency}
    except Exception as err:
        return {"success": False, "error": str(err), "latency_ms": None}
    finally:
        s.close()
