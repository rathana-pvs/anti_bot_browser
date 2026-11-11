"""The Page name and follower count that warming visits read from each account's own Page.

Only Facebook Pages are tracked; a personal profile is never updated.

Account data, kept in its own small file in the account's folder so that
saving it can never overwrite an edit to the account's settings. Only a
completed warming visit writes it. A reading that misses a value keeps the one
saved before: an unreadable follower line never wipes a good number.
"""

import json
import threading
from datetime import datetime, timedelta, timezone

from backend.config import PROFILES_DIR
from backend.services.profile_service import atomic_write_json

FILE_NAME = "facebook_page.json"
MAX_NAME = 80
MAX_FOLLOWERS = 10_000_000_000
# Older than this, the next Auto warming visit starts on the profile to read it again.
REFRESH_AFTER = timedelta(days=3)

_lock = threading.Lock()


def _path(profile_id: str):
    if not profile_id or "/" in profile_id or "\\" in profile_id or profile_id.startswith("."):
        return None
    return PROFILES_DIR / profile_id / FILE_NAME


def sanitize(snapshot) -> dict | None:
    """The usable part of a runner's snapshot, or None when nothing in it is usable."""
    if not isinstance(snapshot, dict):
        return None
    clean = {}
    name = snapshot.get("name")
    if (isinstance(name, str) and (name := " ".join(name.split())[:MAX_NAME]) and len(name) >= 2
            and any(char.isalpha() for char in name)):
        clean["name"] = name
    if snapshot.get("kind") != "page":
        return None  # only Pages are tracked; personal profiles are not updated
    clean["kind"] = "page"
    followers = snapshot.get("followers")
    if isinstance(followers, int) and not isinstance(followers, bool) and 0 <= followers < MAX_FOLLOWERS:
        clean["followers"] = followers
    # A Page whose name could not be read (a script shown as boxes) still gets its follower count.
    return clean if "name" in clean or "followers" in clean else None


def load(profile_id: str) -> dict | None:
    path = _path(profile_id)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and (value.get("name") or isinstance(value.get("followers"), int)) else None


def record(profile_id: str, snapshot, execution_id: str | None = None, now: datetime | None = None) -> dict | None:
    """Save what a warming visit read; returns the saved data, or None when nothing was saved."""
    clean = sanitize(snapshot)
    path = _path(profile_id)
    if clean is None or path is None or not path.parent.is_dir():
        return None
    now = now or datetime.now(timezone.utc)
    with _lock:
        saved = load(profile_id) or {}
        value = {**saved, **clean, "seen_at": now.isoformat(), "execution_id": execution_id}
        if "followers" in clean:
            value["followers_seen_at"] = now.isoformat()
        atomic_write_json(path, value)
    return value


def record_from_result(profile_id: str, result, execution_id: str | None = None) -> None:
    """Best effort from a finished warming result: never raises into result handling."""
    try:
        if isinstance(result, dict) and result.get("account_snapshot") is not None:
            record(profile_id, result["account_snapshot"], execution_id)
    except Exception as error:
        print(f"[Facebook page] Could not save the page name and followers for {profile_id}: {error}")


def needs_refresh(profile_id: str, now: datetime | None = None) -> bool:
    """No reading yet, or the last one is old: the next visit should start on the profile."""
    saved = load(profile_id)
    if not saved:
        return True
    try:
        seen = datetime.fromisoformat(str(saved.get("seen_at")).replace("Z", "+00:00"))
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - seen >= REFRESH_AFTER
