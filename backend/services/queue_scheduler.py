import random
import time
from datetime import datetime, timezone

DEFAULT_SCHEDULER_CONFIG = {
    # Preparing and posting share one pool of job slots.
    "max_publishers": 2,
    "max_preparers": 2,
    "max_total_automation_tasks": 2,
    "max_active_profile_containers": 2,
    "lease_ttl_ms": 60000,
    "heartbeat_interval_ms": 15000,
}

MINUTE_MS = 60000

def worker_silence_timeout_ms(stage: str, lease_ttl_ms: int = DEFAULT_SCHEDULER_CONFIG["lease_ttl_ms"]) -> int:
    stage_limits = {
        "preparing": 6 * MINUTE_MS,
        "warming": 8 * MINUTE_MS,
        "composing": 12 * MINUTE_MS,
        "ready_to_publish": 6 * MINUTE_MS,
        "publish_clicked": 12 * MINUTE_MS,
        "verifying": 12 * MINUTE_MS,
        "commenting": 10 * MINUTE_MS,
    }
    return max(lease_ttl_ms * 3, stage_limits.get(stage, 6 * MINUTE_MS))

RUNNABLE_STATUSES = {
    "pending",
    "ready",
    "failed",
    "failed_before_publish",
    "skipped_stopped",
    "stopped",
}

def as_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except Exception:
        return 0.0

def compute_execution_schedule(
    now_ms: float,
    is_start_now: bool,
    slot_index: int,
    profile_index: int,
    stagger_ms: float,
    post_slot_ms: float,
    window_start_ms: float,
    jitter_ms: float = 0.0,
) -> float:
    if is_start_now:
        return now_ms + profile_index * stagger_ms
    # Scheduled time gates the batch itself. Once that gate opens, the
    # completion-based iteration delay serializes each profile's posts in order.
    return max(now_ms + 60000, window_start_ms + profile_index * stagger_ms)

SPREAD_CHOICES_MINUTES = (0, 15, 30, 60, 120, 240)
# With no spread, accounts still start a varying 45–120 s apart.
QUICK_GAP_SECONDS = (45, 120)


def spread_offsets_ms(count: int, spread_minutes: int, rand=random.random) -> list[float]:
    """Random start offsets, in order, for `count` accounts inside `spread_minutes`.

    Each account gets its own slice of the window and a random moment inside it, so starts never
    line up like a clock and two accounts are always at least half a slice apart (1 min at most).
    With no spread, consecutive starts are a random 45–120 s apart.
    """
    if count <= 0:
        return []
    if spread_minutes <= 0:
        low, high = QUICK_GAP_SECONDS
        offsets, at = [0.0], 0.0
        for _ in range(count - 1):
            at += (low + rand() * (high - low)) * 1000
            offsets.append(at)
        return offsets
    window_ms = spread_minutes * 60_000
    slot_ms = window_ms / count
    gap_ms = min(60_000, slot_ms / 2)
    return [index * slot_ms + rand() * (slot_ms - gap_ms) for index in range(count)]


def is_lease_active(lease: dict | None, now_ms: float | None = None) -> bool:
    if not lease or not isinstance(lease, dict):
        return False
    if now_ms is None:
        now_ms = time.time() * 1000
    return bool(
        lease.get("lease_id")
        and lease.get("kind") in ("publisher", "preparer")
        and as_time(lease.get("expires_at")) > now_ms
    )

def list_queue_leases(queue: dict | None, now_ms: float | None = None) -> list:
    if now_ms is None:
        now_ms = time.time() * 1000
    leases = []
    for batch in (queue or {}).get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.get("status") in ("running", "preparing") and is_lease_active(execution.get("scheduler_lease"), now_ms):
                    l_copy = dict(execution["scheduler_lease"])
                    l_copy["execution_id"] = execution.get("execution_id")
                    l_copy['workload'] = 'live' if post.get('type') == 'live' else 'post'
                    leases.append(l_copy)
    return leases

def scheduler_snapshot(
    queue: dict | None,
    additional_leases: list | None = None,
    config: dict | None = None,
    now_ms: float | None = None,
) -> dict:
    if config is None:
        config = DEFAULT_SCHEDULER_CONFIG
    if now_ms is None:
        now_ms = time.time() * 1000

    unique = {}
    all_leases = list_queue_leases(queue, now_ms) + (additional_leases or [])
    for lease in all_leases:
        if is_lease_active(lease, now_ms):
            unique[lease["lease_id"]] = {**unique.get(lease['lease_id'], {}), **lease}

    leases = list(unique.values())
    publishers = len([l for l in leases if l.get("kind") == "publisher"])
    preparers = len([l for l in leases if l.get("kind") == "preparer"])
    live = sum(l.get('kind') == 'publisher' and l.get('workload') == 'live' for l in leases)
    posts = publishers - live
    # One pool: any free job slot can prepare or post. Post and Live caps
    # (Custom mode) still limit how many accounts are in the posting step.
    free = max(0, config["max_total_automation_tasks"] - len(leases))

    return {
        "config": dict(config),
        "active": {"publishers": publishers, "posts": posts, "live": live, "preparers": preparers, "total": len(leases)},
        "available": {
            "publishers": min(free, max(0, config["max_publishers"] - publishers)),
            'posts': min(free, max(0, config.get('max_post_publishers', config['max_publishers']) - posts)),
            'live': min(free, max(0, config.get('max_live_publishers', config['max_publishers']) - live)),
            "preparers": min(free, max(0, config["max_preparers"] - preparers)),
            "total": free,
        },
        "leases": leases,
    }

def can_acquire_scheduler_slot(
    queue: dict | None,
    additional_leases: list | None,
    kind: str,
    profile_id: str,
    config: dict | None = None,
    now_ms: float | None = None,
    workload: str = 'post',
) -> dict:
    if kind not in ("publisher", "preparer"):
        return {"allowed": False, "reason": f"invalid_slot_kind:{kind}"}
    if config is None:
        config = DEFAULT_SCHEDULER_CONFIG
    if now_ms is None:
        now_ms = time.time() * 1000

    snapshot = scheduler_snapshot(queue, additional_leases, config, now_ms)
    if any(l.get("profile_id") == profile_id for l in snapshot["leases"]):
        return {"allowed": False, "reason": "profile_busy", "snapshot": snapshot}
    # Preparation and posting draw from the same job slots.
    if snapshot["active"]["total"] >= config["max_total_automation_tasks"]:
        return {"allowed": False, "reason": "total_capacity_reached", "snapshot": snapshot}
    workload_key = 'live' if workload == 'live' else 'posts'
    limit_key = 'max_live_publishers' if workload == 'live' else 'max_post_publishers'
    if kind == 'publisher' and snapshot['active'][workload_key] >= config.get(limit_key, config['max_publishers']):
        return {'allowed': False, 'reason': f'{workload_key}_capacity_reached', 'snapshot': snapshot}

    return {"allowed": True, "reason": "slot_available", "snapshot": snapshot}

def preparation_has_room(buffered_preparations: int, active_posts: int, config: dict | None = None) -> bool:
    """Whether one more post may start preparing.

    Accounts that are preparing or prepared are on their way to the posting
    step, so together with the posts already publishing they stay within the
    post cap. A prepared account then always has a posting slot to move into
    instead of idling until its browser is stopped.
    """
    if config is None:
        config = DEFAULT_SCHEDULER_CONFIG
    cap = config.get("max_post_publishers", config["max_publishers"])
    return buffered_preparations + active_posts < cap

def claim_execution_lease(
    queue: dict | None,
    execution: dict | None,
    lease_id: str,
    owner_id: str,
    kind: str = "publisher",
    config: dict | None = None,
    additional_leases: list | None = None,
    now_ms: float | None = None,
    workload: str = 'post',
) -> dict:
    if config is None:
        config = DEFAULT_SCHEDULER_CONFIG
    if now_ms is None:
        now_ms = time.time() * 1000

    if not execution or execution.get("status") not in RUNNABLE_STATUSES:
        status = execution.get("status") if execution else "missing"
        return {"claimed": False, "reason": f"execution_not_runnable:{status}"}

    availability = can_acquire_scheduler_slot(
        queue, additional_leases, kind, execution.get("profile_id"), config, now_ms, workload=workload
    )
    if not availability["allowed"]:
        return {"claimed": False, **availability}

    claimed_at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    expires_at = datetime.fromtimestamp((now_ms + config["lease_ttl_ms"]) / 1000, tz=timezone.utc).isoformat()

    execution["scheduler_lease"] = {
        "lease_id": lease_id,
        "owner_id": owner_id,
        "kind": kind,
        'workload': workload,
        "profile_id": execution.get("profile_id"),
        "claimed_at": claimed_at,
        "heartbeat_at": claimed_at,
        "expires_at": expires_at,
    }
    return {"claimed": True, "reason": "claimed", "lease": execution["scheduler_lease"]}

def refresh_execution_lease(
    execution: dict | None,
    lease_id: str,
    config: dict | None = None,
    now_ms: float | None = None,
) -> bool:
    if config is None:
        config = DEFAULT_SCHEDULER_CONFIG
    if now_ms is None:
        now_ms = time.time() * 1000

    lease = execution.get("scheduler_lease") if execution else None
    if not lease or lease.get("lease_id") != lease_id:
        return False

    lease["heartbeat_at"] = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    lease["expires_at"] = datetime.fromtimestamp((now_ms + config["lease_ttl_ms"]) / 1000, tz=timezone.utc).isoformat()
    return True

def release_execution_lease(execution: dict | None, lease_id: str) -> bool:
    lease = execution.get("scheduler_lease") if execution else None
    if not lease or lease.get("lease_id") != lease_id:
        return False
    execution["last_scheduler_lease"] = execution["scheduler_lease"]
    execution["scheduler_lease"] = None
    return True

def find_stale_running_executions(
    queue: dict | None,
    live_lease_ids: set | None = None,
    now_ms: float | None = None,
) -> list:
    if live_lease_ids is None:
        live_lease_ids = set()
    if now_ms is None:
        now_ms = time.time() * 1000

    stale = []
    for batch in (queue or {}).get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.get("status") not in ("running", "preparing"):
                    continue
                lease_id = execution.get("scheduler_lease", {}).get("lease_id") if execution.get("scheduler_lease") else None
                if lease_id and lease_id in live_lease_ids:
                    continue
                if not is_lease_active(execution.get("scheduler_lease"), now_ms):
                    stale.append(execution)
    return stale
