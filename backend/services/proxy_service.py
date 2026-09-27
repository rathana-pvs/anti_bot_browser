import json
import time
import socket
import httpx
from datetime import datetime, timezone
from pathlib import Path
from backend.config import ROOT_DIR

PROXY_POOL_FILE = ROOT_DIR / "proxies" / "proxy_pool.json"

def load_proxy_pool() -> list:
    try:
        if not PROXY_POOL_FILE.exists():
            PROXY_POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PROXY_POOL_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
            return []
        with open(PROXY_POOL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as err:
        print(f"Error loading proxy pool: {err}")
        return []

def save_proxy_pool(pool: list):
    try:
        PROXY_POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{PROXY_POOL_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pool, f, indent=2)
        Path(tmp).replace(PROXY_POOL_FILE)
    except Exception as err:
        print(f"Error saving proxy pool: {err}")

def import_proxies_from_text(text: str) -> dict:
    pool = load_proxy_pool()
    existing_keys = {f"{p.get('host')}:{p.get('port')}" for p in pool}
    lines = text.splitlines()
    added_count = 0

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
                pool.append({
                    "id": f"proxy_{now_id}_{str(time.time())[-4:]}",
                    "host": host,
                    "port": port,
                    "username": username,
                    "password": password,
                    "type": "socks5",
                    "assigned": False,
                    "profile_id": None,
                    "latency_ms": None,
                    "last_checked": None,
                })
                added_count += 1

    save_proxy_pool(pool)
    return {"added": added_count, "total": len(pool)}

def assign_proxy(profile_id: str) -> dict | None:
    pool = load_proxy_pool()
    for proxy in pool:
        if not proxy.get("assigned"):
            proxy["assigned"] = True
            proxy["profile_id"] = profile_id
            save_proxy_pool(pool)
            return proxy
    return None

def release_proxy(profile_id: str) -> bool:
    pool = load_proxy_pool()
    released = False
    for proxy in pool:
        if proxy.get("profile_id") == profile_id:
            proxy["assigned"] = False
            proxy["profile_id"] = None
            released = True
    if released:
        save_proxy_pool(pool)
    return released

def sync_proxy_assignment(profile_id: str, host: str, port: int | str | None) -> dict | None:
    pool = load_proxy_pool()
    modified = False

    # Release any existing proxy that was assigned to this profile but no longer matches
    for proxy in pool:
        if proxy.get("profile_id") == profile_id:
            if not host or proxy.get("host") != host or str(proxy.get("port")) != str(port):
                proxy["assigned"] = False
                proxy["profile_id"] = None
                modified = True

    matched = None
    if host and port:
        for proxy in pool:
            if proxy.get("host") == host and str(proxy.get("port")) == str(port):
                if not proxy.get("assigned") or proxy.get("profile_id") != profile_id:
                    proxy["assigned"] = True
                    proxy["profile_id"] = profile_id
                    modified = True
                matched = proxy
                break

    if modified:
        save_proxy_pool(pool)
    return matched

def delete_proxy(proxy_id: str) -> bool:
    pool = load_proxy_pool()
    filtered = [p for p in pool if p.get("id") != proxy_id]
    save_proxy_pool(filtered)
    return True

async def fetch_proxy_geo(ip: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            res = await client.get(
                f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,regionName,city,timezone"
            )
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    return {
                        "country": data.get("country") or "United States",
                        "country_code": data.get("countryCode") or "US",
                        "region": data.get("regionName") or "California",
                        "city": data.get("city") or "Palo Alto",
                        "timezone": data.get("timezone") or "America/Los_Angeles",
                    }
    except Exception as err:
        print(f"Failed to fetch geo for {ip}: {err}")
    return {
        "country": "United States",
        "country_code": "US",
        "region": "California",
        "city": "Palo Alto",
        "timezone": "America/Los_Angeles",
    }

async def enrich_proxy_with_geo(proxy: dict) -> dict:
    if not proxy.get("timezone") or not proxy.get("city"):
        geo = await fetch_proxy_geo(proxy.get("host", ""))
        proxy.update(geo)
    return proxy

def test_proxy_ping(host: str, port: int, timeout_sec: float = 4.0) -> dict:
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_sec)
    try:
        s.connect((host, int(port)))
        latency = int((time.time() - start) * 1000)
        s.close()
        return {"success": True, "latency_ms": latency}
    except Exception as err:
        return {"success": False, "error": str(err), "latency_ms": None}
