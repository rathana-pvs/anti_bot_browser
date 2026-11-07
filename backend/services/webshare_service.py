"""Replace bad pool proxies through the Webshare API.

The API key stays on this PC: `data/webshare.json`, readable only by this user and never sent
anywhere but proxy.webshare.io. The API never returns it, only whether one is saved.

A replacement asks Webshare to swap one proxy's IP for another in the same country, then adds
the new proxy to the pool as Available and looks up its location.

Accounts are never moved automatically: a new IP is a risk the person times themselves. The old
entry stays with its accounts, marked `replaced_by`, so each account shows "Proxy replaced" until
someone gives it another proxy; it takes no new account. With no account on it, it is deleted.

One proxy per Webshare replacement, so the old→new pair is never ambiguous.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

import httpx

from backend.config import DATA_DIR
from backend.services import proxy_service

API_BASE = "https://proxy.webshare.io/api"
REQUEST_TIMEOUT = 20
POLL_SECONDS = 3
REPLACE_TIMEOUT = 300
KEY_FILE = DATA_DIR / "webshare.json"
_key_lock = threading.Lock()


class WebshareError(RuntimeError):
    pass


# --- API key --------------------------------------------------------------------------------------

def _read_key_file() -> dict:
    try:
        data = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def api_key() -> str | None:
    key = _read_key_file().get("api_key")
    return key if isinstance(key, str) and key else None


def key_status() -> dict:
    key = api_key()
    return {"webshare_key_set": bool(key), "webshare_key_hint": f"…{key[-4:]}" if key and len(key) > 8 else None}


def _write_key(key: str | None) -> None:
    with _key_lock:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not key:
            KEY_FILE.unlink(missing_ok=True)
            return
        tmp = KEY_FILE.with_suffix(".tmp")
        # Created owner-only, so the key is never readable by other users on this PC, even briefly.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"api_key": key}, f)
        tmp.replace(KEY_FILE)


def set_api_key(key: str | None, verify: Callable[[str], None] | None = None) -> dict:
    """Saves the key after Webshare accepts it. An empty key removes it."""
    key = (key or "").strip()
    if key:
        if len(key) > 200 or any(c.isspace() for c in key):
            raise ValueError("That doesn't look like a Webshare API key.")
        (verify or verify_key)(key)
    _write_key(key or None)
    return key_status()


# --- HTTP ---------------------------------------------------------------------------------------

def _request(method: str, path: str, key: str | None = None, **kwargs) -> dict:
    key = key or api_key()
    if not key:
        raise WebshareError("Add your Webshare API key on the Proxies page first.")
    try:
        response = httpx.request(method, f"{API_BASE}{path}", headers={"Authorization": f"Token {key}"},
                                 timeout=REQUEST_TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        raise WebshareError(f"Couldn't reach Webshare: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise WebshareError("Webshare rejected the API key.")
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:200]
        raise WebshareError(f"Webshare answered {response.status_code}: {detail}")
    return response.json()


def verify_key(key: str) -> None:
    _request("GET", "/v2/proxy/list/", key, params={"mode": "direct", "page": 1, "page_size": 1})


def find_webshare_proxy(ip: str) -> dict | None:
    data = _request("GET", "/v2/proxy/list/", params={"mode": "direct", "proxy_address": ip, "page_size": 10})
    return next((p for p in data.get("results") or [] if p.get("proxy_address") == ip), None)


def _replace(ip: str, country_code: str | None) -> dict:
    replace_with = ([{"type": "country", "country_code": country_code, "count": 1}] if country_code
                    else [{"type": "any", "count": 1}])
    return _request("POST", "/v3/proxy/replace/", json={
        "to_replace": {"type": "ip_address", "ip_addresses": [ip], "count": 1},
        "replace_with": replace_with, "dry_run": False})


def _wait_for(replacement_id: int, sleep=time.sleep, timeout: float = REPLACE_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        state = _request("GET", f"/v3/proxy/replace/{replacement_id}/")
        if state.get("state") == "completed":
            return state
        if state.get("state") == "failed":
            raise WebshareError(state.get("error") or state.get("error_code") or "Webshare couldn't replace this proxy.")
        if time.monotonic() > deadline:
            raise WebshareError("Webshare is still replacing this proxy. Check again in a few minutes.")
        sleep(POLL_SECONDS)


REPLACED_PAGES = 20
MAX_HOPS = 5


def _replaced_rows(**params) -> list[dict]:
    """Rows of Webshare's replaced-proxy list, following its pages."""
    rows, page = [], 1
    while page <= REPLACED_PAGES:
        data = _request("GET", "/v2/proxy/list/replaced/", params={**params, "page": page, "page_size": 100})
        rows += data.get("results") or []
        if not data.get("next"):
            break
        page += 1
    return rows


def _latest(rows: list[dict], ip: str, port: int | None = None) -> tuple[str, int] | None:
    matches = [r for r in rows if r.get("proxy") == ip and r.get("replaced_with")
               and (port is None or r.get("proxy_port") in (None, port))]
    if not matches:
        return None
    row = max(matches, key=lambda r: r.get("created_at") or "")
    return row["replaced_with"], int(row["replaced_with_port"])


def _replaced_with(replacement_id: int, ip: str) -> tuple[str, int]:
    found = _latest(_replaced_rows(proxy_list_replacement=replacement_id), ip) or _latest(_replaced_rows(), ip)
    if not found:
        raise WebshareError("Webshare finished, but didn't say which proxy replaced this one.")
    return found


def already_replaced(ip: str, port: int | None) -> tuple[str, int, dict] | None:
    """The proxy that now stands in for an IP Webshare already swapped (by an earlier run that
    stopped before adding it, the dashboard or auto-replace), following a chain of swaps to the
    one still in the account. None when Webshare never replaced it."""
    rows = _replaced_rows()
    current, current_port = ip, port
    for _ in range(MAX_HOPS):
        nxt = _latest(rows, current, current_port)
        if not nxt:
            return None
        current, current_port = nxt
        found = find_webshare_proxy(current)
        if found:
            return current, current_port, found
    return None


# --- Replacement --------------------------------------------------------------------------------

def replace_one(proxy_id: str, sleep=time.sleep) -> dict:
    """Replaces one pool proxy at Webshare. Its accounts stay on it, marked replaced."""
    old = next((p for p in proxy_service.load_proxy_pool(raise_errors=True) if p.get("id") == proxy_id), None)
    if not old:
        raise WebshareError("This proxy is no longer in the pool.")
    if old.get("replaced_by"):
        raise WebshareError(f"Already replaced by {old.get('replaced_by_address') or 'another proxy'}.")
    ip = old["host"]
    found = find_webshare_proxy(ip)
    if not found:
        # Webshare may have swapped it already; then only the pool is behind, and is caught up.
        earlier = already_replaced(ip, old.get("port"))
        if not earlier:
            raise WebshareError(f"{ip} isn't one of your Webshare proxies.")
        new_ip, new_port, fresh = earlier
        return {**_adopt(old, new_ip, new_port, fresh, {}), "already_replaced": True,
                "country_code": fresh.get("country_code")}
    replacement = _replace(ip, found.get("country_code"))
    _wait_for(int(replacement["id"]), sleep=sleep)
    new_ip, new_port = _replaced_with(int(replacement["id"]), ip)
    return {**_adopt(old, new_ip, new_port, find_webshare_proxy(new_ip) or {}, found),
            "country_code": found.get("country_code")}


def _adopt(old: dict, new_ip: str, new_port: int, fresh: dict, found: dict) -> dict:
    """Adds the new proxy to the pool as Available; the old one keeps its accounts, marked replaced."""
    proxy_id, ip = old["id"], old["host"]
    username = fresh.get("username") or found.get("username") or old.get("username") or ""
    password = fresh.get("password") or found.get("password") or old.get("password") or ""

    added = proxy_service.import_proxies_from_text(f"{new_ip}:{new_port}:{username}:{password}")["added_ids"]
    new_entry = (proxy_service.find_proxy(new_ip, new_port, username) if not added else
                 next((p for p in proxy_service.load_proxy_pool() if p["id"] == added[0]), None))
    if not new_entry:
        raise WebshareError(f"Webshare gave {new_ip}:{new_port}, but it couldn't be added to the pool.")
    new_id = new_entry["id"]
    proxy_service.update_proxy_metadata([new_id], type=old.get("type") or "socks5",
                                        replaced_from=f"{ip}:{old.get('port')}")
    proxy_service.refresh_proxy_statuses([new_id])

    new_address = f"{new_ip}:{new_port}"
    accounts = proxy_service.holders(old)
    if accounts:
        proxy_service.update_proxy_metadata([proxy_id], replaced_by=new_id, replaced_by_address=new_address,
                                            replaced_at=_now())
    else:
        proxy_service.delete_proxy(proxy_id)
    return {"proxy_id": proxy_id, "old": f"{ip}:{old.get('port')}", "new": new_address, "new_id": new_id,
            "accounts": accounts}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplaceRun:
    """One run at a time, one proxy after another; the Proxies page polls `status()`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict | None = None

    def status(self) -> dict:
        with self._lock:
            if not self._state:
                return {"state": "idle"}
            return {**self._state, "results": [dict(r) for r in self._state["results"]]}

    def running(self) -> bool:
        with self._lock:
            return bool(self._state and self._state["state"] == "running")

    def start(self, proxy_ids: list[str], replace_fn=None) -> dict:
        if not api_key():
            raise WebshareError("Add your Webshare API key on the Proxies page first.")
        ids = list(dict.fromkeys(proxy_ids))
        with self._lock:
            if self._state and self._state["state"] == "running":
                raise RuntimeError("Proxies are already being replaced")
            self._state = {"run_id": uuid.uuid4().hex, "state": "running", "started_at": _now(), "finished_at": None,
                           "total": len(ids), "done": 0, "running_id": None, "results": []}
        status = self.status()
        threading.Thread(target=self._run, args=(ids, replace_fn or replace_one),
                         daemon=True, name="webshare-replace").start()
        return status

    def _run(self, ids: list[str], replace_fn) -> None:
        for proxy_id in ids:
            with self._lock:
                self._state["running_id"] = proxy_id
            try:
                result = {**replace_fn(proxy_id), "error": None}
            except Exception as exc:  # One proxy failing never stops the others.
                result = {"proxy_id": proxy_id, "error": str(exc)}
            with self._lock:
                self._state["results"].append(result)
                self._state["done"] += 1
        with self._lock:
            self._state.update(state="finished", running_id=None, finished_at=_now())


runner = ReplaceRun()
