"""Ownership races, deadlines, cancellation and recovery regressions."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from backend.services import automation_service as svc, container_lifecycle as life

@pytest.fixture(autouse=True)
def isolated_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(life, 'OWNERS_FILE', tmp_path / 'owners.json')
    monkeypatch.setattr(life, 'LOCK_FILE', tmp_path / 'owners.lock')
    monkeypatch.setattr(svc, 'active_automation_tasks', {})

def own(execution='exec-1'):
    token = life.acquire('profile-1', execution)
    life.mark_used('profile-1', token)
    return token

def test_stop_retries_transient_failures(monkeypatch):
    monkeypatch.setattr(svc, 'get_container_status', lambda _, **kwargs: 'stopped' if len(attempts) >= 3 else 'running')
    attempts = []
    async def action(*args):
        attempts.append(args)
        if len(attempts) < 3:
            raise RuntimeError('temporary failure')
    monkeypatch.setattr(svc, 'run_profile_lifecycle_action', action)
    monkeypatch.setattr(svc.asyncio, 'sleep', AsyncMock())
    result = asyncio.run(svc._stop_container_with_retries('profile-1'))
    assert result['attempts'] == 3

def mock_queue(monkeypatch):
    monkeypatch.setattr(svc, 'load_posting_queue', lambda: {})
    monkeypatch.setattr(svc, 'find_queue_execution', lambda *args: {'execution': {'profile_id': 'profile-1'}})

@pytest.mark.parametrize('failure', [RuntimeError('launch failed'), asyncio.CancelledError()])
def test_used_container_released_on_exception_or_cancellation(monkeypatch, failure):
    mock_queue(monkeypatch)
    async def action(*args):
        own_token = life.OWNER_TOKEN.get()
        life.acquire('profile-1', 'exec-1', own_token)
        life.mark_used('profile-1', own_token)
        raise failure
    stop = AsyncMock(return_value={'stopped': True})
    monkeypatch.setattr(svc, '_execute_queue_item_inner', action)
    monkeypatch.setattr(svc, '_stop_container_with_retries', stop)
    with pytest.raises(type(failure)):
        asyncio.run(svc.execute_queue_item('exec-1'))
    stop.assert_awaited_once_with('profile-1')
    assert life.snapshot() == {}

def test_rejected_claim_never_stops_other_owner(monkeypatch):
    mock_queue(monkeypatch)
    token = own('other-task')
    async def reject(*args):
        life.acquire('profile-1', 'exec-1', life.OWNER_TOKEN.get())
    stop = AsyncMock()
    monkeypatch.setattr(svc, '_execute_queue_item_inner', reject)
    monkeypatch.setattr(svc, '_stop_container_with_retries', stop)
    with pytest.raises(RuntimeError, match='container_owned'):
        asyncio.run(svc.execute_queue_item('exec-1'))
    stop.assert_not_awaited()
    assert life.snapshot()['profile-1']['token'] == token

def test_old_token_cannot_release_transferred_handoff():
    old = own()
    assert life.handoff('profile-1', old)
    new = life.acquire('profile-1', 'exec-1')
    stop = AsyncMock()
    result = asyncio.run(life.release('profile-1', old, stop))
    assert result['reason'] == 'ownership_changed'
    stop.assert_not_awaited()
    assert life.snapshot()['profile-1']['token'] == new

def test_profile_cannot_be_acquired_while_stop_awaits():
    token = own()
    async def stop(_):
        with pytest.raises(RuntimeError, match='container_owned'):
            life.acquire('profile-1', 'other-execution')
        return {'stopped': True}
    asyncio.run(life.release('profile-1', token, stop))

def test_failed_cleanup_remains_persisted_and_watchdog_retries():
    token = own()
    with pytest.raises(RuntimeError):
        asyncio.run(life.release('profile-1', token, AsyncMock(side_effect=RuntimeError('docker down'))))
    assert life.snapshot()['profile-1']['state'] == 'cleanup_failed'
    stop = AsyncMock(return_value={'stopped': True})
    asyncio.run(life.recover(stop, AsyncMock()))
    stop.assert_awaited_once()
    assert life.snapshot() == {}

def test_manual_owner_recovered_after_manager_restart(monkeypatch):
    token = own('manual:run')
    owners = life.snapshot()
    owners['profile-1']['backend_pid'] = 999999999
    life._write(owners)
    monkeypatch.setattr(life, 'MANAGER_INSTANCE_ID', 'new-manager')
    terminate = AsyncMock()
    stop = AsyncMock(return_value={'stopped': True})
    asyncio.run(life.recover(stop, terminate))
    terminate.assert_awaited_once_with('profile-1', token)
    stop.assert_awaited_once()

def test_handoff_expires_and_does_not_block_container(monkeypatch):
    token = own()
    life.handoff('profile-1', token)
    current = life.time.time()
    monkeypatch.setattr(life.time, 'time', lambda: current + 121)
    stop = AsyncMock(return_value={'stopped': True})
    asyncio.run(life.recover(stop, AsyncMock()))
    stop.assert_awaited_once()
    assert life.snapshot() == {}

def test_logs_and_heartbeats_cannot_extend_absolute_deadline(monkeypatch):
    token = own()
    deadline = life.snapshot()['profile-1']['hard_deadline_at']
    monkeypatch.setattr(life.time, 'time', lambda: deadline + 1)
    assert not life.heartbeat('profile-1', token)
    stop = AsyncMock(return_value={'stopped': True})
    asyncio.run(life.recover(stop, AsyncMock()))
    stop.assert_awaited_once()

def test_valid_owner_is_preserved():
    own()
    stop = AsyncMock()
    asyncio.run(life.recover(stop, AsyncMock()))
    stop.assert_not_awaited()

def test_unused_claim_releases_without_stop():
    token = life.acquire('profile-1', 'never-started')
    stop = AsyncMock()
    asyncio.run(life.release('profile-1', token, stop))
    stop.assert_not_awaited()
    assert life.snapshot() == {}

def test_manual_launch_failure_cleans_persisted_owner(monkeypatch):
    token = life.acquire('profile-1', 'manual:run')
    record = {'profile_id': 'profile-1', 'container_owner_token': token,
              'status': 'running', 'logs': [], 'process': None}
    monkeypatch.setattr(svc.asyncio, 'create_subprocess_exec', AsyncMock(side_effect=OSError('spawn failed')))
    stop = AsyncMock(return_value={'stopped': True})
    monkeypatch.setattr(svc, '_stop_container_with_retries', stop)
    async def run():
        context = life.OWNER_TOKEN.set(token)
        try:
            await svc._run_manual_subprocess('profile-1', [], record)
        finally:
            life.OWNER_TOKEN.reset(context)
    asyncio.run(run())
    stop.assert_awaited_once()
    assert record['status'] == 'failed'
    assert life.snapshot() == {}

def test_docker_status_error_is_not_reported_as_stopped(monkeypatch):
    monkeypatch.setattr(svc, 'get_container_status', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('Docker unavailable')))
    monkeypatch.setattr(svc.asyncio, 'sleep', AsyncMock())
    with pytest.raises(RuntimeError, match='Docker unavailable'):
        asyncio.run(svc._stop_container_with_retries('profile-1'))

def test_preparation_cancellation_cleans_owned_container(monkeypatch):
    mock_queue(monkeypatch)
    async def cancel(*args):
        token = life.OWNER_TOKEN.get()
        life.acquire('profile-1', 'exec-1', token)
        life.mark_used('profile-1', token)
        raise asyncio.CancelledError()
    stop = AsyncMock(return_value={'stopped': True})
    monkeypatch.setattr(svc, '_execute_queue_preparation_inner', cancel)
    monkeypatch.setattr(svc, '_stop_container_with_retries', stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(svc.execute_queue_preparation('exec-1'))
    stop.assert_awaited_once()

def test_shutdown_stops_valid_owner_and_blocks_new_claims(monkeypatch):
    own()
    monkeypatch.setattr(life, 'SHUTTING_DOWN', True)
    with pytest.raises(RuntimeError, match='shutting down'):
        life.acquire('profile-2', 'new')
    stop = AsyncMock(return_value={'stopped': True})
    asyncio.run(life.recover(stop, AsyncMock(), force=True))
    stop.assert_awaited_once()
    assert life.snapshot() == {}

def test_corrupt_registry_fails_closed():
    life.OWNERS_FILE.write_text('{broken')
    with pytest.raises(ValueError):
        life.acquire('profile-1', 'exec-1')

def test_concurrent_profile_claims_have_one_winner():
    from concurrent.futures import ThreadPoolExecutor
    def claim(index):
        try:
            return life.acquire('profile-1', f'exec-{index}')
        except RuntimeError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(16)))
    assert sum(token is not None for token in results) == 1
    assert len(life.snapshot()) == 1

def test_recovery_preserves_another_live_manager(monkeypatch):
    own()
    monkeypatch.setattr(life, 'MANAGER_INSTANCE_ID', 'second-manager')
    stop = AsyncMock()
    asyncio.run(life.recover(stop, AsyncMock()))
    stop.assert_not_awaited()

def test_legacy_migration_happens_only_once():
    execution = {'profile_id': 'profile-1', 'execution_id': 'old-run',
                 'automation_container_owned': True}
    queue = {'daily_batches': [{'posts': [{'executions': [execution]}]}]}
    life.import_legacy_owners(queue)
    assert execution['container_owner_schema'] == 1
    stop = AsyncMock(return_value={'stopped': True})
    asyncio.run(life.recover(stop, AsyncMock()))
    life.import_legacy_owners(queue)
    assert life.snapshot() == {}
