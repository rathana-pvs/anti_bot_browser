"""When each waiting queue item is expected to start, and what it is waiting for.

This walks the same gates as the dispatcher (ordered_due_executions and the batch iteration
order) but looks ahead instead of answering "now or not": the scheduled start, an account
travelling or resting after a proxy change, its waking hours, and the rest between its posts.
It is an estimate for people. The dispatcher alone decides when an item starts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.services import account_hold, active_hours, posting_plan
from backend.services.network_recovery import retry_admitted
from backend.services.profile_pipeline import batch_iteration_availability, batch_timing, execution_start_ms


def _iso(ms: float | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None


def _zone_name(config: dict) -> str | None:
    zone = active_hours.account_zone(config)
    return getattr(zone, "key", None)


def explain(queue: dict | None, now_ms: float, busy: bool = False) -> dict:
    """{execution_id: {expected_start_at, wait_reason, account_timezone, awake_hours}} for waiting items.

    `busy` says whether the scheduler has no free slot, so an item that is due waits for one.
    Reasons: scheduled, planned, replanning, active_hours, active_hours_limit, travelling, on_hold, waiting_for_visit,
    previous_post, rest_between_posts, network_recovery, waiting_for_slot, starting, prepared, paused.
    """
    configs: dict = {}
    loaded: list = []

    def history():
        if not loaded:
            from backend.services.account_health import _load_history
            loaded.append(_load_history())
        return loaded[0]

    result = {}
    for batch in (queue or {}).get("daily_batches", []):
        batch_start_ms, ranks, gap_ms = batch_timing(batch)
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                status = execution.get("status")
                if status not in ("pending", "ready"):
                    continue
                config = active_hours._profile_config(execution.get("profile_id", ""), configs)
                hours = active_hours.waking_hours(config)
                info = {"account_timezone": _zone_name(config),
                        "awake_hours": [hours[0], (hours[0] + hours[1]) % 24] if hours else None}
                result[execution["execution_id"]] = info | _one(batch, post, execution, config, now_ms, busy,
                                                                 batch_start_ms, ranks, gap_ms, history, queue)
    return result


def _one(batch, post, execution, config, now_ms, busy, batch_start_ms, ranks, gap_ms, history, queue=None) -> dict:
    if batch.get("paused"):
        return {"expected_start_at": None, "wait_reason": "paused"}
    if execution.get("status") == "ready":
        return {"expected_start_at": _iso(now_ms), "wait_reason": "prepared"}
    if not retry_admitted(execution, now_ms / 1000):
        return {"expected_start_at": None, "wait_reason": "network_recovery"}

    auto = posting_plan.is_auto(batch) and post.get("type") != "warming"
    due_ms = execution_start_ms(batch, execution, batch_start_ms, ranks, gap_ms)
    at = max(now_ms, due_ms)
    reason = ("planned" if auto else "scheduled") if due_ms > now_ms else None

    # An account on hold waits for a person; one travelling waits until it arrives.
    if account_hold.on_hold(config):
        return {"expected_start_at": None, "wait_reason": "on_hold"}
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    until = account_hold.travelling_until(config, now)
    if until and until.timestamp() * 1000 > at:
        at, reason = until.timestamp() * 1000, "travelling"
    if not account_hold.admits(execution, post, config, at, history) and not account_hold.is_paused(config, now):
        reason = "waiting_for_visit"

    if active_hours.applies(execution, batch, config, post) and (auto or at - due_ms < active_hours.MAX_HOLD_MS) \
            and not active_hours.within_active_hours(config, at):
        wake = active_hours.next_awake_ms(config, at)
        limit = float("inf") if auto else due_ms + active_hours.MAX_HOLD_MS
        at, reason = (wake, "active_hours") if wake <= limit else (limit, "active_hours_limit")
    # A planned item that is due but too close to another post, or over today's limit, gets a new time.
    if auto and at <= now_ms and not posting_plan.admits(execution, queue, config, now_ms):
        return {"expected_start_at": None, "wait_reason": "replanning"}

    order = batch_iteration_availability(batch, now_ms, target_execution=execution)
    if not order["allowed"]:
        if order.get("reason") == "batch_iteration_waiting_for_profiles":
            return {"expected_start_at": _iso(at), "wait_reason": "previous_post"}
        ready_at = order.get("ready_at_ms") or 0
        if ready_at > at:
            at, reason = ready_at, "rest_between_posts"

    if reason is None:
        reason = "waiting_for_slot" if busy else "starting"
    return {"expected_start_at": _iso(at), "wait_reason": reason}
