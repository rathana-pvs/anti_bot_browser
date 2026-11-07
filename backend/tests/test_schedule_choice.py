"""Batch start date, account spread, the Follow active hours switch, and start estimates."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from automation.engine.behavior_profile import Persona
from backend.routers.queue import parse_schedule_choice
from backend.services import account_hold, active_hours, queue_eta
from backend.services.profile_pipeline import ordered_due_executions
from backend.services.queue_scheduler import spread_offsets_ms

SEED = 8727273212897574946
ZONE = "America/Chicago"
START, LENGTH = Persona.from_seed(SEED).active_hours


def local_ms(hour, minute=15, day=30):
    return datetime(2026, 10, day, hour % 24, minute, tzinfo=ZoneInfo(ZONE)).timestamp() * 1000


ASLEEP = local_ms(START + LENGTH)  # the first hour after the account's day ends
AWAKE = local_ms(START)


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(active_hours, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(account_hold, "PROFILES_DIR", tmp_path, raising=False)
    (tmp_path / "profile_001").mkdir()
    (tmp_path / "profile_001" / "config.json").write_text(json.dumps({
        "name": "Flash", "behavior_seed": SEED, "fingerprint": {"timezone": ZONE}}))
    return tmp_path


def batch(due_ms, follow=None, spread=None, post_type="reel"):
    execution = {"execution_id": "e1", "profile_id": "profile_001", "status": "pending",
                 "scheduled_at": datetime.fromtimestamp(due_ms / 1000, tz=timezone.utc).isoformat()}
    window = {"start_now": False, "profile_stagger_seconds": 0}
    if follow is not None:
        window["follow_active_hours"] = follow
    if spread is not None:
        window["spread_minutes"] = spread
    return {"daily_batches": [{"batch_id": "b", "schedule_window": window, "target_profiles": ["profile_001"],
                               "posts": [{"type": post_type, "executions": [execution]}]}]}


def test_spread_offsets_are_in_order_inside_the_window_and_never_too_close():
    for spread in (15, 30, 60):
        offsets = spread_offsets_ms(6, spread)
        assert offsets == sorted(offsets)
        assert offsets[0] >= 0 and offsets[-1] < spread * 60_000
        slot = spread * 60_000 / 6
        assert all(b - a >= min(60_000, slot / 2) for a, b in zip(offsets, offsets[1:]))
    assert spread_offsets_ms(6, 30, rand=lambda: 0.1) != spread_offsets_ms(6, 30, rand=lambda: 0.9)


def test_no_spread_still_varies_the_gap_between_accounts():
    offsets = spread_offsets_ms(5, 0)
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    assert all(45_000 <= gap <= 120_000 for gap in gaps)
    assert spread_offsets_ms(0, 30) == []


def test_a_start_time_in_the_past_is_refused_instead_of_running_at_once():
    now = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="already passed"):
        parse_schedule_choice({"start_at": (now - timedelta(hours=5)).isoformat(), "spread_minutes": 30}, False, now)
    # A moment ago still counts as now.
    choice = parse_schedule_choice({"start_at": (now - timedelta(seconds=30)).isoformat(), "spread_minutes": 30}, False, now)
    assert choice["timing"] == "exact" and choice["spread_minutes"] == 30 and choice["follow_active_hours"] is False
    with pytest.raises(ValueError, match="Pick the date"):
        parse_schedule_choice({"timing": "exact", "spread_minutes": 30}, False, now)
    with pytest.raises(ValueError, match="timezone"):
        parse_schedule_choice({"start_at": "2026-11-07T20:00:00", "spread_minutes": 0}, False, now)
    with pytest.raises(ValueError, match="spread_minutes"):
        parse_schedule_choice({"start_at": now.isoformat(), "spread_minutes": 7}, False, now)
    with pytest.raises(ValueError, match="30 days"):
        parse_schedule_choice({"start_at": (now + timedelta(days=40)).isoformat(), "spread_minutes": 0}, False, now)


def test_the_three_timings():
    now = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)
    assert parse_schedule_choice({"spread_minutes": 15}, True, now) == {
        "timing": "now", "start_at": None, "spread_minutes": 15, "follow_active_hours": False}
    # Follow active hours needs no time at all: the plan picks it.
    assert parse_schedule_choice({"timing": "auto", "start_at": "garbage"}, False, now) == {
        "timing": "auto", "start_at": None, "spread_minutes": None, "follow_active_hours": True}
    # An older client keeps the old behaviour.
    assert parse_schedule_choice({}, False, now)["timing"] is None
    with pytest.raises(ValueError, match="timing"):
        parse_schedule_choice({"timing": "later"}, False, now)


def test_switch_off_starts_at_the_scheduled_time_even_at_night(account):
    assert ordered_due_executions(batch(ASLEEP - 60_000), ASLEEP) == []
    assert len(ordered_due_executions(batch(ASLEEP - 60_000, follow=False), ASLEEP)) == 1
    # Warming always keeps to waking hours.
    assert ordered_due_executions(batch(ASLEEP - 60_000, follow=False, post_type="warming"), ASLEEP) == []


def test_a_spread_batch_starts_each_account_at_its_own_time(account):
    queue = batch(AWAKE + 600_000, spread=30)
    assert ordered_due_executions(queue, AWAKE) == []
    assert len(ordered_due_executions(queue, AWAKE + 600_000)) == 1


def test_next_awake_is_the_start_of_the_account_day(account):
    config = active_hours._profile_config("profile_001", {})
    wake = active_hours.next_awake_ms(config, ASLEEP)
    local = datetime.fromtimestamp(wake / 1000, tz=ZoneInfo(ZONE))
    assert (local.hour, local.minute) == (START, 0) and wake > ASLEEP
    assert active_hours.next_awake_ms(config, AWAKE) == AWAKE


def test_estimate_says_why_an_item_waits_and_when_it_starts(account, monkeypatch):
    monkeypatch.setattr(account_hold, "admits", lambda *a, **k: True)
    info = queue_eta.explain(batch(ASLEEP - 60_000), ASLEEP)["e1"]
    assert info["account_timezone"] == ZONE and info["awake_hours"] == [START, (START + LENGTH) % 24]
    start = datetime.fromisoformat(info["expected_start_at"]).timestamp() * 1000
    hold_limit = ASLEEP - 60_000 + active_hours.MAX_HOLD_MS
    if 24 - LENGTH <= 8:
        assert info["wait_reason"] == "active_hours" and start == active_hours.next_awake_ms(
            active_hours._profile_config("profile_001", {}), ASLEEP)
    else:
        assert info["wait_reason"] == "active_hours_limit" and start == hold_limit

    later = queue_eta.explain(batch(AWAKE + 3_600_000), AWAKE)["e1"]
    assert later["wait_reason"] == "scheduled"
    assert datetime.fromisoformat(later["expected_start_at"]).timestamp() * 1000 == AWAKE + 3_600_000

    off = queue_eta.explain(batch(ASLEEP - 60_000, follow=False), ASLEEP)["e1"]
    assert off["wait_reason"] == "starting"
    assert queue_eta.explain(batch(ASLEEP - 60_000, follow=False), ASLEEP, busy=True)["e1"]["wait_reason"] == "waiting_for_slot"


def test_estimate_for_an_account_travelling_after_a_proxy_change(account, monkeypatch):
    until = datetime.fromtimestamp((AWAKE + 7_200_000) / 1000, tz=timezone.utc)
    monkeypatch.setattr(account_hold, "travelling_until", lambda config, now: until)
    monkeypatch.setattr(account_hold, "is_paused", lambda config, now=None: "travelling")
    monkeypatch.setattr(account_hold, "admits", lambda *a, **k: False)
    info = queue_eta.explain(batch(AWAKE - 60_000, follow=False), AWAKE)["e1"]
    assert info["wait_reason"] == "travelling" and info["expected_start_at"] == until.isoformat()
