"""Proxy sources: where a set of pool proxies comes from.

A source is one provider account or one batch of proxies, such as "Webshare residential" or
"Manual list". Every pool proxy belongs to exactly one source (`source_id` on the proxy). A
source sets:

- `provider`: `manual`, or `webshare` (its API key can replace proxies).

A source with a provider API key (`uses_api`) gets its proxies only from that API: they are
synced from the provider, and pasted proxies can't be imported into it.
- `protocol`: `socks5` or `http`, used for every proxy imported into it. Fixed once it has proxies.
- `accounts_per_proxy`: its own limit, or None to follow the global setting.
- `use_on`: `both`, `pc` or `phone`. Phones only use SOCKS5, so an HTTP source is PC only.

Sources live beside the pool in `proxy_sources.json`. API keys live in `proxy_source_keys.json`,
created readable only by this user, and are never returned: the API shows a 4-character hint.

The first load creates the `default` source and moves every existing proxy into it. A Webshare
key saved before sources existed (`data/webshare.json`) becomes the default source's key.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from backend.config import DATA_DIR

DEFAULT_ID = "default"
PROVIDERS = ("manual", "webshare")
PROTOCOLS = ("socks5", "http")
USE_ON = ("both", "pc", "phone")
MAX_NAME = 60
MAX_KEY = 200
LEGACY_KEY_FILE = DATA_DIR / "webshare.json"
PROVIDER_LABEL = {"manual": "Manual", "webshare": "Webshare"}

_lock = threading.RLock()
_cache: dict = {"key": None, "sources": []}


class SourceError(ValueError):
    pass


def _dir() -> Path:
    from backend.services import proxy_service
    return proxy_service.PROXY_POOL_FILE.parent


def _sources_file() -> Path:
    return _dir() / "proxy_sources.json"


def _keys_file() -> Path:
    return _dir() / "proxy_source_keys.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    if private:
        # Created owner-only, so a key is never readable by other users on this PC, even briefly.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _clean(source: dict) -> dict:
    limit = source.get("accounts_per_proxy")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        limit = None
    protocol = source.get("protocol") if source.get("protocol") in PROTOCOLS else "socks5"
    use_on = source.get("use_on") if source.get("use_on") in USE_ON else "both"
    if protocol != "socks5":
        use_on = "pc"
    return {
        "id": str(source.get("id") or ""),
        "name": str(source.get("name") or "Proxies")[:MAX_NAME],
        "provider": source.get("provider") if source.get("provider") in PROVIDERS else "manual",
        "protocol": protocol,
        "accounts_per_proxy": limit,
        "use_on": use_on,
    }


def _migrate() -> list[dict]:
    """The first run with sources: one default source holding every proxy and any saved key."""
    legacy = _read_json(LEGACY_KEY_FILE).get("api_key")
    legacy = legacy if isinstance(legacy, str) and legacy else None
    source = {"id": DEFAULT_ID, "name": "Webshare" if legacy else "My proxies",
              "provider": "webshare" if legacy else "manual", "protocol": "socks5",
              "accounts_per_proxy": None, "use_on": "both"}
    if legacy and not _read_json(_keys_file()).get(DEFAULT_ID):
        _write_json(_keys_file(), {**_read_json(_keys_file()), DEFAULT_ID: legacy}, private=True)
    _write_json(_sources_file(), {"sources": [source]})
    return [source]


def _load() -> list[dict]:
    with _lock:
        path = _sources_file()
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return [dict(s) for s in _migrate()]
        if _cache["key"] == key:
            return [dict(s) for s in _cache["sources"]]
        rows = _read_json(path).get("sources")
        sources = [_clean(s) for s in rows if isinstance(s, dict) and s.get("id")] if isinstance(rows, list) else []
        if not any(s["id"] == DEFAULT_ID for s in sources):
            sources.insert(0, _clean({"id": DEFAULT_ID, "name": "My proxies"}))
        _cache.update(key=key, sources=sources)
        return [dict(s) for s in sources]


def _save(sources: list[dict]) -> None:
    _write_json(_sources_file(), {"sources": sources})


# --- Keys -----------------------------------------------------------------------------------------

def api_key(source_id: str | None) -> str | None:
    key = _read_json(_keys_file()).get(source_id or DEFAULT_ID)
    return key if isinstance(key, str) and key else None


def _set_key(source_id: str, key: str | None) -> None:
    keys = {k: v for k, v in _read_json(_keys_file()).items() if k != source_id}
    if key:
        keys[source_id] = key
    if keys:
        _write_json(_keys_file(), keys, private=True)
    else:
        _keys_file().unlink(missing_ok=True)


def _key_hint(key: str | None) -> str | None:
    return f"…{key[-4:]}" if key and len(key) > 8 else None


# --- Reading --------------------------------------------------------------------------------------

def get(source_id: str | None) -> dict:
    """The source, or the default one when the id is unknown."""
    sources = _load()
    return next((s for s in sources if s["id"] == (source_id or DEFAULT_ID)), None) \
        or next(s for s in sources if s["id"] == DEFAULT_ID)


def exists(source_id: str) -> bool:
    return any(s["id"] == source_id for s in _load())


def source_of(proxy: dict) -> dict:
    return get(proxy.get("source_id"))


def limit_for(source: dict, default: int) -> int:
    return source.get("accounts_per_proxy") or default


def allowed_on(proxy: dict, surface: str | None) -> bool:
    """True when the proxy's source may serve an account on `surface` ('pc' or 'phone')."""
    use_on = source_of(proxy)["use_on"]
    return surface is None or use_on == "both" or use_on == surface


def surface_message(proxy: dict, surface: str) -> str:
    source = source_of(proxy)
    where = "phone" if surface == "phone" else "PC"
    return (f"Proxies from {source['name']} are set to {'PC' if source['use_on'] == 'pc' else 'phone'} accounts only, "
            f"so this {where} account can't use {proxy.get('host')}:{proxy.get('port')}. Choose a proxy from another source.")


def uses_api(source: dict) -> bool:
    """True when the source's proxies come from its provider's API, not from pasted lists."""
    return source["provider"] != "manual" and bool(api_key(source["id"]))


def import_message(source: dict) -> str:
    return (f"{source['name']} gets its proxies from the {PROVIDER_LABEL[source['provider']]} API, so proxies can't be "
            "pasted into it. Use Sync from Webshare, or import them into a manual source.")


def public(source: dict, pool: list[dict] | None = None) -> dict:
    """What the Proxies page sees: never the key itself."""
    key = api_key(source["id"])
    out = {**source, "key_set": bool(key), "key_hint": _key_hint(key), "uses_api": uses_api(source),
           "provider_label": PROVIDER_LABEL[source["provider"]], "is_default": source["id"] == DEFAULT_ID}
    if pool is not None:
        out["proxy_count"] = sum(1 for p in pool if (p.get("source_id") or DEFAULT_ID) == source["id"])
    return out


def list_public() -> list[dict]:
    from backend.services import proxy_service
    pool = proxy_service.load_proxy_pool()
    return [public(s, pool) for s in _load()]


# --- Changing -------------------------------------------------------------------------------------

def _verify(provider: str, key: str, verify) -> None:
    if verify is not None:
        verify(key)
    elif provider == "webshare":
        from backend.services import webshare_service
        try:
            webshare_service.verify_key(key)
        except webshare_service.WebshareError as exc:
            raise SourceError(str(exc)) from exc


def _validated(changes: dict, current: dict | None, sources: list[dict], has_proxies: bool) -> dict:
    merged = {**(current or {"provider": "manual", "protocol": "socks5", "accounts_per_proxy": None, "use_on": "both"})}
    if "name" in changes:
        name = " ".join(str(changes["name"] or "").split())
        if not name or len(name) > MAX_NAME:
            raise SourceError(f"Give the source a name of 1 to {MAX_NAME} characters.")
        if any(s["name"].casefold() == name.casefold() and s["id"] != (current or {}).get("id") for s in sources):
            raise SourceError(f"A source named “{name}” already exists.")
        merged["name"] = name
    elif not current:
        raise SourceError("Give the source a name.")
    if "provider" in changes:
        if changes["provider"] not in PROVIDERS:
            raise SourceError("Choose Manual or Webshare.")
        merged["provider"] = changes["provider"]
    if "protocol" in changes and changes["protocol"] != merged.get("protocol"):
        if changes["protocol"] not in PROTOCOLS:
            raise SourceError("Choose SOCKS5 or HTTP.")
        if has_proxies:
            raise SourceError("The protocol can't change once a source has proxies. Add a new source for "
                              f"{changes['protocol'].upper()} proxies.")
        merged["protocol"] = changes["protocol"]
    if "accounts_per_proxy" in changes:
        limit = changes["accounts_per_proxy"]
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20):
            raise SourceError("Accounts per proxy must be a whole number from 1 to 20, or empty for the default.")
        merged["accounts_per_proxy"] = limit
    if "use_on" in changes:
        if changes["use_on"] not in USE_ON:
            raise SourceError("Choose PC and phone, PC only, or phone only.")
        merged["use_on"] = changes["use_on"]
    if merged["protocol"] != "socks5" and merged["use_on"] != "pc":
        raise SourceError("Phones only use SOCKS5 proxies, so an HTTP source is for PC accounts only.")
    return merged


def create(changes: dict, verify=None) -> dict:
    key = (changes.get("api_key") or "").strip()
    with _lock:
        sources = _load()
        source = _validated(changes, None, sources, False)
        source["id"] = f"src_{uuid.uuid4().hex[:10]}"
        if key and source["provider"] != "manual":
            _check_key(key)
            _verify(source["provider"], key, verify)
        sources.append(_clean(source))
        _save(sources)
        if key and source["provider"] != "manual":
            _set_key(source["id"], key)
        return public(get(source["id"]))


def update(source_id: str, changes: dict, verify=None) -> dict:
    """`api_key`: None keeps the saved key, "" removes it, anything else replaces it once accepted."""
    from backend.services import proxy_service
    with _lock:
        sources = _load()
        current = next((s for s in sources if s["id"] == source_id), None)
        if not current:
            raise LookupError("This proxy source no longer exists.")
        has_proxies = any((p.get("source_id") or DEFAULT_ID) == source_id for p in proxy_service.load_proxy_pool())
        merged = _validated(changes, current, sources, has_proxies)
        key = changes.get("api_key")
        if isinstance(key, str) and key.strip() and merged["provider"] != "manual":
            key = key.strip()
            _check_key(key)
            _verify(merged["provider"], key, verify)
        sources = [_clean(merged) if s["id"] == source_id else s for s in sources]
        _save(sources)
        if merged["provider"] == "manual" or key == "":
            _set_key(source_id, None)
        elif isinstance(key, str) and key.strip():
            _set_key(source_id, key.strip())
        return public(get(source_id))


def delete(source_id: str) -> None:
    from backend.services import proxy_service
    if source_id == DEFAULT_ID:
        raise SourceError("The first source can't be deleted. Rename it instead.")
    with _lock:
        sources = _load()
        if not any(s["id"] == source_id for s in sources):
            raise LookupError("This proxy source no longer exists.")
        count = sum(1 for p in proxy_service.load_proxy_pool() if p.get("source_id") == source_id)
        if count:
            raise SourceError(f"This source still has {count} {'proxy' if count == 1 else 'proxies'}. Delete them first.")
        _save([s for s in sources if s["id"] != source_id])
        _set_key(source_id, None)


def _check_key(key: str) -> None:
    if len(key) > MAX_KEY or any(c.isspace() for c in key):
        raise SourceError("That doesn't look like an API key.")
