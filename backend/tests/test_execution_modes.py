"""Resource policies and mixed post/Live admission, without browser publication."""
import asyncio
import json

import pytest

from backend.services.adaptive_resources import AdaptiveController
from backend.services.resource_service import ResourceSettingsManager
from backend.services.queue_scheduler import (
    DEFAULT_SCHEDULER_CONFIG, can_acquire_scheduler_slot, claim_execution_lease,
    scheduler_snapshot,
)
from backend.tests.test_adaptive_resources import Clock, sample


@pytest.mark.parametrize('legacy,total,publishers', [('low', 2, 1), ('medium', 4, 2), ('high', 6, 3)])
def test_migrates_saved_fixed_modes_without_losing_other_settings(tmp_path, legacy, total, publishers):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'resource_mode': legacy, 'ocr_engine': 'easyocr', 'other': 'keep'}))
    manager = ResourceSettingsManager(path)
    assert manager.resource_mode == 'custom'
    assert manager.custom_limits == {'max_parallel': total, 'max_posts': publishers,
                                     'max_live': publishers, 'reserve_percent': 10}
    assert json.loads(path.read_text())['other'] == 'keep'
    assert ResourceSettingsManager(path).ocr_engine == 'easyocr'
    assert ResourceSettingsManager(path).custom_limits == manager.custom_limits


@pytest.mark.parametrize('payload', [
    {'mode': 'unknown'}, {'mode': 'extreme', 'max_live': True}, {'max_posts': 17},
    {'max_parallel': 2, 'max_live': 3}, {'reserve_percent': -1}, {'max_parallel': None},
])
def test_invalid_combined_update_changes_neither_saved_nor_in_memory_settings(tmp_path, payload):
    path = tmp_path / 'settings.json'
    manager = ResourceSettingsManager(path)
    manager.update_execution_settings({'mode': 'custom', 'max_parallel': 6})
    before, limits = path.read_bytes(), manager.custom_limits
    with pytest.raises(ValueError):
        manager.update_execution_settings(payload)
    assert path.read_bytes() == before
    assert manager.resource_mode == 'custom'
    assert manager.custom_limits == limits


def test_lowering_total_clamps_saved_post_and_live_limits(tmp_path):
    manager = ResourceSettingsManager(tmp_path / 'settings.json')
    manager.update_execution_settings({'mode': 'custom', 'max_parallel': 6, 'max_posts': 4, 'max_live': 3})
    manager.update_execution_settings({'max_parallel': 2})
    assert manager.custom_limits['max_posts'] == manager.custom_limits['max_live'] == 2
    manager.update_execution_settings({'mode': 'auto'})
    manager.update_execution_settings({'mode': 'custom'})
    assert manager.custom_limits['max_parallel'] == 2


def test_extreme_grows_faster_but_retains_spacing_and_pressure_limits():
    clock = Clock()
    auto = AdaptiveController(clock=clock)
    extreme = AdaptiveController(clock=clock)
    extreme.configure(mode='extreme', max_parallel=None, reserve_percent=10)
    for controller in (auto, extreme):
        controller.decide(sample(), running_jobs=2)
    clock.now = 11
    assert auto.decide(sample(), running_jobs=2).limit == 2
    faster = extreme.decide(sample(), running_jobs=2)
    assert faster.limit == faster.max_publishers == 3
    assert faster.start_spacing_ms == 10000
    assert extreme.decide(sample(), running_jobs=3, since_last_start_ms=9999).admit is False
    pressure = extreme.decide(sample(memory=91), running_jobs=3)
    assert pressure.admit is False and pressure.hold is True


def test_post_only_auto_can_grow_when_publisher_capacity_is_full():
    clock = Clock()
    controller = AdaptiveController(clock=clock)
    controller.decide(sample(), running_jobs=1, demand=True)
    clock.now = 121
    assert controller.decide(sample(), running_jobs=1, demand=True).limit == 3


def test_switching_from_extreme_resets_auto_growth():
    controller = AdaptiveController(start_limit=12)
    controller.configure(mode='extreme', max_parallel=None, reserve_percent=0)
    controller.limit = 12
    controller.configure(mode='auto', max_parallel=None, reserve_percent=10)
    assert controller.decide(sample(), running_jobs=12).limit == 2


def test_custom_uses_user_ceiling_and_resource_forecast():
    controller = AdaptiveController()
    controller.configure(mode='custom', max_parallel=6, reserve_percent=10)
    assert controller.decide(sample(), running_jobs=0).limit == 6
    assert controller.decide(sample(total_mb=4000), running_jobs=0).limit <= 2
    assert controller.decide(sample(memory=94), running_jobs=2).admit is False


def mixed_config():
    return {**DEFAULT_SCHEDULER_CONFIG, 'max_publishers': 6, 'max_post_publishers': 3,
            'max_live_publishers': 2, 'max_preparers': 5,
            'max_total_automation_tasks': 6, 'max_active_profile_containers': 6}


def test_mixed_queue_enforces_post_live_total_and_profile_limits():
    config = mixed_config()
    queue = {'daily_batches': [{'posts': []}]}
    posts = queue['daily_batches'][0]['posts']
    for index, workload in enumerate(['post', 'post', 'post', 'live', 'live']):
        execution = {'execution_id': str(index), 'profile_id': str(index), 'status': 'pending'}
        posts.append({'type': 'live' if workload == 'live' else 'reel', 'executions': [execution]})
        result = claim_execution_lease(queue, execution, str(index), 'test', config=config,
                                       now_ms=1000, workload=workload)
        assert result['claimed']
        execution['status'] = 'running'
    assert can_acquire_scheduler_slot(queue, [], 'publisher', 'new', config, 1000)['reason'] == 'posts_capacity_reached'
    assert can_acquire_scheduler_slot(queue, [], 'publisher', 'new', config, 1000, workload='live')['reason'] == 'live_capacity_reached'
    assert can_acquire_scheduler_slot(queue, [], 'preparer', '0', config, 1000)['reason'] == 'profile_busy'
    prep = {'execution_id': 'prep', 'profile_id': 'prep', 'status': 'pending'}
    posts.append({'type': 'photo', 'executions': [prep]})
    assert claim_execution_lease(queue, prep, 'prep', 'test', kind='preparer', config=config, now_ms=1000)['claimed']
    prep['status'] = 'preparing'
    assert scheduler_snapshot(queue, [], config, 1000)['active'] == {
        'publishers': 5, 'posts': 3, 'live': 2, 'preparers': 1, 'total': 6}
    assert can_acquire_scheduler_slot(queue, [], 'preparer', 'extra', config, 1000)['reason'] == 'total_capacity_reached'


def test_legacy_live_lease_is_classified_without_republishing_or_rewriting():
    lease = {'lease_id': 'old', 'kind': 'publisher', 'profile_id': 'old',
             'expires_at': '2099-01-01T00:00:00+00:00'}
    execution = {'status': 'running', 'scheduler_lease': lease}
    queue = {'daily_batches': [{'posts': [{'type': 'live', 'executions': [execution]}]}]}
    snapshot = scheduler_snapshot(queue, [lease], mixed_config())
    assert snapshot['active']['live'] == 1 and snapshot['active']['posts'] == 0
    assert 'workload' not in lease


def test_dispatch_pass_reserves_post_and_live_caps_before_async_claims(monkeypatch):
    from backend.services import automation_service as service
    config = {**mixed_config(), 'max_post_publishers': 1}
    executions = []
    posts = []
    for index, kind in enumerate(['reel', 'photo', 'live', 'live', 'live']):
        execution = {'execution_id': str(index), 'profile_id': str(index), 'status': 'ready'}
        executions.append(execution)
        posts.append({'type': kind, 'executions': [execution]})
    queue = {'daily_batches': [{'batch_id': 'test', 'posts': posts}]}
    monkeypatch.setattr(service, 'recover_stale_queue_executions', lambda *_: None)
    monkeypatch.setattr(service, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(service, 'ordered_due_executions', lambda *_: executions)
    monkeypatch.setattr(service, 'get_scheduler_config', lambda: config)
    monkeypatch.setattr(service, 'active_in_memory_scheduler_leases', lambda: [])
    monkeypatch.setattr(service, 'get_container_status', lambda *_: 'running')
    monkeypatch.setattr(service, 'cloud_execution_allowed', lambda *_: True)
    monkeypatch.setattr(service, 'batch_iteration_availability', lambda *a, **k: {'allowed': True})
    monkeypatch.setattr('backend.services.auto_warming.yield_for_post', lambda *_: None)
    started = []

    async def run(execution_id, kind):
        started.append(execution_id)

    monkeypatch.setattr(service, '_run_dispatched_queue_task', run)

    async def scenario():
        await service.dispatch_pending_queue()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert started == ['0', '2', '3']


def test_cloud_capacity_keeps_protocol_v1_aggregate_fields(monkeypatch):
    from backend.services import automation_service
    from backend.worker.adapter import QueueAdapter
    monkeypatch.setattr(automation_service, 'current_scheduler_snapshot', lambda: {
        'active': {'publishers': 2, 'posts': 1, 'live': 1, 'preparers': 1, 'total': 3},
        'available': {'publishers': 4, 'posts': 2, 'live': 1, 'preparers': 4, 'total': 3},
        'config': mixed_config(),
    })
    capacity = QueueAdapter(None).capacity()
    assert capacity['active'] == {'publishers': 2, 'preparers': 1, 'total': 3}
    assert set(capacity['available']) == {'publishers', 'preparers', 'total'}
    assert set(capacity['limits']) == {'max_publishers', 'max_preparers', 'max_total_automation_tasks'}


def test_settings_endpoint_recomputes_limits_and_rejects_partial_invalid_update(tmp_path, monkeypatch):
    from backend.routers import settings
    from backend.services import adaptive_runtime, docker_service
    manager = ResourceSettingsManager(tmp_path / 'settings.json')
    monkeypatch.setattr(settings, 'settings_manager', manager)
    monkeypatch.setattr(adaptive_runtime, 'settings_manager', manager)
    monkeypatch.setattr(docker_service, 'settings_manager', manager)
    monkeypatch.setattr(adaptive_runtime, 'controller', AdaptiveController())
    monkeypatch.setattr(adaptive_runtime, '_write_signal', lambda *_: None)
    monkeypatch.setattr(adaptive_runtime.host_pressure, 'sample', lambda **_: sample())
    monkeypatch.setattr(adaptive_runtime, '_running_leases', lambda: [])
    monkeypatch.setattr(docker_service, 'count_running_profile_containers', lambda: 0)
    monkeypatch.setattr(settings, 'shared_ocr_worker_snapshot', lambda: {'status': 'ready'})
    for mode in ('auto', 'extreme', 'custom'):
        result = settings.update_resource_mode({'mode': mode, 'max_parallel': 6, 'max_posts': 3, 'max_live': 2})
        assert result['selected_mode'] == mode
        assert result['adaptive']['enabled'] is True
        assert result['adaptive']['decision']['start_spacing_ms'] >= 10000
    assert result['limits']['max_post_publishers'] == 3
    assert result['limits']['max_live_publishers'] == 2
    before = manager.file_path.read_bytes()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        settings.update_resource_mode({'mode': 'extreme', 'max_live': 17})
    assert error.value.status_code == 400
    assert manager.file_path.read_bytes() == before
