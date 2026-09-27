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

def ordered_due_executions(queue: dict | None, now_ms: float | None = None) -> list:
    if now_ms is None:
        now_ms = time.time() * 1000

    due = []
    for batch_idx, batch in enumerate((queue or {}).get("daily_batches", [])):
        profile_order = batch.get("profile_execution_order") or batch.get("target_profiles") or []
        profile_ranks = {pid: idx for idx, pid in enumerate(profile_order)}

        for post_idx, post in enumerate(batch.get("posts", [])):
            for execution in post.get("executions", []):
                if execution.get("status") not in ("pending", "ready"):
                    continue
                sched_time = parse_iso_time(execution.get("scheduled_at"))
                if sched_time > now_ms:
                    continue

                due.append({
                    "execution": execution,
                    "batchIndex": batch_idx,
                    "postIndex": post_idx,
                    "profileRank": profile_ranks.get(execution.get("profile_id"), float("inf")),
                    "scheduledAtMs": sched_time,
                })

    due.sort(key=lambda item: (
        item["batchIndex"],
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
