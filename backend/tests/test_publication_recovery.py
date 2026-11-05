"""Completed publishing results survive lost stdout and manager restarts."""
import json
from copy import deepcopy

import pytest

from backend.services import automation_service as svc, queue_service
from backend.services.publication_recovery import recover_completed_publication


@pytest.fixture
def saved_attempt(tmp_path, monkeypatch):
    profiles = tmp_path / 'profiles'
    directory = profiles / 'profile-4' / 'automation_evidence' / 'run_FacebookReelTask'
    directory.mkdir(parents=True)
    execution = {
        'execution_id': 'exec-4', 'profile_id': 'profile-4', 'status': 'running',
        'stage': 'commenting', 'container_owner_token': 'owner-4',
        'evidence_dir': str(directory),
        'scheduler_lease': {'lease_id': 'lease-4', 'kind': 'publisher',
                            'claimed_at': '2026-01-01T00:00:00+00:00',
                            'expires_at': '2026-01-01T00:01:00+00:00'},
        'stage_history': [{'stage': 'running', 'reason': 'publisher_scheduler_lease_claimed',
                           'lease_id': 'lease-4'}, {'stage': 'publish_clicked'}, {'stage': 'commenting'}],
    }
    context = dict(execution_id='exec-4', lease_id='lease-4', owner_token='owner-4')
    result = dict(run_id=directory.name, status='published', error=None,
                  finished_at='2026-01-01T00:02:00+00:00', publication_status='published',
                  first_comment='submitted_verified', first_comment_method='profile_first_post',
                  permalink_status='not_requested', telemetry={'outcome': 'published'})
    (directory / 'attempt.json').write_text(json.dumps(context))
    (directory / 'result.json').write_text(json.dumps(result))
    queue = {'daily_batches': [{'posts': [{'type': 'reel', 'executions': [execution]}]}]}
    monkeypatch.setattr(queue_service, 'QUEUE_FILE', tmp_path / 'queue.json')
    assert queue_service.save_posting_queue(queue)
    monkeypatch.setattr(svc, 'PROFILES_DIR', profiles)
    monkeypatch.setattr(svc, 'active_automation_tasks', {})
    monkeypatch.setattr(svc, '_queue_execution_tasks', {})
    monkeypatch.setattr(svc, 'active_in_memory_scheduler_leases', lambda: [])
    monkeypatch.setattr(svc, 'load_posting_queue', queue_service.load_posting_queue)
    monkeypatch.setattr(svc, 'save_posting_queue', queue_service.save_posting_queue)
    return profiles, directory, execution, result


def persisted():
    return queue_service.find_queue_execution(queue_service.load_posting_queue(), 'exec-4')['execution']


def test_stale_publisher_restores_completed_result_and_comment(saved_attempt):
    svc.recover_stale_queue_executions()
    execution = persisted()
    assert execution['status'] == execution['stage'] == 'published'
    assert execution['scheduler_lease'] is None
    assert execution['last_scheduler_lease']['lease_id'] == 'lease-4'
    assert execution['first_comment_status'] == 'submitted_verified'
    assert execution['first_comment_verified_at'] == execution['published_at'] == execution['ended_at']
    assert execution['error'] is None
    assert execution['stage_history'][-1]['reason'] == 'completed_publication_recovered'
    svc.recover_stale_queue_executions()
    assert persisted() == execution  # Idempotent; no rerun or duplicate history.


def test_already_misclassified_restart_is_repaired(saved_attempt):
    _, _, execution, _ = saved_attempt
    queue = queue_service.load_posting_queue()
    saved = queue_service.find_queue_execution(queue, 'exec-4')['execution']
    saved.update(status='uncertain', stage='uncertain', last_scheduler_lease=saved.pop('scheduler_lease'))
    saved['stage_history'].append({'stage': 'uncertain', 'reason': 'manager_restart_recovery'})
    queue_service.save_posting_queue(queue)
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'published'


@pytest.mark.parametrize('field', ['execution_id', 'lease_id', 'owner_token'])
def test_wrong_attempt_never_restores_success(saved_attempt, field):
    profiles, directory, execution, _ = saved_attempt
    context = json.loads((directory / 'attempt.json').read_text())
    context[field] = 'other'
    (directory / 'attempt.json').write_text(json.dumps(context))
    before = deepcopy(execution)
    assert not recover_completed_publication(profiles, execution, 'reel')
    assert execution == before


@pytest.mark.parametrize('contents', ['{partial', '[]', '{}', 'null'])
def test_corrupt_result_preserves_uncertainty(saved_attempt, contents):
    _, directory, _, _ = saved_attempt
    (directory / 'result.json').write_text(contents)
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'uncertain'


@pytest.mark.parametrize('changes', [
    {'status': 'uncertain'}, {'status': 'failed_after_publish'}, {'status': 'pending_profile_verification'},
    {'run_id': 'older_run'}, {'finished_at': None}, {'finished_at': 'invalid'},
    {'finished_at': '2026-01-01T00:02:00'}, {'finished_at': '2025-01-01T00:02:00+00:00'},
    {'finished_at': '2099-01-01T00:02:00+00:00'}, {'error': 'unconfirmed'},
    {'publication_status': 'uncertain'}, {'publication_status': []},
])
def test_only_valid_terminal_success_is_recovered(saved_attempt, changes):
    profiles, directory, execution, result = saved_attempt
    result.update(changes)
    (directory / 'result.json').write_text(json.dumps(result))
    assert not recover_completed_publication(profiles, execution, 'reel')


@pytest.mark.parametrize('changes', [
    {'cloud_attempt_id': 'cloud-1'}, {'review_status': 'resolved_not_published'},
    {'cancel_requested_at': 'now'}, {'container_owner_token': None},
    {'scheduler_lease': {'kind': 'preparer', 'lease_id': 'lease-4'}},
    {'status': 'cancelled'}, {'status': 'pending'}, {'status': 'published'},
])
def test_recovery_does_not_override_other_authority(saved_attempt, changes):
    profiles, _, execution, _ = saved_attempt
    execution.update(changes)
    assert not recover_completed_publication(profiles, execution, 'reel')


def test_newer_claim_rejects_old_matching_evidence(saved_attempt):
    profiles, _, execution, _ = saved_attempt
    execution['stage_history'].append({'reason': 'publisher_scheduler_lease_claimed', 'lease_id': 'newer'})
    assert not recover_completed_publication(profiles, execution, 'reel')


@pytest.mark.parametrize('file', ['attempt.json', 'result.json'])
def test_missing_or_symlinked_proof_is_rejected(saved_attempt, file):
    profiles, directory, execution, _ = saved_attempt
    path = directory / file
    content = path.read_text()
    path.unlink()
    assert not recover_completed_publication(profiles, execution, 'reel')
    other = directory.parent / ('other-' + file)
    other.write_text(content)
    path.symlink_to(other)
    assert not recover_completed_publication(profiles, execution, 'reel')


def test_missing_directory_hint_uses_exact_attempt_not_latest(saved_attempt):
    profiles, _, execution, _ = saved_attempt
    execution.pop('evidence_dir')
    assert recover_completed_publication(profiles, execution, 'reel')


def test_live_attempt_is_not_reconciled_or_released(saved_attempt, monkeypatch):
    monkeypatch.setattr(svc, 'queue_attempt_still_active', lambda execution: True)
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'running'
    assert persisted()['scheduler_lease']['lease_id'] == 'lease-4'


def test_recovered_publication_does_not_invent_verified_comment(saved_attempt):
    profiles, directory, execution, result = saved_attempt
    result['first_comment'] = 'submitted_unverified'
    (directory / 'result.json').write_text(json.dumps(result))
    assert recover_completed_publication(profiles, execution, 'reel')
    assert execution['first_comment_status'] == 'submitted_unverified'
    assert not execution.get('first_comment_verified_at')


def test_ambiguous_attempt_directories_are_not_guessed(saved_attempt):
    profiles, directory, execution, _ = saved_attempt
    import shutil
    shutil.copytree(directory, directory.with_name('other_FacebookReelTask'))
    execution.pop('evidence_dir')
    assert not recover_completed_publication(profiles, execution, 'reel')


def test_directory_hint_cannot_point_to_another_profile(saved_attempt):
    profiles, directory, execution, _ = saved_attempt
    import shutil
    other = profiles / 'other-profile' / 'automation_evidence' / directory.name
    shutil.copytree(directory, other)
    execution['evidence_dir'] = str(other)
    assert not recover_completed_publication(profiles, execution, 'reel')


def test_generic_uncertain_outcome_is_not_auto_reviewed(saved_attempt):
    profiles, _, execution, _ = saved_attempt
    execution.update(status='uncertain', stage='uncertain', last_scheduler_lease=execution.pop('scheduler_lease'))
    execution['stage_history'].append({'reason': 'publication_verification_unconfirmed'})
    assert not recover_completed_publication(profiles, execution, 'reel')


def test_newer_comment_retry_result_is_preserved(saved_attempt):
    profiles, _, execution, _ = saved_attempt
    execution.update(comment_retry_status='completed', first_comment_status='submitted_unverified')
    assert recover_completed_publication(profiles, execution, 'reel')
    assert execution['first_comment_status'] == 'submitted_unverified'
