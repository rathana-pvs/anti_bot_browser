"""Page name and followers: only warming writes them, and a bad reading never wipes good data."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import facebook_page

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(facebook_page, "PROFILES_DIR", tmp_path)
    (tmp_path / "profile_001").mkdir()
    (tmp_path / "profile_001" / "config.json").write_text('{"id": "profile_001"}', encoding="utf-8")
    return tmp_path


def test_a_reading_is_saved_next_to_the_config_without_touching_it(profiles):
    facebook_page.record("profile_001", {"name": "News 429M", "kind": "page", "followers": 3400}, "exec_1", NOW)
    saved = facebook_page.load("profile_001")
    assert saved["name"] == "News 429M" and saved["kind"] == "page" and saved["followers"] == 3400
    assert saved["seen_at"] == saved["followers_seen_at"] == NOW.isoformat() and saved["execution_id"] == "exec_1"
    assert (profiles / "profile_001" / "config.json").read_text(encoding="utf-8") == '{"id": "profile_001"}'


def test_a_reading_without_followers_keeps_the_old_count():
    facebook_page.record("profile_001", {"name": "News 429M", "kind": "page", "followers": 3400}, now=NOW)
    later = NOW + timedelta(days=1)
    facebook_page.record("profile_001", {"name": "News 429M Daily", "kind": None}, now=later)
    saved = facebook_page.load("profile_001")
    assert saved["name"] == "News 429M Daily" and saved["followers"] == 3400 and saved["kind"] == "page"
    assert saved["seen_at"] == later.isoformat() and saved["followers_seen_at"] == NOW.isoformat()


@pytest.mark.parametrize("snapshot", [None, "text", {}, {"name": ""}, {"name": "x"}, {"followers": 10},
                                      {"name": 5, "followers": 10}])
def test_unusable_readings_save_nothing(snapshot):
    assert facebook_page.record("profile_001", snapshot, now=NOW) is None
    assert facebook_page.load("profile_001") is None


def test_bad_values_are_dropped_but_the_name_is_kept():
    facebook_page.record("profile_001", {"name": "  News   429M  ", "kind": "group", "followers": -5}, now=NOW)
    assert facebook_page.load("profile_001") == {"name": "News 429M", "seen_at": NOW.isoformat(), "execution_id": None}
    facebook_page.record("profile_001", {"name": "News", "followers": True}, now=NOW)
    assert "followers" not in facebook_page.load("profile_001")


def test_unknown_or_unsafe_accounts_are_ignored(profiles):
    assert facebook_page.record("profile_404", {"name": "News"}, now=NOW) is None
    assert facebook_page.record("../evil", {"name": "News"}, now=NOW) is None
    assert not (profiles / "profile_404").exists()


def test_saving_from_a_result_never_raises(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(facebook_page, "atomic_write_json", broken)
    facebook_page.record_from_result("profile_001", {"account_snapshot": {"name": "News"}}, "exec_1")
    facebook_page.record_from_result("profile_001", None)
    facebook_page.record_from_result("profile_001", {"status": "completed"})


def test_a_missing_or_old_reading_needs_a_profile_visit():
    assert facebook_page.needs_refresh("profile_001", NOW)
    facebook_page.record("profile_001", {"name": "News"}, now=NOW)
    assert not facebook_page.needs_refresh("profile_001", NOW + timedelta(days=2))
    assert facebook_page.needs_refresh("profile_001", NOW + facebook_page.REFRESH_AFTER)
