"""Accounts on hold, and the gentle restart after a proxy change.

A user puts an account on hold on purpose, usually before changing its proxy.
While on hold, nothing automatic touches it: no queue item or Auto warming visit
starts, and cloud jobs are refused. A post that is already publishing finishes.
Opening the browser by hand still works.

A new proxy means Facebook sees a new IP and location. First the account
travels: nothing runs for as long as a real person would need to get from the
old proxy's place to the new one (`travel_hours`). Then, until 24 hours after
the change, the account is shown as warming up, and while Auto warming is on its
posts wait until one warming visit has finished from the new IP. A proxy changed
while on hold starts this restart when the hold is released.

Both are stored in the account section of the profile config: `on_hold`
({since, note}) and `proxy_restart` ({since, until, travel_until, travel_hours,
to} or {pending: true, travel_hours, to}).
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.config import PROFILES_DIR

RESTART_HOURS = 24
# Hours a person needs to get from the old proxy's place to the new one.
TRAVEL_SAME_AREA = 1        # same city or state: just a new IP
TRAVEL_SAME_ZONE = 4        # another state in the same time zone
TRAVEL_SAME_COUNTRY = 8     # another time zone in the same country
TRAVEL_ABROAD = 20          # another country
TRAVEL_UNKNOWN = 8
# Keys only these helpers write; the account settings form never changes them.
SERVER_KEYS = ("on_hold", "proxy_restart")
NETWORK_KEYS = ("mode", "proxy_id", "proxy_host", "proxy_port", "proxy_user")
WAITING_STATUSES = ("pending", "ready")


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def on_hold(config: dict) -> dict | None:
    value = (config.get("account") or {}).get("on_hold")
    return value if isinstance(value, dict) and value.get("since") else None


def _load(profile_id: str) -> dict:
    try:
        config = json.loads((PROFILES_DIR / profile_id / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return config if isinstance(config, dict) else {}


def is_on_hold(profile_id: str) -> bool:
    return on_hold(_load(profile_id)) is not None


def paused_reason(profile_id: str) -> str | None:
    return is_paused(_load(profile_id))


def hold(config: dict, note: str, now: datetime) -> None:
    account = config.setdefault("account", {})
    account["on_hold"] = {"since": now.isoformat(), "note": note.strip()[:120]}


def release(config: dict, now: datetime) -> None:
    account = config.setdefault("account", {})
    account.pop("on_hold", None)
    if (account.get("proxy_restart") or {}).get("pending"):
        _start_restart(account, now)


def proxy_changed(before: dict | None, after: dict | None) -> bool:
    before, after = before or {}, after or {}
    return any(before.get(key) != after.get(key) for key in NETWORK_KEYS)


def _offset_hours(zone: str | None, now: datetime) -> float | None:
    try:
        offset = now.astimezone(ZoneInfo(zone)).utcoffset() if zone else None
    except (ValueError, KeyError):
        return None
    return offset.total_seconds() / 3600 if offset is not None else None


def travel_hours(before: dict | None, after: dict | None, now: datetime | None = None) -> float:
    """How long the account rests after moving from `before` to `after`, two places with any of
    city, region, country_code and timezone. No earlier place (a first proxy) means no trip."""
    if not before or not after:
        return 0
    now = now or datetime.now(timezone.utc)
    same = lambda key: bool(before.get(key)) and before.get(key) == after.get(key)
    country = same("country_code")
    if country and (same("city") or same("region")):
        return TRAVEL_SAME_AREA
    gap = None
    a, b = _offset_hours(before.get("timezone"), now), _offset_hours(after.get("timezone"), now)
    if a is not None and b is not None:
        gap = abs(a - b)
    if before.get("country_code") and after.get("country_code"):
        if not country:
            return TRAVEL_ABROAD
        return TRAVEL_SAME_ZONE if gap == 0 else TRAVEL_SAME_COUNTRY
    # Location unknown: about two hours per hour of time difference.
    if gap is None:
        return TRAVEL_UNKNOWN
    return TRAVEL_SAME_ZONE if gap == 0 else min(TRAVEL_ABROAD, max(3, 2 * gap))


def _start_restart(account: dict, now: datetime, hours: float | None = None, to: str | None = None) -> None:
    pending = account.get("proxy_restart") or {}
    hours = pending.get("travel_hours", 0) if hours is None else hours
    to = pending.get("to") if to is None else to
    account["proxy_restart"] = {"since": now.isoformat(),
                                "until": (now + timedelta(hours=max(RESTART_HOURS, hours))).isoformat(),
                                "travel_until": (now + timedelta(hours=hours)).isoformat(),
                                "travel_hours": hours, "to": to}


def note_proxy_change(config: dict, now: datetime, before: dict | None = None, after: dict | None = None) -> None:
    """A new proxy restarts the account gently, now or when its hold is released. `before` and
    `after` are the old and new places (see travel_hours)."""
    account = config.setdefault("account", {})
    hours = travel_hours(before, after, now)
    to = ", ".join(v for v in ((after or {}).get("city"), (after or {}).get("country_code")) if v) or None
    if on_hold(config):
        account["proxy_restart"] = {"pending": True, "travel_hours": hours, "to": to}
    else:
        _start_restart(account, now, hours, to)


def place_of(network: dict | None, pool: dict, fallback_timezone: str | None = None) -> dict | None:
    """Where an account appears with this network: the pool proxy's exit location, or just a
    timezone for a custom proxy or this PC's own connection."""
    network = network or {}
    proxy = pool.get(network.get("proxy_id")) if network.get("mode") == "pool" else None
    if proxy and proxy.get("geo_source") != "unknown":
        return {key: proxy.get(key) for key in ("city", "region", "country_code", "timezone")}
    return {"timezone": fallback_timezone} if fallback_timezone else None


def travelling_until(config: dict, now: datetime) -> datetime | None:
    """While set, the account is between places: nothing automatic runs for it."""
    value = (config.get("account") or {}).get("proxy_restart")
    if not isinstance(value, dict) or value.get("pending"):
        return None
    until = _parse(value.get("travel_until"))
    return until if until and until > now else None


def is_paused(config: dict, now: datetime | None = None) -> str | None:
    """Why nothing automatic may run now: on hold or travelling to a new proxy's place."""
    if on_hold(config):
        return "on_hold"
    if travelling_until(config, now or datetime.now(timezone.utc)):
        return "travelling"
    return None


def restart(config: dict, now: datetime) -> dict | None:
    """The restart window in effect now, if any."""
    value = (config.get("account") or {}).get("proxy_restart")
    if not isinstance(value, dict) or value.get("pending"):
        return None
    since, until = _parse(value.get("since")), _parse(value.get("until"))
    if not since or not until or until <= now:
        return None
    travel = _parse(value.get("travel_until"))
    return {"since": since, "until": until, "travel_until": travel if travel and travel > now else None, "to": value.get("to")}


def visited_since(entries, since: datetime) -> bool:
    """A warming visit finished after the restart began."""
    return any(entry.get("kind") == "warming" and entry.get("status") == "completed"
               and (_parse(entry.get("at")) or since) > since for entry in entries)


def waiting_for_visit(config: dict, entries, now: datetime) -> bool:
    window = restart(config, now)
    return window is not None and not visited_since(entries, window["since"])


def admits(execution: dict, post: dict, config: dict, now_ms: float, history) -> bool:
    """Whether the dispatcher may start this item now.

    `history` is called only when needed and returns the account history by
    profile. Running items are never touched.
    """
    if execution.get("status") not in WAITING_STATUSES:
        return True
    if on_hold(config):
        return False
    if travelling_until(config, datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)):
        return False
    if post.get("type") == "warming" or execution.get("cloud_attempt_id"):
        return True
    from backend.services import auto_warming
    # Without Auto warming no visit would come, so posts are not held.
    if not auto_warming.enabled():
        return True
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    if restart(config, now) is None:
        return True
    entries = (history().get(execution.get("profile_id")) or {}).values()
    return not waiting_for_visit(config, entries, now)
