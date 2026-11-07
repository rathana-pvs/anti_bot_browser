"""Auto-planned posting: a batch scheduled with Follow active hours picks each account's time.

Each post gets a random moment inside the account's own waking day (in its proxy's timezone),
away from the first and last minutes of that day, a random 2–4 h from the account's other posts
in any batch, and on a day that still has room under the daily limit (5 by default). Later days
take the overflow.

Only auto-planned items are held by the gap and the limit. "Start now" and an exact scheduled
time start when asked; they still count as posts the plan keeps away from.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from backend.services import active_hours

# Never in the first or last stretch of the account's day: a random 30–60 min at each end.
EDGE_MINUTES = (30, 60)
PLAN_DAYS = 14
# A plan never lands in the next couple of minutes, so the form's preview still holds.
MIN_LEAD_MS = 2 * 60_000
# Started or finished posts that may be on Facebook. Failed-before-publish and skipped ones are not.
POSTED_STATUSES = {"published", "uncertain", "needs_review", "failed_after_publish", "running", "preparing", "ready"}


class NoRoom(ValueError):
    pass


def is_auto(batch: dict | None) -> bool:
    return ((batch or {}).get("schedule_window") or {}).get("timing") == "auto"


def settings() -> dict:
    from backend.services.resource_service import settings_manager
    return settings_manager.posting_plan


def _ms(value) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return 0.0


def _started_ms(execution: dict) -> float:
    for entry in reversed(execution.get("stage_history") or []):
        if entry.get("stage") in ("running", "preparing"):
            return _ms(entry.get("timestamp"))
    return 0.0


def account_posts(queue: dict | None, profile_id: str, exclude=None) -> dict:
    """{"posted": [ms], "planned": [ms]} for the account's posts in every batch. `exclude` is one
    execution id or several, left out (the items being planned again)."""
    excluded = {exclude} if isinstance(exclude, str) else set(exclude or ())
    posted, planned = [], []
    for batch in (queue or {}).get("daily_batches", []):
        for post in batch.get("posts", []):
            if post.get("type") == "warming":
                continue
            for execution in post.get("executions", []):
                if execution.get("profile_id") != profile_id or execution.get("execution_id") in excluded:
                    continue
                status = execution.get("status")
                if status in POSTED_STATUSES:
                    at = _ms(execution.get("published_at")) or _started_ms(execution) or _ms(execution.get("scheduled_at"))
                    if at:
                        posted.append(at)
                elif status == "pending":
                    at = _ms(execution.get("scheduled_at"))
                    if at:
                        planned.append(at)
    return {"posted": posted, "planned": planned}


def _hours(config: dict) -> tuple[int, int]:
    return active_hours.waking_hours(config) or active_hours.DEFAULT_HOURS


def account_day(config: dict, at_ms: float) -> tuple[float, float]:
    """(start, end) in ms of the account day holding `at_ms`, or the next day when it is asleep then."""
    start_hour, length = _hours(config)
    zone = active_hours.account_zone(config)
    local = datetime.fromtimestamp(at_ms / 1000, tz=zone)
    for offset in (-1, 0, 1):
        day = (local + timedelta(days=offset)).date()
        start = datetime(day.year, day.month, day.day, start_hour, tzinfo=zone).timestamp() * 1000
        if at_ms < start + length * 3600_000:
            return start, start + length * 3600_000
    raise AssertionError("unreachable")


def _free(lo: float, hi: float, blocked: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [(lo, hi)]
    for b_lo, b_hi in blocked:
        next_pieces = []
        for p_lo, p_hi in pieces:
            if b_hi <= p_lo or b_lo >= p_hi:
                next_pieces.append((p_lo, p_hi))
                continue
            if b_lo > p_lo:
                next_pieces.append((p_lo, b_lo))
            if b_hi < p_hi:
                next_pieces.append((b_hi, p_hi))
        pieces = next_pieces
    return [(a, b) for a, b in pieces if b > a]


def plan_time(queue: dict | None, profile_id: str, config: dict, now_ms: float, rand: random.Random | None = None,
              plan: dict | None = None, extra: list[float] | None = None, exclude=None) -> float:
    """A random time for the account's next auto post, keeping its gap and daily limit."""
    rand = rand or random.Random()
    plan = plan or settings()
    posts = account_posts(queue, profile_id, exclude)
    taken = posts["posted"] + posts["planned"] + list(extra or [])
    gap_ms = rand.uniform(plan["min_gap_hours"], plan["max_gap_hours"]) * 3600_000
    blocked = [(at - gap_ms, at + gap_ms) for at in taken]
    day_start, day_end = account_day(config, now_ms)
    for _ in range(PLAN_DAYS):
        if sum(day_start <= at < day_end for at in taken) < plan["max_posts_per_day"]:
            lo = max(day_start + rand.uniform(*EDGE_MINUTES) * 60_000, now_ms + MIN_LEAD_MS)
            hi = day_end - rand.uniform(*EDGE_MINUTES) * 60_000
            pieces = _free(lo, hi, blocked) if hi > lo else []
            total = sum(b - a for a, b in pieces)
            if total > 0:
                point = rand.uniform(0, total)
                for a, b in pieces:
                    if point <= b - a:
                        return a + point
                    point -= b - a
                return pieces[-1][1]
        day_start, day_end = account_day(config, day_end + 3600_000)
    raise NoRoom(f"No room to post within {PLAN_DAYS} days under the daily limit.")


def admits(execution: dict, queue: dict | None, config: dict, now_ms: float, plan: dict | None = None) -> bool:
    """Whether an auto-planned item may start now: the account is awake, no post of its own went out
    within the shortest gap, and today's limit has room. Other items are never held here."""
    plan = plan or settings()
    if not active_hours.within_active_hours(config, now_ms):
        return False
    posted = account_posts(queue, execution.get("profile_id", ""), execution.get("execution_id"))["posted"]
    if any(abs(now_ms - at) < plan["min_gap_hours"] * 3600_000 for at in posted):
        return False
    day_start, day_end = account_day(config, now_ms)
    return sum(day_start <= at < day_end for at in posted) < plan["max_posts_per_day"]


def replan_overdue(queue: dict, now_ms: float, grace_ms: float = 10 * 60_000) -> list[str]:
    """Moves auto items that are overdue but can't start (asleep, too close to a post, or the day
    is full) to a fresh time, so they keep the gap instead of all firing together later. Returns
    the ids moved; the caller saves the queue."""
    moved = []
    cache: dict = {}
    plan = settings()
    for batch in queue.get("daily_batches", []):
        if not is_auto(batch) or batch.get("paused"):
            continue
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.get("status") != "pending" or execution.get("cloud_attempt_id"):
                    continue
                due = _ms(execution.get("scheduled_at"))
                if not due or now_ms - due < grace_ms:
                    continue
                config = active_hours._profile_config(execution.get("profile_id", ""), cache)
                if admits(execution, queue, config, now_ms, plan):
                    continue
                try:
                    at = plan_time(queue, execution["profile_id"], config, now_ms, plan=plan, exclude=execution.get("execution_id"))
                except NoRoom:
                    continue
                execution["scheduled_at"] = datetime.fromtimestamp(at / 1000).astimezone().isoformat()
                history = execution.setdefault("plan_history", [])
                del history[:-9]
                history.append({"from": datetime.fromtimestamp(due / 1000).astimezone().isoformat(),
                                                                 "to": execution["scheduled_at"], "at": datetime.now().astimezone().isoformat()})
                moved.append(execution["execution_id"])
    return moved
