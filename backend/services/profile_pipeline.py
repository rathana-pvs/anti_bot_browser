import random
import time
from datetime import datetime

def shuffled_profile_order(profile_ids: list, previous_last_profile: str | None = None, rand_fn=random.random) -> list:
    order = list(dict.fromkeys(profile_ids or []))
    for i in range(len(order) - 1, 0, -1):
        swap_idx = int(rand_fn() * (i + 1))
        order[i], order[swap_idx] = order[swap_idx], order[i]

    if len(order) > 1 and previous_last_profile and order[0] == previous_last_profile:
        swap_idx = 1 + int(rand_fn() * (len(order) - 1))
        order[0], order[swap_idx] = order[swap_idx], order[0]

    return order

def parse_iso_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except Exception:
        return 0.0

def batch_iteration_availability(
    batch: dict | None,
    now_ms: float | None = None,
    target_execution: dict | None = None,
) -> dict:
    """Gate each profile's post order and delay before preparation or publishing."""
    if now_ms is None:
        now_ms = time.time() * 1000
    batch = batch or {}
    schedule_window = batch.get("schedule_window") or {}
    try:
        delay_seconds = max(0, min(3600, int(schedule_window.get("batch_iteration_delay_seconds", 0))))
    except (TypeError, ValueError):
        delay_seconds = 0

    posts = batch.get("posts", [])
    target_post_index = None
    target_execution_id = (target_execution or {}).get("execution_id")
    for post_index, post in enumerate(posts):
        if any(
            execution is target_execution
            or (
                target_execution_id
                and execution.get("execution_id") == target_execution_id
            )
            for execution in post.get("executions", [])
        ):
            target_post_index = post_index
            break

    if target_post_index in (None, 0):
        return {"allowed": True, "reason": "batch_iteration_ready", "retry_after_ms": 0}

    profile_id = (target_execution or {}).get("profile_id")
    previous_executions = [
        execution
        for earlier_post in posts[:target_post_index]
        for execution in earlier_post.get("executions", [])
        if execution.get("profile_id") == profile_id
    ]
    if any(
        execution.get("status") in ("pending", "ready", "running", "preparing", "uncertain", "needs_review")
        for execution in previous_executions
    ):
        return {
            "allowed": False,
            "reason": "batch_iteration_waiting_for_profiles",
            "retry_after_ms": None,
        }

    # Terminal preparation failures/skips also finish an iteration. Do not use
    # another account's completion time, or a preparation time for unfinished work.
    latest_end_ms = max(
        (parse_iso_time(execution.get("ended_at")) for execution in previous_executions),
        default=0.0,
    )

    ready_at_ms = latest_end_ms + delay_seconds * 1000
    if latest_end_ms and now_ms < ready_at_ms:
        return {
            "allowed": False,
            "reason": "batch_iteration_delay",
            "retry_after_ms": max(1, int(ready_at_ms - now_ms)),
            "ready_at_ms": ready_at_ms,
        }
    return {"allowed": True, "reason": "batch_iteration_ready", "retry_after_ms": 0}

def active_batch_id(queue: dict | None) -> str | None:
    """Return the oldest started batch that still owns unfinished work."""
    candidates = []
    unfinished_statuses = {"pending", "ready", "running", "preparing", "uncertain", "needs_review"}
    for batch_index, batch in enumerate((queue or {}).get("daily_batches", [])):
        executions = [
            execution
            for post in batch.get("posts", [])
            for execution in post.get("executions", [])
        ]
        if not executions or not any(execution.get("status") in unfinished_statuses for execution in executions):
            continue
        started_times = [
            parse_iso_time(execution.get("started_at"))
            for execution in executions
            if parse_iso_time(execution.get("started_at")) > 0
        ]
        if not started_times:
            continue
        candidates.append((
            min(started_times),
            parse_iso_time(batch.get("created_at")),
            -batch_index,
            batch.get("batch_id"),
        ))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]

def batch_has_review_hold(batch: dict | None) -> bool:
    return any(
        execution.get("status") in ("uncertain", "needs_review")
        for post in (batch or {}).get("posts", [])
        for execution in post.get("executions", [])
    )

def ordered_due_executions(queue: dict | None, now_ms: float | None = None) -> list:
    if now_ms is None:
        now_ms = time.time() * 1000

    due = []
    for batch_idx, batch in enumerate((queue or {}).get("daily_batches", [])):
        profile_order = batch.get("profile_execution_order") or batch.get("target_profiles") or []
        profile_ranks = {pid: idx for idx, pid in enumerate(profile_order)}
        all_batch_executions = [
            execution
            for post in batch.get("posts", [])
            for execution in post.get("executions", [])
        ]
        batch_start_ms = min(
            (value for value in (parse_iso_time(item.get("scheduled_at")) for item in all_batch_executions) if value > 0),
            default=0.0,
        )
        try:
            profile_stagger_ms = max(0, int(batch.get("schedule_window", {}).get("profile_stagger_seconds", 0))) * 1000
        except (TypeError, ValueError):
            profile_stagger_ms = 0

        for post_idx, post in enumerate(batch.get("posts", [])):
            for execution in post.get("executions", []):
                if execution.get("status") not in ("pending", "ready"):
                    continue
                profile_rank = profile_ranks.get(execution.get("profile_id"), 0)
                sched_time = batch_start_ms + profile_rank * profile_stagger_ms
                if sched_time > now_ms:
                    continue

                due.append({
                    "execution": execution,
                    "batchIndex": batch_idx,
                    "batchCreatedAtMs": parse_iso_time(batch.get("created_at")),
                    "batchStartMs": batch_start_ms,
                    "postIndex": post_idx,
                    "profileRank": profile_ranks.get(execution.get("profile_id"), float("inf")),
                    "iterationRank": execution.get("batch_iteration_index", float("inf")),
                    "scheduledAtMs": sched_time,
                })

    due.sort(key=lambda item: (
        item["batchStartMs"],
        item["batchCreatedAtMs"],
        -item["batchIndex"],
        item["iterationRank"],
        item["postIndex"],
        item["profileRank"],
        item["scheduledAtMs"],
    ))
    return [item["execution"] for item in due]

def count_buffered_preparations(queue: dict | None, except_execution_id: str | None = None) -> int:
    count = 0
    for batch in (queue or {}).get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.get("execution_id") == except_execution_id:
                    continue
                if execution.get("status") in ("preparing", "ready"):
                    count += 1
    return count

def has_buffered_preparation(queue: dict | None, except_execution_id: str | None = None) -> bool:
    return count_buffered_preparations(queue, except_execution_id) > 0
