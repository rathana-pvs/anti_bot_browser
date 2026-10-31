"""Preparation completion must survive lost stdout and slow worker shutdown."""
import asyncio
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from automation.preparation_result import read_result, result_path, write_result
from backend.services import automation_service as svc, queue_service
from backend.services.queue_safety import classify_interrupted_execution


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    execution = {
        'execution_id': 'exec-12', 'profile_id': 'profile-12',
        'status': 'preparing', 'stage': 'completed', 'preparation_mode': 'brief',
        'preparation_status': 'pending', 'container_owner_token': 'owner-12',
        'stage_history': [{'stage': 'preparing'}, {'stage': 'ready'}, {'stage': 'completed'}],
        'scheduler_lease': {'lease_id': 'lease-12', 'kind': 'preparer',
                            'expires_at': '2026-01-01T00:00:00+00:00'},
    }
    queue = {'daily_batches': [{'posts': [{'type': 'reel', 'executions': [execution]}]}]}
    monkeypatch.setattr(queue_service, 'QUEUE_FILE', tmp_path / 'queue.json')
    assert queue_service.save_posting_queue(queue)
    monkeypatch.setattr(svc, 'load_posting_queue', queue_service.load_posting_queue)
    monkeypatch.setattr(svc, 'save_posting_queue', queue_service.save_posting_queue)
    monkeypatch.setattr(svc, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(svc, 'active_automation_tasks', {})
    monkeypatch.setattr(svc.lifecycle, 'snapshot', lambda: {})
    monkeypatch.setattr(svc, 'get_container_status', Mock(return_value='running'))
    return execution


def persisted():
    return queue_service.find_queue_execution(queue_service.load_posting_queue(), 'exec-12')['execution']


def completed_result():
    return {'status': 'completed', 'success': True, 'error': None,
            'telemetry': {'outcome': 'completed'}, 'evidence_dir': '/local/preparation/evidence'}


def live_handoff(execution):
    return dict(state='handoff', token=execution['container_owner_token'],
                execution_id=execution['execution_id'], manager_id=svc.MANAGER_INSTANCE_ID,
                heartbeat_expires_at=time.time() + 60, hard_deadline_at=time.time() + 600)


@pytest.mark.parametrize('usable', [True, False])
def test_saved_result_recovers_only_with_usable_fenced_handoff(attempt, monkeypatch, usable):
    context = svc.preparation_result_context(attempt)
    write_result(svc.PROFILES_DIR, context, completed_result())
    monkeypatch.setattr(svc.lifecycle, 'snapshot', lambda: {'profile-12': live_handoff(attempt)})
    svc.get_container_status.return_value = 'running' if usable else 'stopped'
    svc.recover_stale_queue_executions()
    result = persisted()
    assert result['status'] == result['preparation_status'] == ('ready' if usable else 'pending')
    assert result['scheduler_lease'] is None
    assert result['error'] is None
    assert result['preparation_evidence_dir'] == '/local/preparation/evidence'


@pytest.mark.parametrize('bad_context', ['lease_id', 'execution_id', 'owner_token', 'profile_id'])
def test_older_or_wrong_attempt_result_cannot_restore_readiness(attempt, monkeypatch, bad_context):
    context = svc.preparation_result_context(attempt)
    wrong = dict(context, **{bad_context: 'another-attempt'})
    write_result(svc.PROFILES_DIR, wrong, completed_result())
    monkeypatch.setattr(svc.lifecycle, 'snapshot', lambda: {'profile-12': live_handoff(attempt)})
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'pending'
    assert persisted()['preparation_status'] == 'pending'


@pytest.mark.parametrize('contents', ['{broken', '[]', '{"result": {"status": "completed"}}'])
def test_missing_or_corrupt_durable_result_requires_preparation_again(attempt, contents):
    context = svc.preparation_result_context(attempt)
    path = result_path(svc.PROFILES_DIR, context['profile_id'], context['lease_id'])
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    assert read_result(svc.PROFILES_DIR, context) is None
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'pending'


def test_account_12_completed_preparer_without_new_result_is_not_uncertain(attempt):
    assert classify_interrupted_execution(attempt)['status'] == 'failed_before_publish'
    svc.recover_stale_queue_executions()
    result = persisted()
    assert result['status'] == result['preparation_status'] == 'pending'
    assert result['stage_history'][-1]['reason'] == 'preparation_retry_required'


@pytest.mark.parametrize('stage', ['publish_authorized', 'publish_clicked', 'verifying', 'pending_profile_verification'])
def test_publish_evidence_always_preserves_uncertainty(attempt, stage):
    queue = queue_service.load_posting_queue()
    execution = queue_service.find_queue_execution(queue, 'exec-12')['execution']
    execution['stage_history'].insert(1, {'stage': stage})
    assert queue_service.save_posting_queue(queue)
    write_result(svc.PROFILES_DIR, svc.preparation_result_context(attempt), completed_result())
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'uncertain'


def test_publisher_completed_stage_is_not_treated_as_preparation(attempt):
    attempt['scheduler_lease']['kind'] = 'publisher'
    assert classify_interrupted_execution(attempt)['status'] == 'uncertain'


def test_reviewed_old_click_does_not_block_new_preparation(attempt):
    attempt['stage_history'] = [
        {'stage': 'publish_clicked'},
        {'stage': 'failed_before_publish', 'reason': 'operator_resolved_not_published'},
        {'stage': 'preparing'}, {'stage': 'completed'},
    ]
    attempt['last_active_stage'] = 'publish_clicked'
    assert classify_interrupted_execution(attempt)['status'] == 'failed_before_publish'


@pytest.mark.parametrize('live_worker', [False, True])
def test_manager_interrupted_after_ready_commit_rechecks_container(attempt, monkeypatch, live_worker):
    assert queue_service.complete_queue_preparation('exec-12', 'lease-12', completed_result())
    if live_worker:
        monkeypatch.setattr(svc, 'active_automation_tasks', {
            'profile-12': {'queue_execution_id': 'exec-12', 'status': 'running', 'lifecycle_active': True},
        })
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == ('ready' if live_worker else 'pending')


def test_expired_or_wrong_container_handoff_requires_preparation_again(attempt, monkeypatch):
    context = svc.preparation_result_context(attempt)
    write_result(svc.PROFILES_DIR, context, completed_result())
    owner = live_handoff(attempt)
    owner['token'] = 'different-owner'
    monkeypatch.setattr(svc.lifecycle, 'snapshot', lambda: {'profile-12': owner})
    svc.recover_stale_queue_executions()
    assert persisted()['status'] == 'pending'


def test_completion_is_atomic_and_cannot_overwrite_newer_claim(attempt):
    assert not queue_service.complete_queue_preparation('exec-12', 'older-lease', completed_result())
    assert persisted()['status'] == 'preparing'
    assert queue_service.complete_queue_preparation('exec-12', 'lease-12', completed_result())
    result = persisted()
    assert result['status'] == result['preparation_status'] == 'ready'
    assert result['scheduler_lease'] is None
    assert result['last_scheduler_lease']['lease_id'] == 'lease-12'
    # Late stage output from that worker cannot regress the committed result.
    assert not queue_service.persist_queue_execution_stage('exec-12', 'completed', lease_id='lease-12')
    assert persisted()['stage'] == 'ready'


@pytest.mark.parametrize('close_pipes', [False, True])
def test_completed_result_is_committed_before_slow_worker_exit(attempt, monkeypatch, close_pipes):
    monkeypatch.setattr(svc, 'claim_queue_execution', Mock(return_value={
        'targetExec': attempt, 'leaseId': 'lease-12', 'lease': attempt['scheduler_lease'],
    }))
    monkeypatch.setattr(svc, 'ensure_profile_container_ready', AsyncMock(return_value={'started': True}))
    monkeypatch.setattr(svc, 'automation_worker_env', lambda: {})
    monkeypatch.setattr(svc, '_delayed_dispatch', AsyncMock())
    monkeypatch.setattr(svc, 'PREPARATION_EXIT_TIMEOUT', .05)
    spawn_real = asyncio.create_subprocess_exec
    workers = []
    committed_while_alive = []
    commit_real = svc.complete_queue_preparation

    def commit(*args):
        committed_while_alive.append(workers[0].returncode is None)
        return commit_real(*args)

    async def spawn(*args, **kwargs):
        assert json.loads(kwargs['env']['AUTOMATION_PREPARATION_CONTEXT']) == svc.preparation_result_context(attempt)
        script = 'import time, os; print(' + repr(json.dumps(completed_result())) + ', flush=True); '
        if close_pipes:
            script += 'os.close(1); os.close(2); '
        script += 'time.sleep(60)'
        proc = await spawn_real(sys.executable, '-u', '-c', script, stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE)
        workers.append(proc)
        return proc

    monkeypatch.setattr(svc.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(svc, 'complete_queue_preparation', commit)
    result = asyncio.run(svc._execute_queue_preparation_inner('exec-12'))
    assert result['success'] is True
    assert committed_while_alive == [True]
    assert workers[0].returncode is not None
    assert persisted()['status'] == 'ready'


def test_cancel_during_early_ready_handoff_does_not_leave_runnable_work(attempt, monkeypatch):
    monkeypatch.setattr(svc, '_queue_execution_tasks', {})
    monkeypatch.setattr(svc, '_terminate_owned_worker', AsyncMock())
    monkeypatch.setattr(svc.lifecycle, 'release', AsyncMock())

    async def cancel_after_result(execution_id):
        queue = queue_service.load_posting_queue()
        execution = queue_service.find_queue_execution(queue, execution_id)['execution']
        execution['container_owner_token'] = svc.lifecycle.OWNER_TOKEN.get()
        assert queue_service.save_posting_queue(queue)
        assert queue_service.complete_queue_preparation(execution_id, 'lease-12', completed_result())
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(svc._run_owned_queue('exec-12', cancel_after_result, preparation=True))
    assert persisted()['status'] == 'cancelled'
    assert not svc._queue_execution_tasks


def test_failed_preparation_keeps_screenshot_directory_and_logs(attempt, monkeypatch):
    result = dict(status='failed_before_publish', success=False,
                  error='No ready surface after two attempts',
                  evidence_dir='/local/failed-preparation', logs=['Both readiness attempts failed'])
    monkeypatch.setattr(svc, 'claim_queue_execution', Mock(return_value={
        'targetExec': attempt, 'leaseId': 'lease-12', 'lease': attempt['scheduler_lease'],
    }))
    monkeypatch.setattr(svc, 'ensure_profile_container_ready', AsyncMock(return_value={'started': True}))
    monkeypatch.setattr(svc, 'automation_worker_env', lambda: {})
    monkeypatch.setattr(svc, '_delayed_dispatch', AsyncMock())
    monkeypatch.setattr(svc, 'stop_profile_container_after_report', AsyncMock())
    proc = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[json.dumps(result).encode(), b''])),
        stderr=SimpleNamespace(readline=AsyncMock(return_value=b'')),
        wait=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(svc.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
    assert asyncio.run(svc._execute_queue_preparation_inner('exec-12'))['success'] is False
    execution = persisted()
    assert execution['status'] == 'failed_before_publish'
    assert execution['evidence_dir'] == result['evidence_dir']
    assert execution['logs'] == result['logs']


def test_an_interrupted_warming_visit_is_stopped_not_uncertain():
    from backend.services.queue_safety import repair_interrupted_warming
    visit = {'status': 'running', 'stage': 'warming', 'stage_history': [{'stage': 'warming'}]}
    assert classify_interrupted_execution(visit)['status'] == 'stopped'
    old = {'status': 'uncertain', 'stage': 'uncertain', 'error': 'publication outcome requires review',
           'stage_history': [{'stage': 'warming'}, {'stage': 'uncertain', 'reason': 'manager_restart_recovery',
                                                    'interrupted_stage': 'warming'}]}
    assert repair_interrupted_warming(old) and old['status'] == 'stopped'
    assert not repair_interrupted_warming(old)
    post = {'status': 'uncertain', 'stage_history': [{'reason': 'manager_restart_recovery', 'interrupted_stage': 'running'}]}
    assert not repair_interrupted_warming(post)
