"""Active hours: each account's desktop items wait for its usual waking hours.

A desktop batch item waits until its account's usual hours, taken from the
same persona the automation uses. Cloud attempts are scheduled by the cloud
and are never held here, nor are "start now" batches or items whose
preparation has already started.
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from automation.engine.behavior_profile import Persona
from backend.config import PROFILES_DIR

# Never hold an item longer than this past its due time.
MAX_HOLD_MS = 8 * 3600 * 1000


def _profile_config(profile_id: str, cache: dict) -> dict:
    if profile_id not in cache:
        try:
            value = json.loads((PROFILES_DIR / profile_id / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            value = {}
        cache[profile_id] = value if isinstance(value, dict) else {}
    return cache[profile_id]


# Without a persona seed, a person is assumed awake from 08:00 to 22:00.
DEFAULT_HOURS = (8, 14)


def account_zone(config: dict):
    """The account's local time: its proxy's timezone, estimated from the exit IP, so sleep, active
    hours and rest days follow where Facebook sees it. Phone accounts store this PC's timezone,
    which must never shape their day. Without a known proxy timezone, the saved one is used."""
    from backend.services import proxy_service
    network = config.get("network") or {}
    zone_name = (proxy_service.proxy_timezone(network.get("proxy_id")) if network.get("mode") == "pool" else None) \
        or (config.get("fingerprint") or {}).get("timezone") or (config.get("requested_environment") or {}).get("timezone")
    try:
        return ZoneInfo(zone_name) if zone_name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def persona_hours_admit(config: dict, now_ms: float) -> bool:
    """Whether the account's persona is awake now, whatever the switches say."""
    seed = config.get("behavior_seed")
    has_seed = isinstance(seed, int) and not isinstance(seed, bool) and seed >= 1
    start, length = Persona.from_seed(seed).active_hours if has_seed else DEFAULT_HOURS
    hour = datetime.fromtimestamp(now_ms / 1000, tz=account_zone(config)).hour
    return (hour - start) % 24 < length


def within_active_hours(config: dict, now_ms: float) -> bool:
    seed = config.get("behavior_seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 1:
        return True
    return persona_hours_admit(config, now_ms)


def applies(execution: dict, batch: dict, config: dict, post: dict | None = None) -> bool:
    """Whether this item keeps to its account's waking hours at all."""
    if execution.get("cloud_attempt_id") or execution.get("status") != "pending":
        return False
    window = batch.get("schedule_window") or {}
    if window.get("start_now") is True:
        return False
    if (post or {}).get("type") == "warming":  # warming always keeps to them
        return True
    # The batch's "Follow active hours" switch: off means the schedule picked on this PC wins.
    if window.get("follow_active_hours") is False:
        return False
    runtime = config.get("runtime") or {}
    if runtime.get("kind") == "android_user":
        from backend.services.android_devices import follows_active_hours
        if not follows_active_hours(runtime.get("device_serial") or ""):
            return False
    return True


def admits(execution: dict, batch: dict, due_ms: float, now_ms: float, cache: dict, post: dict | None = None) -> bool:
    if execution.get("cloud_attempt_id") or execution.get("status") != "pending":
        return True
    # A planned (Follow active hours) batch waits for the real morning; others at most MAX_HOLD_MS.
    if now_ms - due_ms >= MAX_HOLD_MS and (batch.get("schedule_window") or {}).get("timing") != "auto":
        return True
    config = _profile_config(execution.get("profile_id", ""), cache)
    if not applies(execution, batch, config, post):
        return True
    return within_active_hours(config, now_ms)


def waking_hours(config: dict) -> tuple[int, int] | None:
    """(start hour, length) of the account's day in its own zone, or None when it has no persona."""
    seed = config.get("behavior_seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 1:
        return None
    return Persona.from_seed(seed).active_hours


def next_awake_ms(config: dict, from_ms: float) -> float:
    """The first moment at or after `from_ms` when the account is awake."""
    if within_active_hours(config, from_ms):
        return from_ms
    zone = account_zone(config)
    local = datetime.fromtimestamp(from_ms / 1000, tz=zone).replace(minute=0, second=0, microsecond=0)
    for hours in range(1, 25):
        candidate = local.timestamp() * 1000 + hours * 3600_000
        if within_active_hours(config, candidate):
            # Wall-clock hour start in the zone (DST-safe enough for an hourly window).
            return datetime.fromtimestamp(candidate / 1000, tz=zone).replace(minute=0, second=0, microsecond=0).timestamp() * 1000
    return from_ms
