"""Automatic warming: short read-only visits that keep accounts active.

One global switch. When it is on, `tick` runs once a minute and starts at most
one visit, for the account that needs it most, and only with spare capacity:
posts due soon keep their slots, a busy machine starts nothing, and a running
visit gives way to a post (see `yield_for_post`).

Visits are ordinary `warming` queue items in one system batch per day, kept at
the end of the queue, so the dispatcher, slots, evidence and account history
treat them like any other warm-up.
"""

import json
import random
import time
from datetime import datetime, timezone

from backend.config import DATA_DIR, PROFILES_DIR
from backend.services import account_health, active_hours
from backend.services.profile_service import atomic_write_json

AUTO_BATCH_PREFIX = "auto_warming_"
YIELD_FILE = DATA_DIR / "resources" / "yield.json"

STAGE_WEIGHT = {"new": 3.0, "warming_up": 2.0, "ready": 1.0, "resting": 0.5}
DAILY_VISIT_CAP = {"new": 4, "warming_up": 3, "ready": 2, "resting": 1}
VISIT_SECONDS = {"new": (180, 360), "warming_up": (150, 300), "ready": (90, 240), "resting": (60, 120)}

MINUTE_MS = 60_000
GAP_AFTER_VISIT_MS = 90 * MINUTE_MS
GAP_AFTER_POST_MS = 30 * MINUTE_MS
POST_RESERVE_MS = 15 * MINUTE_MS
WARMUP_WINDOW_MS = (20 * MINUTE_MS, 60 * MINUTE_MS)
UNSTARTED_EXPIRY_MS = 30 * MINUTE_MS

ACTIVE_STATUSES = {"running", "preparing"}
WAITING_STATUSES = {"pending", "ready"}


def enabled() -> bool:
    from backend.services.resource_service import settings_manager
    return settings_manager.auto_warming


def is_auto_batch(batch: dict) -> bool:
    return str(batch.get("batch_id", "")).startswith(AUTO_BATCH_PREFIX)


def _ms(value) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp() * 1000


def _executions(queue: dict):
    for batch in queue.get("daily_batches") or []:
        for post in batch.get("posts") or []:
            for execution in post.get("executions") or []:
                yield batch, post, execution


def persona_day(config: dict, now_ms: float) -> dict:
    """This person's habits for today: a rest day, and how often they open Facebook."""
    seed = config.get("behavior_seed") if isinstance(config.get("behavior_seed"), int) else hash(config.get("id", ""))
    day = datetime.fromtimestamp(now_ms / 1000, tz=active_hours.account_zone(config)).date().isoformat()
    habits = random.Random(f"auto-warming:{seed}")
    rest_rate, frequency = habits.uniform(0.08, 0.25), habits.uniform(0.7, 1.3)
    return {"rest_day": random.Random(f"auto-warming:{seed}:{day}").random() < rest_rate, "frequency": frequency}


def _local_day_start_ms(config: dict, now_ms: float) -> float:
    local = datetime.fromtimestamp(now_ms / 1000, tz=active_hours.account_zone(config))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000


def _needs_restart_visit(health: dict, pid: str) -> bool:
    restart = (health.get(pid) or {}).get("proxy_restart")
    return bool(restart) and not restart.get("visited")


def candidates(queue: dict, configs: dict, health: dict, history: dict, now_ms: float) -> list[dict]:
    """Accounts that may get a visit now, best first."""
    busy, next_post = set(), {}
    for batch, post, execution in _executions(queue):
        pid, status = execution.get("profile_id"), execution.get("status")
        if status in ACTIVE_STATUSES or (status in WAITING_STATUSES and is_auto_batch(batch)):
            busy.add(pid)
        elif status in WAITING_STATUSES:
            due = _ms(execution.get("scheduled_at"))
            next_post[pid] = min(next_post.get(pid, due), due)
    ranked = []
    for pid, config in configs.items():
        stage = (health.get(pid) or {}).get("stage")
        if stage not in STAGE_WEIGHT or pid in busy:
            continue
        if (config.get("runtime") or {}).get("kind") == "android_user" or (config.get("automation") or {}).get("enabled") is False:
            continue
        if not active_hours.persona_hours_admit(config, now_ms):
            continue
        habits = persona_day(config, now_ms)
        if habits["rest_day"]:
            continue
        entries = list((history.get(pid) or {}).values())
        day_start = _local_day_start_ms(config, now_ms)
        visits = [_ms(e["at"]) for e in entries if e.get("kind") == "warming" and e.get("status") == "completed"]
        posts = [_ms(e["at"]) for e in entries if e.get("kind") != "warming" and e.get("status") == "published"]
        visits_today = sum(1 for at in visits if at >= day_start)
        if visits_today >= DAILY_VISIT_CAP[stage]:
            continue
        if visits and now_ms - max(visits) < GAP_AFTER_VISIT_MS:
            continue
        if posts and now_ms - max(posts) < GAP_AFTER_POST_MS:
            continue
        until_post = next_post.get(pid, float("inf")) - now_ms
        # After a proxy change its posts wait for this visit, so they never block it.
        restart_visit = _needs_restart_visit(health, pid)
        if until_post < POST_RESERVE_MS and not restart_visit:
            continue
        last_seen = max([_ms(e["at"]) for e in entries] or [_ms(config.get("created_at")) or now_ms])
        quiet_hours = max(0.0, (now_ms - last_seen) / 3_600_000)
        posts_today = sum(1 for at in posts if at >= day_start)
        score = STAGE_WEIGHT[stage] * habits["frequency"] * (1 + min(quiet_hours, 12) / 4) / (1 + visits_today + posts_today / 2)
        reason = f"{stage.replace('_', ' ').capitalize()} account, quiet for {quiet_hours:.0f} h"
        if WARMUP_WINDOW_MS[0] <= until_post <= WARMUP_WINDOW_MS[1]:
            score += 2.0
            reason = f"Warm-up before its post in {until_post / MINUTE_MS:.0f} min"
        if restart_visit:
            # Its posts are waiting for this visit, so it goes first.
            score += 10.0
            reason = "First visit after a proxy change"
        ranked.append({"profile_id": pid, "stage": stage, "score": round(score, 3), "reason": reason})
    ranked.sort(key=lambda item: -item["score"])
    return ranked


def spare_capacity(queue: dict, leases: list, config: dict, now_ms: float, held: set | frozenset = frozenset()) -> bool:
    """A slot is free after keeping one for each post due in the next 15 minutes.

    Posts on `held` accounts wait for a visit, so they keep no slot.
    """
    from backend.services.queue_scheduler import scheduler_snapshot
    snapshot = scheduler_snapshot(queue, leases, config, now_ms)
    due_soon = sum(1 for batch, _post, execution in _executions(queue)
                   if not is_auto_batch(batch) and execution.get("status") in WAITING_STATUSES
                   and execution.get("profile_id") not in held
                   and _ms(execution.get("scheduled_at")) <= now_ms + POST_RESERVE_MS)
    return (snapshot["available"]["preparers"] > 0
            and snapshot["available"]["total"] - due_soon > 0)


def _visit_options(stage: str, rng: random.Random) -> dict:
    low, high = VISIT_SECONDS[stage]
    return {"auto": True, "max_seconds": rng.randint(low, high), "surface": "random"}


def add_visit(queue: dict, choice: dict, now: datetime, rng: random.Random) -> dict:
    """Append one visit to today's Auto warming batch and return its execution."""
    day = now.astimezone().date().isoformat()
    batch_id = f"{AUTO_BATCH_PREFIX}{day}"
    batches = queue.setdefault("daily_batches", [])
    batch = next((item for item in batches if item.get("batch_id") == batch_id), None)
    if batch is None:
        batch = {
            "batch_id": batch_id, "name": f"Auto warming · {day}", "created_at": now.isoformat(),
            "target_profiles": [], "profile_execution_order": [], "posting_order_per_profile": {},
            "schedule_window": {"start_now": True, "profile_stagger_seconds": 0, "batch_iteration_delay_seconds": 0,
                                "session_preparation_mode": "off", "execution_flow": "resource_bounded"},
            "posts": [],
        }
        # Kept last so it never changes the order of the user's own batches.
        batches.append(batch)
    pid = choice["profile_id"]
    if pid not in batch["target_profiles"]:
        batch["target_profiles"].append(pid)
        batch["profile_execution_order"].append(pid)
    index = len(batch["posts"])
    stamp = now.isoformat()
    execution = {
        "execution_id": f"exec_auto_{int(now.timestamp() * 1000)}_{index}",
        "profile_id": pid, "scheduled_at": stamp, "batch_iteration_index": index,
        "spun_caption": "", "status": "pending", "stage": "pending",
        "stage_history": [{"stage": "pending", "timestamp": stamp}],
        "preparation_mode": "off", "preparation_status": "not_requested",
        "retry_count": 0, "error": None, "published_at": None, "logs": [],
        "auto_warming": {"reason": choice["reason"], "score": choice["score"], "stage": choice["stage"]},
    }
    batch["posts"].append({
        "post_id": f"v_{index + 1}", "type": "warming", "media_file": "", "base_caption": "",
        "first_comment": None, "scrolls": 4, "warming_options": _visit_options(choice["stage"], rng),
        "ai_spin": False, "executions": [execution],
    })
    return execution


def expire_unstarted(queue: dict, now_ms: float, *, all_pending: bool = False) -> int:
    """Cancel visits that waited too long (or every waiting one when switched off)."""
    expired = 0
    for batch, _post, execution in _executions(queue):
        if not is_auto_batch(batch) or execution.get("status") != "pending":
            continue
        if all_pending or now_ms - _ms(execution.get("scheduled_at")) >= UNSTARTED_EXPIRY_MS:
            execution.update(status="cancelled", stage="cancelled",
                             error="Auto warming switched off" if all_pending else "No spare capacity in time; visit skipped")
            expired += 1
    return expired


def _profile_configs() -> dict:
    configs = {}
    for path in PROFILES_DIR.glob("*/config.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(config, dict) and config.get("id"):
            configs[config["id"]] = config
    return configs


_last_choice: dict | None = None


def plan_once(now: datetime | None = None) -> dict | None:
    """Start at most one visit now; returns it, or None."""
    global _last_choice
    from backend.services.automation_service import active_in_memory_scheduler_leases
    from backend.services.docker_service import get_scheduler_config
    from backend.services.queue_service import load_posting_queue, save_posting_queue

    now = now or datetime.now(timezone.utc)
    now_ms = now.timestamp() * 1000
    queue = load_posting_queue()
    if not enabled():
        if expire_unstarted(queue, now_ms, all_pending=True):
            save_posting_queue(queue)
        return None
    changed = expire_unstarted(queue, now_ms) > 0
    # Kept current every tick: posts held after a proxy change wait for a visit to show up here.
    history = account_health.sync_history(queue, now)
    # One visit starts at a time; running ones may continue side by side.
    waiting = any(is_auto_batch(batch) and execution.get("status") in {"pending", "ready", "preparing"}
                  for batch, _post, execution in _executions(queue))
    choice = None
    if not waiting and _machine_calm():
        configs = _profile_configs()
        health = {pid: account_health.compute(config, account_health.entries_for(history, pid), now)
                  for pid, config in configs.items()}
        held = {pid for pid in health if _needs_restart_visit(health, pid)}
        if spare_capacity(queue, active_in_memory_scheduler_leases(), get_scheduler_config(), now_ms, held):
            ranked = candidates(queue, configs, health, history, now_ms)
        else:
            ranked = []
        if ranked:
            choice = ranked[0]
            add_visit(queue, choice, now, random.Random())
            changed = True
            _last_choice = {**choice, "at": now.isoformat()}
    if changed:
        save_posting_queue(queue)
    return choice


def _machine_calm() -> bool:
    from backend.services import adaptive_runtime
    if not adaptive_runtime.adaptive_enabled():
        return True
    return adaptive_runtime.current_decision().state == "calm"


async def tick():
    try:
        choice = plan_once()
    except Exception as error:
        print(f"Auto warming error: {error}")
        return
    if choice:
        from backend.services.automation_service import dispatch_pending_queue
        await dispatch_pending_queue()


# Giving way to posts ------------------------------------------------------

def read_yields() -> dict:
    try:
        value = json.loads(YIELD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = value.get("profiles") if isinstance(value, dict) else None
    return profiles if isinstance(profiles, dict) else {}


def _write_yields(profiles: dict) -> None:
    try:
        atomic_write_json(YIELD_FILE, {"updated_at": time.time(), "profiles": profiles})
    except OSError:
        pass


def request_yield(profile_id: str, reason: str, handoff_to: str | None = None) -> None:
    """Ask the running visit on this account to end at its next safe point."""
    profiles = read_yields()
    profiles[profile_id] = {"reason": reason, "handoff_to": handoff_to, "at": time.time()}
    _write_yields(profiles)


def clear_yield(profile_id: str) -> dict | None:
    profiles = read_yields()
    request = profiles.pop(profile_id, None)
    if request is not None:
        _write_yields(profiles)
    return request


def running_visits(queue: dict) -> list[dict]:
    return [execution for batch, _post, execution in _executions(queue)
            if is_auto_batch(batch) and execution.get("status") in ACTIVE_STATUSES]


def yield_for_post(queue: dict, execution: dict) -> bool:
    """Make room for a post that could not start; returns whether a visit was asked to end.

    A visit on the post's own account ends and hands its open browser to the
    post. Otherwise the running visit with the lowest score gives up its slot.
    """
    visits = running_visits(queue)
    if not visits:
        return False
    pid = execution.get("profile_id")
    own = next((visit for visit in visits if visit.get("profile_id") == pid), None)
    if own is not None:
        if pid not in read_yields():
            request_yield(pid, "own_post", handoff_to=execution.get("execution_id"))
        return True
    if any(visit.get("profile_id") in read_yields() for visit in visits):
        return True  # one is already ending
    lowest = min(visits, key=lambda visit: (visit.get("auto_warming") or {}).get("score", 0))
    request_yield(lowest["profile_id"], "post_needs_slot")
    return True


def summary() -> dict:
    from backend.services.queue_service import load_posting_queue
    queue = load_posting_queue()
    today = datetime.now().astimezone().date().isoformat()
    visits = [execution for batch, _post, execution in _executions(queue)
              if batch.get("batch_id") == f"{AUTO_BATCH_PREFIX}{today}"]
    return {
        "enabled": enabled(),
        "visits_today": sum(1 for execution in visits if execution.get("status") == "completed"),
        "running": [execution.get("profile_id") for execution in visits if execution.get("status") in ACTIVE_STATUSES],
        "last_choice": _last_choice,
    }
