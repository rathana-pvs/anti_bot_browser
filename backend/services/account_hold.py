"""Accounts on hold, and the gentle restart after a proxy change.

A user puts an account on hold on purpose, usually before changing its proxy.
While on hold, nothing automatic touches it: no queue item or Auto warming visit
starts, and cloud jobs are refused. A post that is already publishing finishes.
Opening the browser by hand still works.

A new proxy means Facebook sees a new IP and location. For the next 24 hours
the account is shown as warming up, and while Auto warming is on its posts wait
until one warming visit has finished from the new IP. A proxy changed while
on hold starts this restart when the hold is released.

Both are stored in the account section of the profile config: `on_hold`
({since, note}) and `proxy_restart` ({since, until} or {pending: true}).
"""

import json
from datetime import datetime, timedelta, timezone

from backend.config import PROFILES_DIR

RESTART_HOURS = 24
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


def is_on_hold(profile_id: str) -> bool:
    try:
        config = json.loads((PROFILES_DIR / profile_id / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(config, dict) and on_hold(config) is not None


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


def _start_restart(account: dict, now: datetime) -> None:
    account["proxy_restart"] = {"since": now.isoformat(),
                                "until": (now + timedelta(hours=RESTART_HOURS)).isoformat()}


def note_proxy_change(config: dict, now: datetime) -> None:
    """A new proxy restarts the account gently, now or when its hold is released."""
    account = config.setdefault("account", {})
    if on_hold(config):
        account["proxy_restart"] = {"pending": True}
    else:
        _start_restart(account, now)


def restart(config: dict, now: datetime) -> dict | None:
    """The restart window in effect now, if any."""
    value = (config.get("account") or {}).get("proxy_restart")
    if not isinstance(value, dict) or value.get("pending"):
        return None
    since, until = _parse(value.get("since")), _parse(value.get("until"))
    if not since or not until or until <= now:
        return None
    return {"since": since, "until": until}


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
