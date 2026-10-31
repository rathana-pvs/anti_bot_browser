"""Active hours hold desktop items outside the account's waking hours, and never for long."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from automation.engine.behavior_profile import Persona
from backend.services import active_hours
from backend.services.profile_pipeline import ordered_due_executions

SEED = 8727273212897574946
ZONE = "America/Chicago"


def at_local_hour(hour):
    return datetime(2026, 10, 30, hour, 15, tzinfo=ZoneInfo(ZONE)).timestamp() * 1000


def outside_and_inside():
    start, length = Persona.from_seed(SEED).active_hours
    inside = start
    outside = (start + length) % 24
    return at_local_hour(outside), at_local_hour(inside)


def write_profile(root, natural=True):
    (root / "profile_001").mkdir(parents=True)
    (root / "profile_001" / "config.json").write_text(json.dumps({
        "behavior_seed": SEED, "fingerprint": {"timezone": ZONE},
        "automation": {"natural_session": natural},
    }))


def queue(start_now=False, status="pending", cloud=False, scheduled_ms=0):
    execution = {"execution_id": "e1", "profile_id": "profile_001", "status": status,
                 "scheduled_at": datetime.fromtimestamp(scheduled_ms / 1000).astimezone().isoformat()}
    if cloud:
        execution["cloud_attempt_id"] = "attempt"
    return {"daily_batches": [{"batch_id": "b", "created_at": execution["scheduled_at"],
                               "schedule_window": {"start_now": start_now, "profile_stagger_seconds": 0},
                               "target_profiles": ["profile_001"],
                               "posts": [{"executions": [execution]}]}]}


def test_natural_item_waits_outside_active_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(active_hours, "PROFILES_DIR", tmp_path)
    write_profile(tmp_path)
    outside, inside = outside_and_inside()
    assert ordered_due_executions(queue(scheduled_ms=outside - 60000), outside) == []
    assert len(ordered_due_executions(queue(scheduled_ms=inside - 60000), inside)) == 1


def test_items_that_are_never_held(tmp_path, monkeypatch):
    monkeypatch.setattr(active_hours, "PROFILES_DIR", tmp_path)
    write_profile(tmp_path)
    outside, _ = outside_and_inside()
    due = outside - 60000
    assert len(ordered_due_executions(queue(start_now=True, scheduled_ms=due), outside)) == 1
    assert len(ordered_due_executions(queue(cloud=True, scheduled_ms=due), outside)) == 1
    assert len(ordered_due_executions(queue(status="ready", scheduled_ms=due), outside)) == 1
    late = outside - active_hours.MAX_HOLD_MS
    assert len(ordered_due_executions(queue(scheduled_ms=late), outside)) == 1


def test_an_old_switched_off_account_is_held_too(tmp_path, monkeypatch):
    monkeypatch.setattr(active_hours, "PROFILES_DIR", tmp_path)
    write_profile(tmp_path, natural=False)
    outside, _ = outside_and_inside()
    assert ordered_due_executions(queue(scheduled_ms=outside - 60000), outside) == []
