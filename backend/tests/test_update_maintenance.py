import pytest
import asyncio
from backend.services import container_lifecycle as lifecycle
from backend.services import automation_service as automation


@pytest.fixture(autouse=True)
def isolated_owners(monkeypatch, tmp_path):
    monkeypatch.setattr(lifecycle, 'OWNERS_FILE', tmp_path / 'owners.json')
    monkeypatch.setattr(lifecycle, 'LOCK_FILE', tmp_path / 'owners.lock')
    monkeypatch.setattr(lifecycle, 'UPDATE_MAINTENANCE', False)
    monkeypatch.setattr(lifecycle, 'SHUTTING_DOWN', False)


def test_update_barrier_blocks_all_new_execution_claims_and_can_be_released():
    assert lifecycle.set_update_maintenance(True, {})
    with pytest.raises(RuntimeError, match='App update maintenance'):
        lifecycle.acquire('profile_003', 'manual:test')
    assert not lifecycle.OWNERS_FILE.exists()
    assert not lifecycle.set_update_maintenance(False, {})
    assert lifecycle.acquire('profile_003', 'manual:test')


def test_owner_acquired_before_pause_prevents_installation():
    lifecycle.acquire('profile_003', 'cloud-or-desktop')
    with pytest.raises(RuntimeError, match='Wait for active'):
        lifecycle.set_update_maintenance(True, {})
    assert not lifecycle.UPDATE_MAINTENANCE


@pytest.mark.parametrize('record', [{'status':'running'}, {'status':'preparing'},
                                  {'status':'publishing'}, {'status':'completed','lifecycle_active':True}])
def test_active_or_cleanup_work_prevents_pause(record):
    with pytest.raises(RuntimeError, match='Wait for active'):
        lifecycle.set_update_maintenance(True, {'profile_003':record})
    assert not lifecycle.UPDATE_MAINTENANCE


def test_dispatch_during_update_does_not_claim_or_mutate_queue(monkeypatch):
    monkeypatch.setattr(lifecycle, 'UPDATE_MAINTENANCE', True)
    def forbidden():
        pytest.fail('paused dispatch read/modified the queue')
    monkeypatch.setattr(automation, 'load_posting_queue', forbidden)
    asyncio.run(automation.dispatch_pending_queue())
    result = asyncio.run(automation._run_dispatched_queue_task('execution', 'publisher'))
    assert result['status'] == 'deferred'
