"""Check now: one short read-only check after the user fixed an account.

The check is a warming queue item in check mode: it opens the account's own
profile, verifies the session (login, checkpoint, Page or personal profile),
captures a screenshot and stops. A pass is recorded in the account history
like any finished visit, so the account leaves Attention; a failure keeps it
there with fresh evidence. Checks run only when the user asks for one.
"""

from datetime import datetime

CHECK_BATCH_PREFIX = "account_check_"
CHECK_OPTIONS = {"check": True, "surface": "profile", "max_seconds": 60}
OPEN_STATUSES = {"pending", "ready", "preparing", "running"}


def is_check_batch(batch: dict) -> bool:
    return str(batch.get("batch_id", "")).startswith(CHECK_BATCH_PREFIX)


def open_check(queue: dict, profile_id: str) -> dict | None:
    """A check for this account that has not finished yet."""
    for batch in queue.get("daily_batches") or []:
        if not is_check_batch(batch):
            continue
        for post in batch.get("posts") or []:
            for execution in post.get("executions") or []:
                if execution.get("profile_id") == profile_id and execution.get("status") in OPEN_STATUSES:
                    return execution
    return None


def add_check(queue: dict, profile_id: str, now: datetime) -> dict:
    """Append one check to today's check batch, which starts right away."""
    day = now.astimezone().date().isoformat()
    batch_id = f"{CHECK_BATCH_PREFIX}{day}"
    batches = queue.setdefault("daily_batches", [])
    batch = next((item for item in batches if item.get("batch_id") == batch_id), None)
    if batch is None:
        batch = {
            "batch_id": batch_id, "name": f"Account check · {day}", "created_at": now.isoformat(),
            "target_profiles": [], "profile_execution_order": [], "posting_order_per_profile": {},
            "schedule_window": {"start_now": True, "profile_stagger_seconds": 0, "batch_iteration_delay_seconds": 0,
                                "session_preparation_mode": "off", "execution_flow": "resource_bounded"},
            "posts": [],
        }
        batches.append(batch)
    if profile_id not in batch["target_profiles"]:
        batch["target_profiles"].append(profile_id)
        batch["profile_execution_order"].append(profile_id)
    index = len(batch["posts"])
    stamp = now.isoformat()
    execution = {
        "execution_id": f"exec_check_{int(now.timestamp() * 1000)}_{index}",
        "profile_id": profile_id, "scheduled_at": stamp, "batch_iteration_index": index,
        "spun_caption": "", "status": "pending", "stage": "pending",
        "stage_history": [{"stage": "pending", "timestamp": stamp, "reason": "account_check_requested"}],
        "preparation_mode": "off", "preparation_status": "not_requested",
        "retry_count": 0, "error": None, "published_at": None, "logs": [],
        "account_check": True,
    }
    batch["posts"].append({
        "post_id": f"check_{index + 1}", "type": "warming", "media_file": "", "base_caption": "",
        "first_comment": None, "scrolls": 1, "warming_options": dict(CHECK_OPTIONS),
        "ai_spin": False, "executions": [execution],
    })
    return execution
