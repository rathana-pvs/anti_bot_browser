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
    zone_name = (config.get("fingerprint") or {}).get("timezone") or (config.get("requested_environment") or {}).get("timezone")
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


def admits(execution: dict, batch: dict, due_ms: float, now_ms: float, cache: dict) -> bool:
    if execution.get("cloud_attempt_id") or execution.get("status") != "pending":
        return True
    if (batch.get("schedule_window") or {}).get("start_now") is True:
        return True
    if now_ms - due_ms >= MAX_HOLD_MS:
        return True
    return within_active_hours(_profile_config(execution.get("profile_id", ""), cache), now_ms)
