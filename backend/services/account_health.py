"""Account health stages, worked out from each account's own activity history.

The stage is advice for the user, never a block: the post creator warns about
accounts that are new, resting or need attention, and the user decides.

History is kept in its own file because the posting queue can be cleared.
Every finished queue execution is copied into it once, keyed by its id.
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.config import DATA_DIR, PROFILES_DIR
from backend.services.profile_service import atomic_write_json

HISTORY_FILE = DATA_DIR / "account_history.json"
HISTORY_DAYS = 120

STAGES = ("new", "warming_up", "ready", "resting", "attention")
# Stages a user may set by hand; resting and attention always come from history.
OVERRIDE_STAGES = ("new", "warming_up", "ready")

NEW_DAYS = 7
WARMING_UP_DAYS = 21
READY_MIN_POSTS = 10
DAILY_POST_LIMITS = {"new": 0, "warming_up": 2, "ready": 4}
PUSHBACK_REST_HOURS = 72
FAILURE_STREAK = 3

SUCCESS_STATUSES = {"published", "completed"}
FAILURE_STATUSES = {"failed", "failed_before_publish", "skipped_session_unverified"}
RECORDED_STATUSES = SUCCESS_STATUSES | FAILURE_STATUSES | {
    "uncertain", "needs_review", "failed_after_publish", "skipped_auth_required",
}
# Facebook itself refused the action, as opposed to a slow proxy or our own error.
PUSHBACK_MARKERS = ("rejected", "blocked", "restricted")

_lock = threading.Lock()


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_history() -> dict:
    try:
        value = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = value.get("profiles") if isinstance(value, dict) else None
    return profiles if isinstance(profiles, dict) else {}


def _entry(execution: dict, kind: str) -> dict | None:
    status = execution.get("status")
    if status not in RECORDED_STATUSES:
        return None
    times = [execution.get(key) for key in ("published_at", "ended_at", "stage_updated_at", "scheduled_at")]
    at = next((value for value in times if _parse(value)), None)
    if at is None:
        return None
    entry = {"at": at, "kind": kind or "post", "status": status, "error": (execution.get("error") or "")[:200]}
    if execution.get("facebook_identity") in ("page", "personal"):
        entry["identity"] = execution["facebook_identity"]
    return entry


def sync_history(queue: dict, now: datetime | None = None) -> dict:
    """Copy finished queue executions into the history and return it by profile."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=HISTORY_DAYS)
    with _lock:
        history = _load_history()
        changed = False
        for batch in queue.get("daily_batches") or []:
            for post in batch.get("posts") or []:
                for execution in post.get("executions") or []:
                    profile_id, execution_id = execution.get("profile_id"), execution.get("execution_id")
                    entry = _entry(execution, post.get("type"))
                    if not (profile_id and execution_id and entry):
                        continue
                    entries = history.setdefault(profile_id, {})
                    if entries.get(execution_id) != entry:
                        entries[execution_id] = entry
                        changed = True
        for profile_id, entries in history.items():
            for execution_id in [key for key, entry in entries.items() if (_parse(entry.get("at")) or now) < cutoff]:
                del entries[execution_id]
                changed = True
        if changed:
            atomic_write_json(HISTORY_FILE, {"version": 1, "profiles": history})
        return history


def _zone(config: dict):
    name = (config.get("fingerprint") or {}).get("timezone") or (config.get("requested_environment") or {}).get("timezone")
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def manual_stage(config: dict) -> str | None:
    account = config.get("account") or {}
    override = account.get("stage_override")
    if override in OVERRIDE_STAGES:
        return override
    # Accounts marked "Warm-up complete" before stages existed count as ready.
    return "ready" if account.get("warming_complete") is True else None


def compute(config: dict, entries: list[dict], now: datetime | None = None) -> dict:
    """The account's stage, why, and what is suggested for today."""
    now = now or datetime.now(timezone.utc)
    entries = sorted((entry for entry in entries if _parse(entry.get("at"))), key=lambda entry: entry["at"])
    zone = _zone(config)
    local_now = now.astimezone(zone)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = (today_start + timedelta(days=1)).astimezone(timezone.utc)

    posts = [entry for entry in entries if entry["status"] == "published" and entry.get("kind") != "warming"]
    successes = [entry for entry in entries if entry["status"] in SUCCESS_STATUSES]
    posts_today = sum(1 for entry in posts if _parse(entry["at"]).astimezone(zone) >= today_start)
    starts = [value for value in [_parse(config.get("created_at"))] + [_parse(entries[0]["at"]) if entries else None] if value]
    age_days = max(0, (now - min(starts)).days) if starts else 0

    override = manual_stage(config)
    if override:
        growth, reason = override, "Set by hand in the account settings."
    elif age_days < NEW_DAYS or not successes:
        growth = "new"
        reason = "No finished sessions yet." if not successes else f"Only {age_days} days of history."
    elif age_days < WARMING_UP_DAYS or len(posts) < READY_MIN_POSTS:
        growth = "warming_up"
        reason = f"{age_days} days of history and {len(posts)} posts."
    else:
        growth, reason = "ready", f"{age_days} days of history and {len(posts)} posts without recent problems."
    limit = DAILY_POST_LIMITS[growth]
    result = {
        "stage": growth, "growth_stage": growth, "reason": reason, "manual": bool(override),
        "until": None, "age_days": age_days, "posts": len(posts), "posts_today": posts_today,
        "daily_limit": limit, "last_post_at": posts[-1]["at"] if posts else None,
    }

    last_success = successes[-1]["at"] if successes else ""
    checkpoint = next((entry for entry in reversed(entries) if entry["status"] == "skipped_auth_required"), None)
    if checkpoint and checkpoint["at"] > last_success:
        result.update(stage="attention", reason="Facebook asked to log in or verify this account.")
        return result
    # Pages post as the Page; a session that showed the personal profile
    # means the browser switched away from the Page.
    identity = next((entry["identity"] for entry in reversed(entries) if entry.get("identity")), None)
    if identity == "personal":
        result.update(stage="attention",
                      reason="Facebook was showing the personal profile instead of the Page. Switch back to the Page in the browser.")
        return result
    recent = entries[-FAILURE_STREAK:]
    if len(recent) == FAILURE_STREAK and all(entry["status"] in FAILURE_STATUSES for entry in recent):
        result.update(stage="attention", reason=f"The last {FAILURE_STREAK} sessions failed before posting.")
        return result

    pushback = next((entry for entry in reversed(entries)
                     if any(marker in entry.get("error", "").lower() for marker in PUSHBACK_MARKERS)), None)
    if pushback:
        until = _parse(pushback["at"]) + timedelta(hours=PUSHBACK_REST_HOURS)
        if until > now:
            result.update(stage="resting", until=until.isoformat(),
                          reason="Facebook refused a recent action, so this account rests for a few days.")
            return result
    if limit and posts_today >= limit:
        result.update(stage="resting", until=tomorrow.isoformat(),
                      reason=f"Today's suggested limit of {limit} posts is reached.")
    return result


def _profile_configs() -> dict[str, dict]:
    configs = {}
    for path in PROFILES_DIR.glob("*/config.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(config, dict) and config.get("id"):
            configs[config["id"]] = config
    return configs


def all_health(queue: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    history = sync_history(queue, now)
    return {profile_id: compute(config, list((history.get(profile_id) or {}).values()), now)
            for profile_id, config in _profile_configs().items()}
