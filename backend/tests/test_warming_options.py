import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.routers import automation, queue


def request(router, path, payload):
    app = FastAPI()
    app.include_router(router)
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            return await client.post(path, json=payload)
    return asyncio.run(run())


def test_manual_launch_forwards_preferences(monkeypatch):
    launch = AsyncMock(return_value={'status': 'running'})
    monkeypatch.setattr(automation, 'launch_manual_automation', launch)
    options = {'random_scrolls': True, 'min_scrolls': 3, 'max_scrolls': 7}
    result = request(automation.router, '/api/automation/run', {
        'profile_id': 'test', 'task': 'warming', 'warming_options': options,
    })
    assert result.status_code == 200
    assert launch.await_args.kwargs['warming_options'] == options


@pytest.mark.parametrize('options', [{'max_seconds': 0}, {'min_scrolls': 10, 'max_scrolls': 2}])
def test_queue_rejects_invalid_options_before_touching_queue(monkeypatch, options):
    def unexpected_read():
        pytest.fail('Queue must not be accessed for invalid options')
    monkeypatch.setattr(queue, 'load_posting_queue', unexpected_read)
    result = request(queue.router, '/api/queue/batch', {
        'target_profiles': ['test'], 'posts': [{'type': 'warming', 'warming_options': options}],
    })
    assert result.status_code == 400


def test_queue_preserves_preferences_for_runner_and_monitor(monkeypatch, tmp_path):
    stored = {'daily_batches': []}
    monkeypatch.setattr(queue, 'PROFILES_DIR', tmp_path)
    monkeypatch.setattr(queue, 'load_posting_queue', lambda: stored)
    monkeypatch.setattr(queue, 'save_posting_queue', lambda value: None)
    options = {'random_scrolls': True, 'min_scrolls': 3, 'max_scrolls': 7, 'pace': 'relaxed'}
    result = request(queue.router, '/api/queue/batch', {
        'target_profiles': ['test'], 'posts': [{'type': 'warming', 'warming_options': options}],
    })
    assert result.status_code == 200
    saved = stored['daily_batches'][0]['posts'][0]['warming_options']
    assert saved['pace'] == 'relaxed'
    assert saved['min_scrolls'] == 3
    assert saved['max_scrolls'] == 7
    assert saved['max_seconds'] == 180
    assert queue.get_queue()['executions'][0]['warming_options'] == saved
