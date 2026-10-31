"""Accounts on hold get no automatic work; a new proxy restarts an account gently."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.routers import profiles
from backend.services import account_hold, auto_warming
from backend.services.account_health import compute
from backend.services.account_hold import admits, hold, note_proxy_change, proxy_changed, release

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
NOW_MS = NOW.timestamp() * 1000


def config(**account):
    return {"id": "profile_001", "created_at": (NOW - timedelta(days=60)).isoformat(),
            "fingerprint": {"timezone": "America/New_York"}, "account": account}


def ready_history():
    return [{"at": (NOW - timedelta(days=2 + day)).isoformat(), "kind": "post", "status": "published", "error": ""}
            for day in range(12)]


def visit(hours_ago):
    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(), "kind": "warming", "status": "completed", "error": ""}


@pytest.fixture
def warming_on(monkeypatch):
    monkeypatch.setattr(auto_warming, "enabled", lambda: True)


def item(status="pending", **extra):
    return {"execution_id": "e1", "profile_id": "profile_001", "status": status, **extra}


def test_an_account_on_hold_shows_on_hold_until_released():
    value = config()
    hold(value, "changing proxy", NOW)
    health = compute(value, ready_history(), NOW)
    assert health["stage"] == "on_hold" and "changing proxy" in health["reason"]
    release(value, NOW)
    assert compute(value, ready_history(), NOW)["stage"] == "ready"


def test_nothing_new_starts_on_an_account_on_hold(warming_on):
    value = config(on_hold={"since": NOW.isoformat(), "note": ""})
    for execution, post in ((item(), {"type": "photo"}), (item("ready"), {"type": "reel"}),
                            (item(), {"type": "warming"}), (item(cloud_attempt_id="a1"), {"type": "photo"})):
        assert not admits(execution, post, value, NOW_MS, dict)
    assert admits(item("running"), {"type": "photo"}, value, NOW_MS, dict)


def test_only_the_network_counts_as_a_proxy_change():
    before = {"mode": "pool", "proxy_id": "p1", "proxy_host": "1.2.3.4", "proxy_port": 1080, "proxy_user": "u"}
    assert not proxy_changed(before, dict(before))
    assert proxy_changed(before, dict(before, proxy_id="p2", proxy_host="5.6.7.8"))
    assert proxy_changed(before, {"mode": "direct"})


def test_a_new_proxy_restarts_the_account_and_posts_wait_for_a_visit(warming_on):
    value = config()
    note_proxy_change(value, NOW - timedelta(hours=2))
    health = compute(value, ready_history(), NOW)
    assert health["stage"] == "warming_up" and health["proxy_restart"]["visited"] is False
    assert health["daily_limit"] == 2
    history = lambda: {"profile_001": {f"h{i}": entry for i, entry in enumerate(ready_history())}}
    assert not admits(item(), {"type": "photo"}, value, NOW_MS, history)
    assert admits(item(), {"type": "warming"}, value, NOW_MS, history)
    assert admits(item(cloud_attempt_id="a1"), {"type": "photo"}, value, NOW_MS, history)
    visited = lambda: {"profile_001": {"v": visit(1)}}
    assert admits(item(), {"type": "photo"}, value, NOW_MS, visited)
    assert compute(value, ready_history() + [visit(1)], NOW)["proxy_restart"]["visited"] is True


def test_posts_are_not_held_without_auto_warming_or_after_a_day(monkeypatch):
    value = config()
    note_proxy_change(value, NOW - timedelta(hours=2))
    monkeypatch.setattr(auto_warming, "enabled", lambda: False)
    assert admits(item(), {"type": "photo"}, value, NOW_MS, dict)
    monkeypatch.setattr(auto_warming, "enabled", lambda: True)
    later = NOW_MS + 23 * 3600 * 1000
    assert admits(item(), {"type": "photo"}, value, later, dict)
    assert compute(value, ready_history(), NOW + timedelta(hours=23))["stage"] == "ready"


def test_a_proxy_changed_on_hold_restarts_on_release():
    value = config()
    hold(value, "", NOW - timedelta(hours=5))
    note_proxy_change(value, NOW - timedelta(hours=4))
    assert value["account"]["proxy_restart"] == {"pending": True}
    release(value, NOW)
    window = account_hold.restart(value, NOW)
    assert window["since"] == NOW and window["until"] == NOW + timedelta(hours=24)


def test_a_restarted_account_gets_the_next_visit_even_with_a_post_due(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_warming.active_hours, "persona_hours_admit", lambda config, now_ms: True)
    monkeypatch.setattr(auto_warming, "persona_day", lambda config, now_ms: {"rest_day": False, "frequency": 1.0})
    configs = {"a": dict(config(), id="a"), "b": dict(config(), id="b")}
    health = {"a": {"stage": "warming_up", "proxy_restart": {"visited": False}}, "b": {"stage": "new"}}
    due = {"daily_batches": [{"batch_id": "x", "posts": [{"type": "photo", "executions": [
        {"profile_id": "a", "status": "pending", "scheduled_at": NOW.isoformat()}]}]}]}
    ranked = auto_warming.candidates(due, configs, health, {}, NOW_MS)
    assert ranked[0]["profile_id"] == "a" and ranked[0]["reason"] == "First visit after a proxy change"


def write_profile(tmp_path, monkeypatch, account=None):
    directory = tmp_path / "profiles" / "profile_001"
    directory.mkdir(parents=True)
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(account_hold, "PROFILES_DIR", tmp_path / "profiles")
    path = directory / "config.json"
    path.write_text(json.dumps({"id": "profile_001", "name": "A", "account": account or {}}))
    return path


def test_hold_and_release_endpoints(tmp_path, monkeypatch):
    path = write_profile(tmp_path, monkeypatch)
    monkeypatch.setattr(profiles, "load_posting_queue", lambda: {"daily_batches": []})
    profiles.hold_profile("profile_001", profiles.HoldRequest(note="new proxy"))
    assert json.loads(path.read_text())["account"]["on_hold"]["note"] == "new proxy"
    assert account_hold.is_on_hold("profile_001")
    profiles.release_profile("profile_001")
    assert "on_hold" not in json.loads(path.read_text())["account"]
    assert not account_hold.is_on_hold("profile_001")


def test_the_settings_form_cannot_change_hold_state(tmp_path, monkeypatch):
    path = write_profile(tmp_path, monkeypatch, {"on_hold": {"since": NOW.isoformat(), "note": "keep"}})
    profiles.update_profile("profile_001", profiles.ProfileUpdateRequest(
        account={"notes": "hello", "on_hold": None, "proxy_restart": {"since": "x"}}))
    account = json.loads(path.read_text())["account"]
    assert account["on_hold"]["note"] == "keep" and account["notes"] == "hello"
    assert "proxy_restart" not in account


def test_the_dispatcher_skips_accounts_on_hold(tmp_path, monkeypatch):
    from backend.services import active_hours
    from backend.services.profile_pipeline import ordered_due_executions
    write_profile(tmp_path, monkeypatch, {"on_hold": {"since": NOW.isoformat(), "note": ""}})
    monkeypatch.setattr(active_hours, "PROFILES_DIR", tmp_path / "profiles")
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    queue = {"daily_batches": [{"batch_id": "b", "schedule_window": {"start_now": True, "profile_stagger_seconds": 0},
                                "target_profiles": ["profile_001"], "posts": [{"type": "photo", "executions": [
                                    item(scheduled_at=due_at.isoformat())]}]}]}
    assert ordered_due_executions(queue) == []
    profiles.release_profile("profile_001")
    assert len(ordered_due_executions(queue)) == 1
