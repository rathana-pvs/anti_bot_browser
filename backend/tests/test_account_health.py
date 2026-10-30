"""Account health stages come from each account's own history and stay advice only."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.routers import profiles
from backend.services import account_health
from backend.services.account_health import compute, sync_history

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)  # 11:00 in New York


def config(days_old=60, **account):
    return {"id": "profile_001", "created_at": (NOW - timedelta(days=days_old)).isoformat(),
            "fingerprint": {"timezone": "America/New_York"}, "account": account}


def entry(hours_ago, status="published", kind="post", error=""):
    return {"at": (NOW - timedelta(hours=hours_ago)).isoformat(), "kind": kind, "status": status, "error": error}


def posts(count, start_hours=48):
    return [entry(start_hours + index * 24) for index in range(count)]


def test_an_account_without_finished_sessions_is_new():
    health = compute(config(days_old=40), [], NOW)
    assert health["stage"] == "new"
    assert health["daily_limit"] == 0


def test_young_or_thin_history_is_warming_up_and_long_history_is_ready():
    assert compute(config(days_old=10), posts(3), NOW)["stage"] == "warming_up"
    assert compute(config(days_old=60), posts(4), NOW)["stage"] == "warming_up"
    ready = compute(config(days_old=60), posts(12), NOW)
    assert ready["stage"] == "ready"
    assert ready["daily_limit"] == 4


def test_reaching_the_daily_limit_rests_until_local_midnight():
    today = [entry(1), entry(2), entry(3), entry(4)]
    health = compute(config(), posts(12) + today, NOW)
    assert health["stage"] == "resting"
    assert health["posts_today"] == 4
    assert health["until"] == datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc).isoformat()
    # Yesterday evening in New York is not today, although it is today in UTC.
    assert compute(config(), posts(12) + [entry(12)] * 4, NOW)["posts_today"] == 0


def test_warming_visits_do_not_count_as_posts():
    visits = [entry(1, status="completed", kind="warming")] * 5
    health = compute(config(), posts(12) + visits, NOW)
    assert health["posts_today"] == 0
    assert health["stage"] == "ready"


def test_a_checkpoint_needs_attention_until_a_later_success():
    checkpoint = entry(5, status="skipped_auth_required")
    assert compute(config(), posts(12) + [checkpoint], NOW)["stage"] == "attention"
    assert compute(config(), posts(12) + [checkpoint, entry(1)], NOW)["stage"] == "ready"


def test_three_failed_sessions_in_a_row_need_attention():
    failures = [entry(3, "failed_before_publish"), entry(2, "skipped_session_unverified"), entry(1, "failed_before_publish")]
    assert compute(config(), posts(12) + failures, NOW)["stage"] == "attention"
    # A slow proxy's uncertain upload is not a failure streak.
    assert compute(config(), posts(12) + failures[:2] + [entry(1, "uncertain")], NOW)["stage"] == "ready"


def test_facebook_pushback_rests_for_three_days():
    rejected = entry(30, "failed_before_publish", error="reel_publish_rejected")
    health = compute(config(), posts(12, start_hours=100) + [rejected], NOW)
    assert health["stage"] == "resting"
    assert compute(config(), posts(12, start_hours=100) + [entry(80, "failed_before_publish", error="reel_publish_rejected")],
                   NOW)["stage"] == "ready"


def test_manual_stage_sets_growth_but_problems_still_show():
    assert compute(config(days_old=2, stage_override="ready"), [], NOW)["stage"] == "ready"
    assert compute(config(days_old=2, warming_complete=True), [], NOW)["manual"] is True
    checkpoint = [entry(1, status="skipped_auth_required")]
    assert compute(config(days_old=2, stage_override="ready"), checkpoint, NOW)["stage"] == "attention"


def queue(*executions, kind="reel"):
    return {"daily_batches": [{"posts": [{"type": kind, "executions": list(executions)}]}]}


def test_history_survives_a_cleared_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(account_health, "HISTORY_FILE", tmp_path / "history.json")
    done = {"profile_id": "profile_001", "execution_id": "e1", "status": "published",
            "published_at": (NOW - timedelta(hours=2)).isoformat()}
    waiting = {"profile_id": "profile_001", "execution_id": "e2", "status": "pending",
               "scheduled_at": NOW.isoformat()}
    sync_history(queue(done, waiting), NOW)
    history = sync_history({"daily_batches": []}, NOW)
    assert list(history["profile_001"]) == ["e1"]
    assert history["profile_001"]["e1"]["kind"] == "reel"
    old = dict(done, execution_id="e0", published_at=(NOW - timedelta(days=200)).isoformat())
    assert "e0" not in sync_history(queue(old), NOW)["profile_001"]


def test_stage_override_is_validated(tmp_path, monkeypatch):
    directory = tmp_path / "profiles" / "profile_001"
    directory.mkdir(parents=True)
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    (directory / "config.json").write_text(json.dumps({"id": "profile_001", "name": "A", "account": {}}))
    with pytest.raises(HTTPException) as error:
        profiles.update_profile("profile_001", profiles.ProfileUpdateRequest(account={"stage_override": "resting"}))
    assert error.value.status_code == 400
    assert "Account stage" in error.value.detail
