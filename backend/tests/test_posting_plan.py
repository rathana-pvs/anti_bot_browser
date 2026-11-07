"""Follow active hours batches plan each account's posts: inside its day, apart, under a daily limit."""
import json
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from automation.engine.behavior_profile import Persona
from backend.services import active_hours, posting_plan
from backend.services.profile_pipeline import ordered_due_executions

SEED = 8727273212897574946
ZONE = "America/Chicago"
START, LENGTH = Persona.from_seed(SEED).active_hours
PLAN = {"min_gap_hours": 2.0, "max_gap_hours": 4.0, "max_posts_per_day": 5}
HOUR = 3600_000


def local(hour, day=30, minute=0):
    return datetime(2026, 10, day, 0, minute, tzinfo=ZoneInfo(ZONE)).timestamp() * 1000 + hour * HOUR


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(active_hours, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(posting_plan, "settings", lambda: PLAN)
    (tmp_path / "pc1").mkdir()
    value = {"name": "Flash", "behavior_seed": SEED, "fingerprint": {"timezone": ZONE}}
    (tmp_path / "pc1" / "config.json").write_text(json.dumps(value))
    return value


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def queue_with(*items, timing="auto"):
    """items: (status, ms, post type)"""
    posts = [{"type": kind, "executions": [{"execution_id": f"e{i}", "profile_id": "pc1", "status": status,
                                            "scheduled_at": iso(at), **({"published_at": iso(at)} if status == "published" else {})}]}
             for i, (status, at, kind) in enumerate(items)]
    return {"daily_batches": [{"batch_id": "b", "schedule_window": {"timing": timing, "follow_active_hours": True},
                               "target_profiles": ["pc1"], "posts": posts}]}


def awake(config, at):
    return active_hours.within_active_hours(config, at)


def test_planned_posts_stay_inside_the_day_apart_and_under_the_limit(config):
    now = local(START - 3)  # before the account wakes up
    rand = random.Random(1)
    times = []
    for _ in range(12):
        times.append(posting_plan.plan_time(None, "pc1", config, now, rand, PLAN, extra=times))
    times.sort()
    assert all(awake(config, at) for at in times)
    assert all(b - a >= 2 * HOUR for a, b in zip(times, times[1:]))
    for at in times:
        day_start, day_end = posting_plan.account_day(config, at)
        assert at - day_start >= 30 * 60_000 and day_end - at >= 30 * 60_000
        assert sum(day_start <= other < day_end for other in times) <= 5
    # Twelve posts at most five a day reach the third day.
    assert len({posting_plan.account_day(config, at) for at in times}) >= 3


def test_other_batches_posts_are_kept_apart_too(config):
    now = local(START + 1)
    published = now - HOUR
    planned_elsewhere = now + 3 * HOUR
    queue = queue_with(("published", published, "reel"), ("pending", planned_elsewhere, "photo"), timing="exact")
    for seed in range(20):
        at = posting_plan.plan_time(queue, "pc1", config, now, random.Random(seed), PLAN)
        assert abs(at - published) >= 2 * HOUR and abs(at - planned_elsewhere) >= 2 * HOUR
    # Warm-ups don't count as posts.
    warm = queue_with(*[("published", now - i * 60_000, "warming") for i in range(6)])
    assert posting_plan.account_posts(warm, "pc1") == {"posted": [], "planned": []}


def test_a_full_day_moves_the_post_to_the_next_day(config):
    now = local(START + 1)
    day_start, day_end = posting_plan.account_day(config, now)
    done = [("published", day_start + i * 60_000, "reel") for i in range(5)]
    at = posting_plan.plan_time(queue_with(*done), "pc1", config, now, random.Random(3), PLAN)
    assert at >= day_end


def test_only_planned_batches_wait_for_the_gap(config):
    now = local(START + 3)
    recent = ("published", now - 30 * 60_000, "reel")
    auto = queue_with(recent, ("pending", now - 60_000, "reel"))
    assert [e["execution_id"] for e in ordered_due_executions(auto, now)] == []
    assert not posting_plan.admits(auto["daily_batches"][0]["posts"][1]["executions"][0], auto, config, now, PLAN)
    # An exact time is followed as given, however close to another post.
    exact = queue_with(recent, ("pending", now - 60_000, "reel"), timing="exact")
    exact["daily_batches"][0]["schedule_window"]["follow_active_hours"] = False
    assert [e["execution_id"] for e in ordered_due_executions(exact, now)] == ["e1"]


def test_an_overdue_planned_post_that_cant_start_gets_a_new_time(config):
    now = local(START + 3)
    queue = queue_with(("published", now - 30 * 60_000, "reel"), ("pending", now - 20 * 60_000, "reel"))
    moved = posting_plan.replan_overdue(queue, now)
    assert moved == ["e1"]
    execution = queue["daily_batches"][0]["posts"][1]["executions"][0]
    at = datetime.fromisoformat(execution["scheduled_at"]).timestamp() * 1000
    assert at - (now - 30 * 60_000) >= 2 * HOUR and awake(config, at)
    assert execution["plan_history"][-1]["to"] == execution["scheduled_at"]
    # One that could start now is left for the dispatcher.
    ready = queue_with(("pending", now - 20 * 60_000, "reel"))
    assert posting_plan.replan_overdue(ready, now) == []


def test_a_planned_post_waits_for_the_real_morning_not_the_8_hour_limit(config):
    asleep = local(START + LENGTH + 1)
    due = asleep - active_hours.MAX_HOLD_MS - HOUR
    if awake(config, asleep):
        pytest.skip("this persona has no night at that hour")
    auto = queue_with(("pending", due, "reel"))
    assert ordered_due_executions(auto, asleep) == []
