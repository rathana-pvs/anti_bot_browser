from datetime import datetime, timedelta, timezone

from backend.services import account_activity

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def test_docker_times_with_nanoseconds_are_read():
    assert account_activity._parse("2026-11-05T22:14:03.123456789Z") == datetime(2026, 11, 5, 22, 14, 3, 123456, tzinfo=timezone.utc)
    assert account_activity._parse("0001-01-01T00:00:00Z") is None


def test_the_latest_source_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(account_activity, "ACTIVITY_FILE", tmp_path / "activity.json")
    entries = [{"at": (NOW - timedelta(hours=9)).isoformat()}, {"at": (NOW - timedelta(hours=30)).isoformat()}]
    pc = {"runtime": {}}
    assert account_activity.last_active("p1", pc, NOW, entries, container=lambda pid, now: NOW - timedelta(hours=12)) == NOW - timedelta(hours=9)
    # A running browser is active now.
    assert account_activity.last_active("p1", pc, NOW, entries, container=lambda pid, now: now) == NOW
    account_activity.note_active("p1", NOW - timedelta(hours=1))
    assert account_activity.last_active("p1", pc, NOW, entries, container=lambda pid, now: None) == NOW - timedelta(hours=1)


def test_a_phone_account_on_screen_is_active_now(tmp_path, monkeypatch):
    monkeypatch.setattr(account_activity, "ACTIVITY_FILE", tmp_path / "activity.json")
    phone = {"runtime": {"kind": "android_user"}}
    assert account_activity.last_active("p2", phone, NOW, [], phone_on_screen=lambda: True) == NOW
    assert account_activity.last_active("p2", phone, NOW, [], phone_on_screen=lambda: False) is None
