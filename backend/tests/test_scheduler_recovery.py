"""An expired scheduler lease must not detach a still-running local worker."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.services import automation_service as svc, queue_service
from backend.services.queue_scheduler import DEFAULT_SCHEDULER_CONFIG, is_lease_active


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    execution = {
        'execution_id': 'exec-13', 'profile_id': 'profile-13',
        'status': 'running', 'stage': 'composing', 'container_owner_token': 'owner-13',
        'scheduler_lease': {'lease_id': 'lease-13', 'kind': 'publisher',
                            'expires_at': '2026-01-01T00:00:00+00:00'},
    }
    queue = {'daily_batches': [{'posts': [{'type': 'reel', 'executions': [execution]}]}]}
    monkeypatch.setattr(queue_service, 'QUEUE_FILE', tmp_path / 'queue.json')
    assert queue_service.save_posting_queue(queue)
    monkeypatch.setattr(svc, 'load_posting_queue', queue_service.load_posting_queue)
    monkeypatch.setattr(svc, 'save_posting_queue', queue_service.save_posting_queue)
    monkeypatch.setattr(svc, 'active_automation_tasks', {})
    monkeypatch.setattr(svc, '_queue_execution_tasks', {})
    monkeypatch.setattr(svc, 'get_scheduler_config', lambda: DEFAULT_SCHEDULER_CONFIG)
    monkeypatch.setattr(svc.lifecycle, 'heartbeat', Mock(return_value=True))
    return execution


def worker(execution, **changes):
    return dict(
        profile_id=execution['profile_id'], queue_execution_id=execution['execution_id'],
        container_owner_token=execution['container_owner_token'],
        scheduler_lease=deepcopy(execution['scheduler_lease']), status='running',
        current_stage='composing', result=None,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
        process=SimpleNamespace(returncode=None, terminate=Mock()),
        **changes,
    )


def persisted():
    return queue_service.find_queue_execution(queue_service.load_posting_queue(), 'exec-13')['execution']


@pytest.mark.parametrize('status', ['running', 'stale'])
def test_expired_lease_does_not_recover_worker_that_is_still_alive(attempt, status):
    record = worker(attempt)
    record['status'] = status
    svc.active_automation_tasks['profile-13'] = record
    svc.recover_stale_queue_executions('dispatcher_stale_lease')
    assert persisted() == attempt
    # The original attempt can still persist publication progress/results.
    assert queue_service.persist_queue_execution_stage('exec-13', 'publish_clicked', lease_id='lease-13')


def test_pending_result_drain_keeps_attempt_fenced_after_process_exit(attempt):
    record = worker(attempt)
    record['process'].returncode = 0
    svc.active_automation_tasks['profile-13'] = record
    svc._queue_execution_tasks['exec-13'] = Mock(done=Mock(return_value=False))
    svc.recover_stale_queue_executions()
    assert persisted() == attempt


@pytest.mark.parametrize('field', ['queue_execution_id', 'container_owner_token', 'scheduler_lease'])
def test_another_attempt_cannot_protect_an_expired_lease(attempt, field):
    record = worker(attempt)
    record[field] = {'lease_id': 'another'} if field == 'scheduler_lease' else 'another'
    svc.active_automation_tasks['profile-13'] = record
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'failed_before_publish'
    assert persisted()['scheduler_lease'] is None


@pytest.mark.parametrize('stage,expected', [('composing', 'failed_before_publish'),
                                           ('publish_clicked', 'uncertain')])
def test_dead_worker_is_recovered_and_publish_uncertainty_is_preserved(attempt, stage, expected):
    queue = queue_service.load_posting_queue()
    queue_service.find_queue_execution(queue, 'exec-13')['execution']['stage'] = stage
    queue_service.save_posting_queue(queue)
    record = worker(attempt)
    record['process'].returncode = 1
    svc.active_automation_tasks['profile-13'] = record
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == expected


def test_null_lease_in_one_live_task_does_not_block_other_heartbeats(attempt):
    svc.active_automation_tasks['other'] = {
        'status': 'running', 'lifecycle_active': True, 'scheduler_lease': None,
    }
    record = worker(attempt)
    record['current_stage'] = None  # result=None is also normal before completion.
    svc.active_automation_tasks['profile-13'] = record
    svc.heartbeat_scheduler_leases()
    assert persisted()['status'] == 'running'
    assert is_lease_active(persisted()['scheduler_lease'])
    assert record['scheduler_lease'] == persisted()['scheduler_lease']


def test_ownership_loss_terminates_worker_before_recovery_can_release_attempt(attempt):
    record = worker(attempt)
    svc.active_automation_tasks['profile-13'] = record
    svc.lifecycle.heartbeat.return_value = False
    svc.heartbeat_scheduler_leases()
    record['process'].terminate.assert_called_once()
    assert record['status'] == 'stale'
    assert persisted()['status'] == 'running'
    record['process'].returncode = -15
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'failed_before_publish'
