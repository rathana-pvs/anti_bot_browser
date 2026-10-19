import json
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.services import caption_hook as hooks
from backend.routers.ai import router


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, 'STORE', tmp_path / 'hooks')


def request(**context):
    return hooks.GenerateRequest(context=hooks.HookContext(
        original_caption='Coffee beans available.\nDelivery available. https://example.com/?a=1&b=2', **context), api_key='secret-key')


def provider(*args):
    return ['A new morning favorite?', 'Looking for coffee today?', 'What is in your cup?']


def approval():
    req = request()
    generated = hooks.generate(req, provider=provider)
    assert generated['status'] == 'ready'
    approved = hooks.approve(hooks.ApproveRequest(generation_id=generated['generation_id'], hook=generated['candidates'][0]['hook']))
    return req, generated, approved


def test_generation_has_three_candidates_and_no_stored_secrets():
    req, generated, approved = approval()
    assert len(generated['candidates']) == 3
    for file in hooks.STORE.glob('*.json'):
        assert 'secret-key' not in file.read_text()
    assert approved['final_caption'].endswith(req.context.original_caption)
    assert 'https://example.com/?a=1&b=2' in approved['final_caption']


def test_missing_context_never_calls_provider():
    callback = Mock(side_effect=AssertionError('No provider'))
    result = hooks.generate(hooks.GenerateRequest(context=hooks.HookContext()), provider=callback)
    assert result['status'] == 'needs_context'
    callback.assert_not_called()


def test_validation_retry_is_bounded_and_filters_new_numbers_links_and_duplicates():
    callback = Mock(return_value=['Only $99 today!', 'https://bad.example', 'same hook'])
    result = hooks.generate(request(recent_hooks=['same hook']), provider=callback)
    assert result['status'] == 'validation_failed'
    assert callback.call_count == 2


def test_provider_failure_is_sanitized():
    callback = Mock(side_effect=RuntimeError('secret-key raw request'))
    result = hooks.generate(request(), provider=callback)
    assert result['status'] == 'provider_failed'
    assert 'secret-key' not in json.dumps(result)


def test_snapshot_disables_legacy_spin_and_retry_needs_no_provider(monkeypatch):
    req, _, approved = approval()
    monkeypatch.setattr(hooks, 'provider_generate', Mock(side_effect=AssertionError('No runtime generation')))
    post = {'type': 'photo', 'media_file': '', 'base_caption': req.context.original_caption,
            'hook_approval': approved, 'ai_spin': True}
    prepared = hooks.prepare_scheduled_post(post)
    assert prepared['base_caption'] == approved['final_caption']
    assert prepared['ai_spin'] is False
    assert prepared['hook_preparation']['original_caption'] == req.context.original_caption
    assert post['base_caption'] == req.context.original_caption


@pytest.mark.parametrize('field,value', [('base_caption', 'edited'), ('media_file', 'changed.jpg'), ('type', 'reel')])
def test_stale_post_rejected(field, value):
    req, _, approved = approval()
    post = {'type': 'photo', 'media_file': '', 'base_caption': req.context.original_caption, 'hook_approval': approved}
    post[field] = value
    with pytest.raises(ValueError, match='changed'): hooks.prepare_scheduled_post(post)


def test_tampered_preview_or_revision_rejected():
    req, _, approved = approval()
    for patch in [{'final_caption': 'tampered'}, {'source_revision': 'old'}]:
        with pytest.raises(ValueError):
            hooks.prepare_scheduled_post({'type': 'photo', 'media_file': '', 'base_caption': req.context.original_caption,
                                          'hook_approval': {**approved, **patch}})


def test_disabled_preserves_original():
    post = {'base_caption': ' caption \n', 'ai_spin': False}
    assert hooks.prepare_scheduled_post(post) is post


def test_path_traversal_rejected():
    with pytest.raises(ValueError): hooks._read('../../secret')


def test_approved_history_used_for_future_requests():
    _, _, approved = approval()
    assert hooks.recent_approved_hooks()
    callback = Mock(return_value=[approved['final_caption'].split('\n')[0]])
    result = hooks.generate(request(), provider=callback)
    assert result['status'] == 'validation_failed'


def test_khmer_composition_preserves_body_and_newlines():
    original = 'សួស្តី\nhttps://example.com?a=1'
    assert hooks.compose_final_caption(original, 'តើអ្នកចូលចិត្តកាហ្វេទេ?') == 'តើអ្នកចូលចិត្តកាហ្វេទេ?\n\n' + original


def test_route_validation_hides_secret_values_and_approval_paths():
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    assert client.post('/api/ai/caption-hooks', json={'context': {}, 'api_key': 'secret', 'model': '../bad'}).status_code == 422
    assert client.post('/api/ai/caption-hooks/approve', json={'generation_id': 'a'*32, 'hook': 'hello'}).status_code == 400


def test_queue_snapshots_approved_caption_for_every_profile(monkeypatch, tmp_path):
    import asyncio
    from backend.routers import queue as queue_router
    req, _, approved = approval()
    queue = {'daily_batches': []}
    monkeypatch.setattr(queue_router, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(queue_router, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(queue_router, 'save_posting_queue', lambda data: True)
    monkeypatch.setattr(queue_router, 'generate_spun_caption', Mock(side_effect=AssertionError('Approved captions cannot spin')))
    result = asyncio.run(queue_router.create_batch({
        'name': 'Fixture', 'target_profiles': ['p1', 'p2'], 'start_now': False,
        'schedule_window': {'start_time': '09:00', 'end_time': '21:00'},
        'posts': [{'type': 'photo', 'media_file': '', 'base_caption': req.context.original_caption,
                   'ai_spin': True, 'hook_approval': approved}],
    }))
    post = result['batch']['posts'][0]
    assert post['base_caption'] == approved['final_caption']
    assert post['hook_preparation']['approval_id'] == approved['approval_id']
    assert len(post['executions']) == 2
    assert {execution['spun_caption'] for execution in post['executions']} == {approved['final_caption']}
