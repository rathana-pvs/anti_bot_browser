"""Local queue and service safety checks using isolated temporary data."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend import runtime_service
from backend.routers import queue as queue_router
from backend.services import automation_service, queue_service
from backend.worker import adapter, service
from backend.worker.adapter import QueueAdapter
from backend.worker.config import WorkerConfig
from automation.engine.cloud_publish_guard import record_cloud_publish_intent


def cfg():
    return WorkerConfig(server_url='wss://server.example/ws', worker_id='worker-1',
                        credential='secret', account_profiles={'account-1': 'profile-1'})


@pytest.fixture
def queue_adapter(tmp_path, monkeypatch):
    profile = tmp_path / 'profiles' / 'profile-1'
    profile.mkdir(parents=True)
    (profile / 'config.json').write_text('{}')
    monkeypatch.setattr(adapter, 'PROFILES_DIR', profile.parent)
    monkeypatch.setattr(queue_service, 'QUEUE_FILE', tmp_path / 'queue.json')
    monkeypatch.setattr(queue_service, 'QUEUE_CLAIM_LOCK_FILE', tmp_path / 'queue.lock')
    return QueueAdapter(cfg())


def attempt():
    return {'id': 'attempt-1', 'local_id': 'cloud_' + 'a' * 64,
            'job': json.dumps({'account_id': 'account-1', 'task': 'photo', 'caption': 'Hi', 'first_comment': None})}


def test_submission_is_idempotent_and_enters_existing_scheduler(queue_adapter):
    queue_adapter.submit(attempt(), 'worker/photo.png')
    queue_adapter.submit(attempt(), 'worker/photo.png')
    queue = queue_service.load_posting_queue()
    assert len(queue['daily_batches']) == 1
    execution = queue_adapter.snapshot(attempt()['local_id'])
    assert execution['status'] == 'pending'
    assert execution['cloud_attempt_id'] == 'attempt-1'
    assert execution['profile_id'] == 'profile-1'
    assert queue['daily_batches'][0]['posts'][0]['media_file'] == 'worker/photo.png'


def test_unresolved_local_outcome_blocks_new_cloud_attempt(queue_adapter):
    queue_adapter.submit(attempt(), 'photo.png')
    queue = queue_service.load_posting_queue()
    queue['daily_batches'][0]['posts'][0]['executions'][0]['status'] = 'uncertain'
    assert queue_service.save_posting_queue(queue)
    second = {**attempt(), 'id': 'attempt-2', 'local_id': 'cloud_' + 'b' * 64}
    with pytest.raises(ValueError, match='unresolved'):
        queue_adapter.submit(second, 'photo.png')
    assert len(queue_service.load_posting_queue()['daily_batches']) == 1


def test_safe_queue_cancellation_and_claimed_work_rejection(queue_adapter):
    queue_adapter.submit(attempt(), 'photo.png')
    queue = queue_service.load_posting_queue()
    execution = queue['daily_batches'][0]['posts'][0]['executions'][0]
    execution.update(status='running', scheduler_lease={'lease_id': 'local-lease'})
    assert queue_service.save_posting_queue(queue)
    assert queue_adapter.cancel(attempt()['local_id']) == 'too_late'
    execution.update(status='pending', scheduler_lease=None)
    assert queue_service.save_posting_queue(queue)
    assert queue_adapter.cancel(attempt()['local_id']) == 'cancelled'
    assert queue_adapter.cancel(attempt()['local_id']) == 'cancelled'


def test_evidence_cannot_escape_profile_or_include_browser_data(queue_adapter, tmp_path):
    queue_adapter.submit(attempt(), 'photo.png')
    directory = tmp_path / 'profiles' / 'profile-1' / 'automation_evidence' / 'run-1'
    directory.mkdir(parents=True)
    (directory / 'screen.png').write_bytes(b'fake-png')
    (directory / 'result.json').write_text('{}')
    secret = tmp_path / 'cookie.png'
    secret.write_bytes(b'secret')
    (directory / 'linked.png').symlink_to(secret)
    queue = queue_service.load_posting_queue()
    execution = queue['daily_batches'][0]['posts'][0]['executions'][0]
    execution['evidence_dir'] = str(directory)
    queue_service.save_posting_queue(queue)
    assert queue_adapter.evidence_file(attempt(), 'screen.png').name == 'screen.png'
    for name in ['../cookie.png', 'result.json', 'linked.png']:
        with pytest.raises(ValueError):
            queue_adapter.evidence_file(attempt(), name)
    execution['evidence_dir'] = str(tmp_path)
    queue_service.save_posting_queue(queue)
    with pytest.raises(ValueError):
        queue_adapter.evidence_file(attempt(), 'cookie.png')


def test_cloud_dispatch_is_fail_closed_without_agent():
    assert not service.cloud_execution_allowed({'cloud_attempt_id': 'remote-1'})
    assert service.cloud_execution_allowed({'execution_id': 'local-1'})


def test_cloud_claim_cannot_bypass_dispatch_guard(monkeypatch):
    execution = {'execution_id': 'cloud-1', 'cloud_attempt_id': 'remote-1',
                 'status': 'pending', 'profile_id': 'profile-1'}
    monkeypatch.setattr(automation_service, 'load_posting_queue', lambda: {})
    monkeypatch.setattr(automation_service, 'find_queue_execution', lambda *_: {
        'execution': execution, 'post': {}, 'batch': {}})
    monkeypatch.setattr(automation_service, 'with_queue_claim_lock', lambda callback: callback())
    with pytest.raises(RuntimeError, match='not authorized'):
        automation_service.claim_queue_execution('cloud-1')


def test_desktop_cannot_retry_same_cloud_attempt(monkeypatch):
    monkeypatch.setattr(queue_router, 'load_posting_queue', lambda: {})
    monkeypatch.setattr(queue_router, 'find_queue_execution', lambda *_: {
        'execution': {'cloud_attempt_id': 'attempt-1', 'status': 'uncertain'}})
    with pytest.raises(HTTPException) as error:
        asyncio.run(queue_router.run_execution_now('cloud-1'))
    assert error.value.status_code == 409


def test_publish_intent_is_durable_and_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    monkeypatch.setenv('CLOUD_EXECUTION_ID', 'cloud_' + 'a' * 64)
    record_cloud_publish_intent()
    file = tmp_path / 'data' / 'worker' / 'publish-intents' / ('cloud_' + 'a' * 64 + '.json')
    assert json.loads(file.read_text())['stage'] == 'publish_clicked'
    assert file.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        record_cloud_publish_intent()


def test_publish_marker_overrides_stale_pre_publish_stage(tmp_path, monkeypatch):
    from backend import config as backend_config
    execution = {'execution_id': 'cloud_' + 'a' * 64, 'cloud_attempt_id': 'attempt-1',
                 'status': 'running', 'stage': 'composing', 'scheduler_lease': {'lease_id': 'stale'}}
    marker = tmp_path / 'worker' / 'publish-intents' / (execution['execution_id'] + '.json')
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')  # Even a partial/corrupt marker is treated as ambiguous.
    monkeypatch.setattr(backend_config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(automation_service, 'load_posting_queue', lambda: {'daily_batches': []})
    monkeypatch.setattr(automation_service, 'find_stale_running_executions', lambda *_: [execution])
    monkeypatch.setattr(automation_service, 'active_in_memory_scheduler_leases', lambda: [])
    monkeypatch.setattr(automation_service, 'save_posting_queue', Mock(return_value=True))
    automation_service.recover_stale_queue_executions()
    assert execution['status'] == 'uncertain'
    assert execution['last_active_stage'] == 'publish_clicked'


def test_only_one_backend_can_own_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_service, 'DATA_DIR', tmp_path)
    with runtime_service.exclusive_backend():
        with pytest.raises(RuntimeError, match='already owns'):
            with runtime_service.exclusive_backend():
                pass
    with runtime_service.exclusive_backend():
        pass


def test_service_descriptor_contains_only_local_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_service, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('WORKER_SERVICE_MODE', '1')
    monkeypatch.setenv('MANAGER_API_TOKEN', 'local-secret')
    monkeypatch.setenv('WORKER_CREDENTIAL', 'cloud-secret')
    monkeypatch.setenv('PORT', '32100')
    path = runtime_service.publish_session()
    value = json.loads(path.read_text())
    assert value['token'] == 'local-secret'
    assert 'cloud-secret' not in path.read_text()
    assert path.stat().st_mode & 0o077 == 0


def test_capacity_reports_existing_local_scheduler(queue_adapter, monkeypatch):
    monkeypatch.setattr(automation_service, 'current_scheduler_snapshot', lambda: {
        'active': {'publishers': 1, 'preparers': 0, 'total': 1},
        'available': {'publishers': 0, 'preparers': 1, 'total': 1},
        'config': {'max_publishers': 1, 'max_preparers': 1, 'max_total_automation_tasks': 2},
    })
    assert queue_adapter.capacity()['available']['publishers'] == 0


def test_terminal_pre_publish_failure_with_intent_requires_review(queue_adapter, tmp_path, monkeypatch):
    from backend import config as backend_config
    queue_adapter.submit(attempt(), 'photo.png')
    queue = queue_service.load_posting_queue()
    queue['daily_batches'][0]['posts'][0]['executions'][0].update(status='failed_before_publish')
    queue_service.save_posting_queue(queue)
    monkeypatch.setattr(backend_config, 'DATA_DIR', tmp_path)
    marker = tmp_path / 'worker' / 'publish-intents' / (attempt()['local_id'] + '.json')
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    assert queue_adapter.snapshot(attempt()['local_id'])['status'] == 'needs_review'


def test_worker_status_uses_authenticated_backend_event_loop(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers.worker import router
    from backend.security import DesktopSessionAuthMiddleware
    from backend.worker.agent import WorkerAgent
    from backend.worker.journal import Journal

    @asynccontextmanager
    async def lifespan(app):
        journal = Journal(tmp_path / 'status.sqlite3')
        agent = WorkerAgent(cfg(), journal, Mock(), Mock())
        monkeypatch.setattr(service, '_agent', agent)
        try:
            yield
        finally:
            journal.close()
            monkeypatch.setattr(service, '_agent', None)

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(DesktopSessionAuthMiddleware)
    app.include_router(router)
    monkeypatch.setenv('MANAGER_API_TOKEN', 'local-token')
    with TestClient(app) as client:
        assert client.get('/api/worker/status').status_code == 403
        response = client.get('/api/worker/status', headers={'X-Manager-Token': 'local-token'})
        assert response.status_code == 200
        assert response.json()['worker_id'] == 'worker-1'
        assert response.json()['enabled'] is True
        assert 'credential' not in response.json()


def test_cloud_credential_is_not_inherited_by_automation(monkeypatch):
    from backend.services.docker_service import automation_worker_env
    monkeypatch.setenv('WORKER_CREDENTIAL', 'private-cloud-secret')
    assert 'WORKER_CREDENTIAL' not in automation_worker_env()
