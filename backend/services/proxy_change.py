"""Changing an account's proxy: moving it to another proxy already in the pool.

("Replace" is different: Webshare gives a proxy a new IP; see webshare_service.)

A change is always done by a person, one account at a time, and never while the account is
running: an open browser, a job in progress, or (for a phone account) any job on its phone, since
trying the new proxy switches the phone to that account's Android user.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.config import PROFILES_DIR
from backend.services import account_activity, account_hold, proxy_service


class AccountRunning(ValueError):
    pass


def _config(profile_id: str) -> dict:
    return json.loads((PROFILES_DIR / profile_id / "config.json").read_text(encoding="utf-8"))


def _phone_serial(config: dict) -> str | None:
    from backend.services.android_devices import ANDROID_RUNTIME
    runtime = config.get("runtime") or {}
    return runtime.get("device_serial") if runtime.get("kind") == ANDROID_RUNTIME else None


def running_reason(profile_id: str, config: dict | None = None) -> str | None:
    """Why this account's proxy can't change now, or None when it can."""
    from backend.services import automation_service
    config = config if config is not None else _config(profile_id)
    if automation_service.is_task_process_active(automation_service.active_automation_tasks.get(profile_id)):
        return "A job is running on this account. Change the proxy when it finishes."
    try:
        leases = automation_service.current_scheduler_snapshot().get("leases") or []
    except Exception:
        leases = []
    if any(lease.get("profile_id") == profile_id for lease in leases):
        return "A job is running on this account. Change the proxy when it finishes."
    serial = _phone_serial(config)
    if serial:
        from backend.services import android_devices
        if android_devices.job_on(serial):
            return "The phone is running a job. Change the proxy when it finishes."
        return None
    from backend.services.docker_service import get_container_status
    if get_container_status(profile_id) in {"running", "paused"}:
        return "The browser is open. Stop it, then change the proxy."
    return None


def ensure_not_running(profile_id: str, config: dict | None = None) -> None:
    reason = running_reason(profile_id, config)
    if reason:
        raise AccountRunning(reason)


def network_changes(current: dict | None, intent: dict) -> bool:
    """Whether saving this network intent would put the account on a different proxy."""
    current = current or {}
    mode = intent.get("mode")
    if mode != current.get("mode"):
        return True
    if mode == "pool":
        return intent.get("proxy_id") != current.get("proxy_id")
    if mode == "custom":
        return (str(intent.get("host") or "").strip(), int(intent.get("port") or 0), str(intent.get("username") or "").strip(),
                str(intent.get("password") or "")) != (current.get("proxy_host") or "", int(current.get("proxy_port") or 0),
                                                       current.get("proxy_user") or "", current.get("proxy_pass") or "")
    return False


def _public(proxy: dict) -> dict:
    return {key: proxy.get(key) for key in (
        "id", "host", "port", "username", "type", "group", "country", "country_code", "region", "city", "timezone",
        "geo_source", "latency_ms", "reachable", "rating", "speed_quality", "replaced_by", "replaced_by_address",
        "replaced_from")} | {"accounts": len(proxy_service.holders(proxy))}


def options(profile_id: str, now: datetime | None = None) -> dict:
    """The account's proxy, and every pool proxy it can change to with the rest that would follow."""
    now = now or datetime.now(timezone.utc)
    config = _config(profile_id)
    network = config.get("network") or {}
    pool = proxy_service.load_proxy_pool()
    by_id = {p.get("id"): p for p in pool}
    current = by_id.get(network.get("proxy_id")) if network.get("mode") == "pool" else None
    zone = (config.get("requested_environment") or {}).get("timezone")
    here = account_hold.place_of(network, by_id, zone)
    last = account_activity.last_active(profile_id, config, now)
    limit = proxy_service.account_limit()
    choices = []
    for proxy in pool:
        if proxy is current or proxy.get("replaced_by") or proxy.get("group") == "Poor proxy":
            continue
        if proxy.get("held_by_phone") and not proxy.get("password"):
            continue
        if not proxy_service.has_room(proxy, profile_id, limit):
            continue
        there = account_hold.place_of({"mode": "pool", "proxy_id": proxy["id"]}, by_id, proxy.get("timezone"))
        travel = account_hold.travel_hours(here, there, now)
        start = min(last, now) if last else now
        rest = max(0.0, (start - now).total_seconds() / 3600 + travel)
        choices.append({**_public(proxy), "travel_hours": travel, "rest_hours": round(rest, 1),
                        "suggested": bool(current and current.get("replaced_by") == proxy["id"]),
                        "same_country": bool(here and here.get("country_code") and here.get("country_code") == proxy.get("country_code"))})
    choices.sort(key=lambda c: (not c["suggested"], not c["same_country"], c["rest_hours"], c["latency_ms"] or 10**6))
    return {"profile_id": profile_id, "phone": bool(_phone_serial(config)), "running_reason": running_reason(profile_id, config),
            "current": _public(current) if current else None,
            "current_network": {"mode": network.get("mode"), "host": network.get("proxy_host"), "port": network.get("proxy_port")},
            "last_active": last.isoformat() if last else None, "options": choices}


def change(profile_id: str, proxy_id: str, move_pc) -> None:
    """Moves the account to `proxy_id` through the same path as its settings (PC) or the phone's
    proxy change, which keeps the new proxy only if traffic gets through."""
    config = _config(profile_id)
    ensure_not_running(profile_id, config)
    if (config.get("network") or {}).get("proxy_id") == proxy_id:
        raise ValueError("The account already uses this proxy.")
    if _phone_serial(config):
        from backend.services import android_ownership
        android_ownership.replace_proxy(profile_id, proxy_id)
    else:
        move_pc(profile_id, proxy_id)
