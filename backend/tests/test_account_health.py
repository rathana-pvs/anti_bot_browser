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


@pytest.mark.parametrize('code', ['account_locked', 'identity_verification_required'])
@pytest.mark.parametrize('status', ['skipped_auth_required', 'needs_review'])
def test_live_account_restriction_skip_marks_attention(tmp_path, monkeypatch, code, status):
    monkeypatch.setattr(account_health, 'HISTORY_FILE', tmp_path / 'history.json')
    execution = dict(profile_id='profile_001', execution_id='blocked_live',
                     status=status, ended_at=NOW.isoformat(),
                     error=account_health.ACCOUNT_BLOCK_ERRORS[code])
    history = sync_history({'daily_batches':[{'posts':[{'type':'live','executions':[execution]}]}]}, NOW)
    health = compute(config(), account_health.entries_for(history, 'profile_001'), NOW)
    assert health['stage'] == 'attention'
    assert ('locked' if code == 'account_locked' else 'identity verification') in health['reason']


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


def test_routine_failures_do_not_mark_the_account_as_needing_attention():
    failures = [entry(3, "failed_before_publish"), entry(2, "skipped_session_unverified"), entry(1, "failed_before_publish")]
    assert compute(config(), posts(12) + failures, NOW)["stage"] == "ready"
    # Unknown publication outcomes remain a job-level concern as well.
    assert compute(config(), posts(12) + failures[:2] + [entry(1, "uncertain")], NOW)["stage"] == "ready"


@pytest.mark.parametrize('error', [
    'Absolute automation deadline exceeded or ownership lost',
    'Live pinned comment entry could not be verified (draft_conflict)',
    'Stream stopped before Facebook confirmed the broadcast',
    'Proxy connection timed out',
])
def test_repeated_job_errors_remain_in_history_without_an_account_warning(error):
    failures = [entry(hours, 'failed_before_publish', error=error) for hours in (3, 2, 1)]
    health = compute(config(), posts(12) + failures, NOW)
    assert health['stage'] == 'ready'
    assert 'evidence' not in health


def test_routine_failures_do_not_clear_an_unresolved_login_problem():
    checkpoint = entry(5, status='skipped_auth_required')
    failures = [entry(hours, 'failed_before_publish', error='Timed out') for hours in (3, 2, 1)]
    assert compute(config(), posts(12) + [checkpoint] + failures, NOW)['stage'] == 'attention'


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


def test_a_page_account_showing_the_personal_profile_needs_attention():
    personal = dict(entry(2), identity="personal")
    health = compute(config(), posts(12) + [personal], NOW)
    assert health["stage"] == "attention"
    assert "personal profile" in health["reason"]
    back_on_page = dict(entry(1), identity="page")
    assert compute(config(), posts(12) + [personal, back_on_page], NOW)["stage"] == "ready"
    # A run that could not tell keeps the last known identity.
    assert compute(config(), posts(12) + [personal, entry(1)], NOW)["stage"] == "attention"


def test_history_keeps_the_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(account_health, "HISTORY_FILE", tmp_path / "history.json")
    done = {"profile_id": "profile_001", "execution_id": "e1", "status": "skipped_personal_profile",
            "scheduled_at": NOW.isoformat(), "facebook_identity": "personal"}
    assert sync_history(queue(done), NOW)["profile_001"]["e1"]["identity"] == "personal"


def test_attention_names_the_items_behind_it():
    checkpoint = dict(entry(1, "skipped_auth_required"), execution_id="e_check", error="Login required")
    health = compute(config(), posts(12) + [checkpoint], NOW)
    assert health["stage"] == "attention"
    assert health["evidence"] == [{"execution_id": "e_check", "at": checkpoint["at"], "kind": "post",
                                   "status": "skipped_auth_required", "error": "Login required"}]


def test_history_entries_carry_their_execution_id():
    from backend.services.account_health import entries_for
    assert entries_for({"p": {"e1": {"at": "x"}}}, "p") == [{"at": "x", "execution_id": "e1"}]
    assert entries_for({}, "p") == []


def test_the_last_finished_visit_is_reported():
    visits = [entry(5, "completed", "warming"), entry(2, "completed", "warming"), entry(1, "failed_before_publish", "warming")]
    assert compute(config(), posts(3) + visits, NOW)["last_visit_at"] == visits[1]["at"]
    assert compute(config(), posts(3), NOW)["last_visit_at"] is None


def test_publishing_identity_restriction_needs_attention_even_after_warming():
    restriction = dict(entry(5, 'uncertain', error='reel_publish_rejected'),
                       attention_reason='identity_verification_required', execution_id='blocked')
    history = posts(12) + [restriction, entry(1, 'completed', kind='warming')]
    health = compute(config(), history, NOW)
    assert health['stage'] == 'attention'
    assert 'identity verification' in health['reason']
    assert health['evidence'][0]['execution_id'] == 'blocked'
    # A later successful publication confirms the publishing restriction ended.
    assert compute(config(), history + [entry(.5)], NOW)['stage'] != 'attention'


def test_identity_restriction_is_read_from_matching_attempt_and_survives_queue_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(account_health, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(account_health, 'HISTORY_FILE', tmp_path / 'history.json')
    run = tmp_path / 'profiles/profile_001/automation_evidence/run'
    run.mkdir(parents=True)
    (run / 'attempt.json').write_text(json.dumps({'execution_id': 'e1', 'lease_id': 'lease1'}))
    (run / '15_reel_publication_verification.json').write_text(json.dumps({
        'label': 'reel_publication_verification', 'state': 'error_dialog', 'signals': ['confirm your identity'],
    }))
    execution = {'profile_id': 'profile_001', 'execution_id': 'e1', 'status': 'uncertain',
                 'ended_at': NOW.isoformat(), 'error': 'reel_publish_rejected',
                 'evidence_dir': str(run), 'last_scheduler_lease': {'lease_id': 'lease1'}}
    before = json.loads(json.dumps(execution))
    history = sync_history(queue(execution), NOW)
    assert execution == before  # Publication status is never rewritten by account health.
    assert history['profile_001']['e1']['attention_reason'] == 'identity_verification_required'
    assert sync_history({'daily_batches': []}, NOW) == history
    assert compute(config(), account_health.entries_for(history, 'profile_001'), NOW)['stage'] == 'attention'


@pytest.mark.parametrize('case', ['generic_error', 'wrong_attempt', 'outside_profile'])
def test_generic_rejection_or_unmatched_evidence_does_not_mark_attention(tmp_path, monkeypatch, case):
    monkeypatch.setattr(account_health, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(account_health, 'HISTORY_FILE', tmp_path / 'history.json')
    run = tmp_path / ('outside' if case == 'outside_profile' else 'profiles/profile_001/automation_evidence/run')
    run.mkdir(parents=True)
    (run / 'attempt.json').write_text(json.dumps({'execution_id': 'other' if case == 'wrong_attempt' else 'e1', 'lease_id': 'lease1'}))
    (run / '15_reel_publication_verification.json').write_text(json.dumps({
        'label': 'reel_publication_verification', 'state': 'error_dialog',
        'signals': ['something went wrong'] if case == 'generic_error' else ['confirm your identity'],
    }))
    execution = {'profile_id': 'profile_001', 'execution_id': 'e1', 'status': 'uncertain',
                 'ended_at': NOW.isoformat(), 'error': 'reel_publish_rejected',
                 'evidence_dir': str(run), 'last_scheduler_lease': {'lease_id': 'lease1'}}
    record = sync_history(queue(execution), NOW)['profile_001']['e1']
    assert 'attention_reason' not in record


def test_verified_identity_restriction_becomes_skip_without_erasing_publish_history(tmp_path, monkeypatch):
    from backend.services.queue_safety import repair_identity_verification_skip
    monkeypatch.setattr(account_health, 'PROFILES_DIR', tmp_path / 'profiles')
    run = tmp_path / 'profiles/profile_001/automation_evidence/skip-run'
    run.mkdir(parents=True)
    (run / 'attempt.json').write_text(json.dumps({'execution_id': 'blocked', 'lease_id': 'lease1'}))
    (run / '15_reel_publication_verification.json').write_text(json.dumps({
        'label': 'reel_publication_verification', 'state': 'error_dialog', 'signals': ['confirm your identity'],
    }))
    execution = {'profile_id': 'profile_001', 'execution_id': 'blocked', 'status': 'uncertain',
                 'ended_at': NOW.isoformat(), 'error': 'reel_publish_rejected',
                 'evidence_dir': str(run), 'last_scheduler_lease': {'lease_id': 'lease1'},
                 'stage_history': [{'stage': 'publish_clicked'}]}
    assert repair_identity_verification_skip(execution)
    assert execution['status'] == 'skipped_auth_required'
    assert execution['stage_history'][0]['stage'] == 'publish_clicked'
    assert execution['evidence_dir'] == str(run)
    assert not repair_identity_verification_skip(execution)
    record = account_health._entry(execution, 'reel')
    assert record['attention_reason'] == 'identity_verification_required'


@pytest.mark.parametrize('overrides', [
    {'error': 'reel_publish_loading_timeout'},
    {'scheduler_lease': {'lease_id': 'active'}},
    {'cloud_attempt_id': 'remote-attempt'},
    {'ended_at': None},
    {'status': 'published'},
    {'error': 'reel_publish_rejected', 'evidence_dir': None},
])
def test_skip_repair_does_not_release_unverified_or_active_attempts(overrides):
    from backend.services.queue_safety import repair_identity_verification_skip
    execution = {'status': 'uncertain', 'ended_at': NOW.isoformat(),
                 'error': account_health.IDENTITY_VERIFICATION_ERROR, **overrides}
    before = dict(execution)
    assert not repair_identity_verification_skip(execution)
    assert execution == before
