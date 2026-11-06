"""When an account was last active on Facebook, for the rest after a proxy change.

The trip to a new proxy's place counts from this moment, not from the change: an account
whose browser closed 10 hours ago could already have travelled anywhere a 10-hour trip reaches.

Sources, latest wins:
- PC: the browser container. Running means active now; otherwise Docker's last stop time, or the
  stop time this app saved, which survives a recreated container.
- Phone: the last time the app opened the account; on screen right now means active now.
- Both: the latest queue item in the account's history (post, visit, comment, check).
Saved times live in data/account_activity.json, apart from the profile configs. Unknown activity
returns None, and the rest then counts from the change, to be safe.
"""
from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone

from backend.config import DATA_DIR

ACTIVITY_FILE = DATA_DIR / "account_activity.json"
_lock = threading.Lock()


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    try:
        # Docker gives nanoseconds; Python reads up to microseconds.
        head, dot, tail = value.replace("Z", "+00:00").partition(".")
        if dot:
            digits = "".join(ch for ch in tail if ch.isdigit())
            zone = tail[len(digits):]
            value = f"{head}.{digits[:6]}{zone}"
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def container_activity(profile_id: str, now: datetime) -> datetime | None:
    """Now while the browser runs; else when its container last stopped."""
    try:
        output = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}} {{.State.FinishedAt}}", f"isolated_{profile_id}"],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if output.returncode != 0:
        return None
    running, _, finished = output.stdout.strip().partition(" ")
    return now if running == "true" else _parse(finished)


def history_activity(entries) -> datetime | None:
    times = [_parse(entry.get("at")) for entry in entries or [] if isinstance(entry, dict)]
    return max((t for t in times if t), default=None)


def last_active(profile_id: str, config: dict, now: datetime | None = None, entries=None,
                container=container_activity, phone_on_screen=None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    if entries is None:
        from backend.services import account_health
        entries = account_health.entries_for(account_health._load_history(), profile_id)
    phone = (config.get("runtime") or {}).get("kind") == "android_user"
    found = [history_activity(entries), _parse(_saved().get(profile_id))]
    if phone:
        if phone_on_screen is not None and phone_on_screen():
            found.append(now)
    else:
        found.append(container(profile_id, now))
    times = [min(t, now) for t in found if t]
    return max(times) if times else None


def _saved() -> dict:
    try:
        value = json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def note_active(profile_id: str, at: datetime | None = None) -> None:
    """Saves that the account was on Facebook now: a browser stopping, or a phone account opened."""
    from backend.services.profile_service import atomic_write_json
    with _lock:
        saved = _saved()
        saved[profile_id] = (at or datetime.now(timezone.utc)).isoformat()
        try:
            ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(ACTIVITY_FILE, saved)
        except OSError:
            pass
