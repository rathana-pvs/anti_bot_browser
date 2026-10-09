"""Live admission, secret isolation and recovery without Facebook or Docker."""
import asyncio
import json
import os
from functools import wraps
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.services import live_service as live, automation_service as automation, container_lifecycle as lifecycle
from backend.routers.live import router

PROFILE = 'profile_001'
SETTINGS = {'server_url': 'rtmps://live-api-s.facebook.com:443/rtmp/',
            'stream_key': 'FB-test-secret-key-123456', 'producer_url': 'https://www.facebook.com/live/producer/'}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    profiles = tmp_path / 'profiles'
    (profiles / PROFILE).mkdir(parents=True)
    (profiles / PROFILE / 'config.json').write_text('{}')
    media = profiles / 'shared_media'
    media.mkdir()
    (media / 'video.mp4').write_bytes(b'fake video')
    monkeypatch.setattr(live, 'PROFILES_DIR', profiles)
    monkeypatch.setattr(live, 'SHARED_MEDIA_DIR', media)
    monkeypatch.setattr(live, 'LIVE_DIR', tmp_path / 'live')
    monkeypatch.setattr(live, 'CONTROLS', {})
    monkeypatch.setattr(automation, 'active_automation_tasks', {})
    monkeypatch.setattr(lifecycle, 'OWNERS_FILE', tmp_path / 'owners.json')
    monkeypatch.setattr(lifecycle, 'LOCK_FILE', tmp_path / 'owners.lock')
    monkeypatch.setattr(lifecycle, 'SHUTTING_DOWN', False)
    monkeypatch.setattr(automation, 'get_scheduler_config', lambda: {'lease_ttl_ms': 60000})
    monkeypatch.setattr(automation, 'load_posting_queue', lambda: {})
    monkeypatch.setattr(automation, 'can_acquire_scheduler_slot', lambda *args: {'allowed': True})
    monkeypatch.setattr(automation, 'get_container_status', lambda *args: 'running')
    live.save_settings(PROFILE, SETTINGS)
    return tmp_path


def test_key_is_private_and_not_returned(sandbox):
    public = live.get_settings(PROFILE)
    assert public['key_saved']
    assert 'stream_key' not in public
    assert 'secret' not in json.dumps(public)
    assert os.stat(live.settings_path(PROFILE)).st_mode & 0o777 == 0o600
    assert not (live.PROFILES_DIR / PROFILE / 'config.json').read_text().count('secret')
    live.save_settings(PROFILE, {**SETTINGS, 'stream_key': ''})
    assert live.read_json(live.settings_path(PROFILE))['stream_key'] == SETTINGS['stream_key']


@pytest.mark.parametrize('url', ['rtmp://live-api-s.facebook.com/rtmp/', 'rtmps://localhost/rtmp/',
    'rtmps://live-api-s.facebook.com.evil.test/rtmp/', 'rtmps://user@live-api-s.facebook.com/rtmp/',
    'rtmps://live-api-s.facebook.com/rtmp/?key=abc', 'rtmps://live-api-s.facebook.com:444/rtmp/'])
def test_rejects_untrusted_stream_destinations(url):
    with pytest.raises(ValueError):
        live.validate_settings({**SETTINGS, 'server_url': url})


@pytest.mark.parametrize('key', ['bad\nkey12345', 'bad;key12345', 'bad/key12345'])
def test_key_cannot_inject_commands(key):
    with pytest.raises(ValueError):
        live.validate_settings({**SETTINGS, 'stream_key': key})


@pytest.mark.parametrize('filename', ['../video.mp4', '/etc/passwd', 'sub/video.mp4', 'a\\video.mp4', 'missing.mp4'])
def test_requires_local_uploaded_video(sandbox, filename):
    with pytest.raises(ValueError):
        live.resolve_video(filename)


def test_symlink_cannot_escape_media(sandbox):
    outside = sandbox / 'outside.mp4'
    outside.write_bytes(b'video')
    (live.SHARED_MEDIA_DIR / 'escape.mp4').symlink_to(outside)
    with pytest.raises(ValueError):
        live.resolve_video('escape.mp4')


def test_crash_holds_session_and_requires_review(sandbox):
    live.private_write(live.state_path(PROFILE), {'profile_id': PROFILE, 'status': 'running', 'stage': 'sending', 'logs': []})
    assert live.get_status(PROFILE)['status'] == 'needs_review'
    assert live.get_status(PROFILE)['stage'] == 'needs_review'
    with pytest.raises(RuntimeError, match='previous broadcast'):
        asyncio.run(live.start_live(PROFILE, 'video.mp4'))
    assert live.clear_review(PROFILE)['stage'] == 'reviewed'


def test_cannot_clear_review_before_container_cleanup(sandbox):
    live.private_write(live.state_path(PROFILE), {'profile_id': PROFILE, 'status': 'needs_review', 'stage': 'needs_review'})
    lifecycle.acquire(PROFILE, 'old-session')
    with pytest.raises(RuntimeError, match='container'):
        live.clear_review(PROFILE)


def test_publisher_admission_is_shared(sandbox, monkeypatch):
    monkeypatch.setattr(automation, 'can_acquire_scheduler_slot', lambda *args: {'allowed': False, 'reason': 'profile_busy'})
    with pytest.raises(RuntimeError, match='profile_busy'):
        asyncio.run(live.start_live(PROFILE, 'video.mp4'))
    assert not lifecycle.snapshot()


def test_container_admission_rejects_pressure(sandbox, monkeypatch):
    monkeypatch.setattr(automation, 'get_container_status', lambda *args: 'stopped')
    monkeypatch.setattr(automation, 'claim_container_start_admission', lambda: {'allowed': False, 'reason': 'memory_pressure'})
    with pytest.raises(RuntimeError, match='memory_pressure'):
        asyncio.run(live.start_live(PROFILE, 'video.mp4'))
    assert not lifecycle.snapshot()


def test_durable_profile_lock_blocks_live(sandbox):
    lifecycle.acquire(PROFILE, 'cloud-execution')
    with pytest.raises(RuntimeError, match='container_owned'):
        asyncio.run(live.start_live(PROFILE, 'video.mp4'))


def test_encoder_keeps_key_out_of_host_arguments_and_bounds_duration():
    args = live.encoder_command(PROFILE, '/data/shared_media/video.mp4', 300, True, 'session')
    assert SETTINGS['stream_key'] not in ' '.join(args)
    assert args[0:3] == ['docker', 'exec', '-i']
    assert args[args.index('-t')+1] == '300'
    assert '-re' in args and '-stream_loop' not in args
    silent = live.encoder_command(PROFILE, '/data/shared_media/video.mp4', 300, False, 'session')
    assert '1:a:0' in silent


def test_loop_option_is_passed_to_ffmpeg_not_docker():
    args = live.encoder_command(PROFILE, '/data/shared_media/video.mp4', 60, True, 'session', loop=True)
    assert args[:3] == ['docker', 'exec', '-i']
    realtime = args.index('-re')
    assert args[realtime:realtime + 5] == ['-re', '-stream_loop', '-1', '-i', '/data/shared_media/video.mp4']


def test_api_masks_keys_and_review_requires_confirmation(sandbox):
    app = FastAPI()
    app.include_router(router)
    async def scenario():
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get(f'/api/profiles/{PROFILE}/live/settings')
            assert response.status_code == 200
            assert SETTINGS['stream_key'] not in response.text
            assert (await client.post(f'/api/profiles/{PROFILE}/live/review', json={'confirmed_ended': False})).status_code == 400
            assert (await client.get('/api/profiles/invalid/live/settings')).status_code == 400
    asyncio.run(scenario())


class FakeEncoder:
    def __init__(self):
        self.returncode = None
        self.stdin = self
        self.stdout = self
        self.sent = b''
        self.done = asyncio.Event()
        self.progress_sent = False
    def write(self, value):
        self.sent += value
    async def drain(self):
        pass
    def close(self):
        pass
    async def readline(self):
        if not self.progress_sent:
            self.progress_sent = True
            return b'out_time_us=2000000\n'
        await self.done.wait()
        return b''
    async def wait(self):
        await self.done.wait()
        return self.returncode
    def finish(self, code=0):
        self.returncode = code
        self.done.set()
    def kill(self):
        self.finish(-9)


@pytest.mark.parametrize('failure,expected', [('prepare', 'failed'), ('start', 'needs_review'), ('end', 'needs_review'), (None, 'completed')])
@pytest.mark.parametrize('loop,limit,muted', [(False, None, False), (True, 600, True), (False, 60, True)])
def test_full_lifecycle_persists_boundary_and_never_retries(sandbox, monkeypatch, failure, expected, loop, limit, muted):
    async def scenario():
        encoder = FakeEncoder()
        calls = []
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        async def command(*args, **kwargs):
            if 'ffprobe' in args:
                return json.dumps({'format': {'duration': '300'}, 'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]}).encode()
            encoder.finish()
            return b''
        monkeypatch.setattr(live, 'command', command)
        async def create(*args, **kwargs):
            assert live.read_json(live.state_path(PROFILE))['stage'] == 'stream_start_intent'
            assert ('-stream_loop' in args) is loop
            assert args[args.index('-t') + 1] == str(limit if limit is not None else 300.0)
            assert args[args.index('-map', args.index('-map') + 1) + 1] == ('1:a:0' if muted else '0:a:0')
            return encoder
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
        async def browser(_, action, url, event):
            calls.append(action)
            if action == failure:
                raise RuntimeError('Simulated Facebook verification failure')
            if action == 'start':
                # Encode completion occurs after confirmation has returned.
                asyncio.get_running_loop().call_later(.01, encoder.finish)
        monkeypatch.setattr(live, 'browser_action', browser)
        async def stop_container(_):
            encoder.finish()
            return {'stopped': True}
        monkeypatch.setattr(automation, '_stop_container_with_retries', stop_container)
        public = await live.start_live(PROFILE, 'video.mp4', loop=loop, max_duration_seconds=limit, muted=muted)
        assert public['stage'] == 'preparing'
        assert automation.is_task_process_active(automation.active_automation_tasks[PROFILE])
        for _ in range(100):
            if PROFILE not in live.CONTROLS:
                break
            await asyncio.sleep(.01)
        assert PROFILE not in live.CONTROLS
        saved = live.get_status(PROFILE)
        assert saved['status'] == expected
        assert SETTINGS['stream_key'] not in json.dumps(saved)
        assert not lifecycle.snapshot()
        assert calls.count('start') <= 1
        if failure != 'prepare':
            assert encoder.sent.decode().strip() == SETTINGS['server_url'] + SETTINGS['stream_key']
        if not failure:
            assert saved['broadcast_confirmed'] is True
    asyncio.run(scenario())


def test_stop_signals_live_without_releasing_profile(sandbox):
    live.CONTROLS[PROFILE] = asyncio.Event()
    record = {'profile_id': PROFILE, 'task': 'live', 'status': 'running', 'stage': 'live', 'logs': [], 'lifecycle_active': True}
    automation.active_automation_tasks[PROFILE] = record
    automation.stop_automation_task(PROFILE)
    assert live.CONTROLS[PROFILE].is_set()
    assert record['status'] == 'running'
    assert record['stage'] == 'stopping'
    assert automation.is_task_process_active(record)


def test_live_holds_publisher_slot_through_terminal_cleanup():
    record = {'task': 'live', 'status': 'completed', 'lifecycle_active': True}
    assert automation.is_task_process_active(record)
    record['lifecycle_active'] = False
    assert not automation.is_task_process_active(record)


def test_stop_after_live_stops_encoder_before_ending_facebook(sandbox, monkeypatch):
    async def scenario():
        encoder = FakeEncoder()
        actions = []
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        async def command(*args, **kwargs):
            if 'ffprobe' in args:
                return b'{"format":{"duration":"300"},"streams":[{"codec_type":"video"}]}'
            encoder.finish()
            return b''
        monkeypatch.setattr(live, 'command', command)
        async def create(*args, **kwargs):
            return encoder
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
        async def browser(_, action, *args):
            actions.append(action)
            if action == 'start':
                asyncio.get_running_loop().call_later(.01, live.stop_live, PROFILE)
            if action == 'end':
                assert encoder.returncode == 0
                assert automation.is_task_process_active(automation.active_automation_tasks[PROFILE])
        monkeypatch.setattr(live, 'browser_action', browser)
        async def stop_container(_):
            return {'stopped': True}
        monkeypatch.setattr(automation, '_stop_container_with_retries', stop_container)
        await live.start_live(PROFILE, 'video.mp4')
        for _ in range(100):
            if PROFILE not in live.CONTROLS:
                break
            await asyncio.sleep(.01)
        assert actions == ['prepare', 'start', 'end']
        assert live.get_status(PROFILE)['status'] == 'stopped'
        assert not lifecycle.snapshot()
    asyncio.run(scenario())

def run_async_test(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapped


@run_async_test
async def test_live_batch_uses_saved_settings_without_copying_secret(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    queue = {'daily_batches': []}
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = await queue_api.create_batch({'target_profiles': [PROFILE],
        'schedule_window': {'session_preparation_mode': 'brief'},
        'posts': [{'type': 'live', 'media_file': 'video.mp4', 'ai_spin': False}]})
    post = result['batch']['posts'][0]
    assert post['type'] == 'live'
    assert post['executions'][0]['preparation_mode'] == 'off'
    assert post['executions'][0]['preparation_status'] == 'not_requested'
    assert SETTINGS['stream_key'] not in json.dumps(result)
    assert 'stream_key' not in json.dumps(queue)


@run_async_test
@pytest.mark.parametrize('post', [
    {'type': 'live', 'media_file': 'missing.mp4'},
    {'type': 'live', 'media_file': 'video.mp4', 'base_caption': 'Ignored caption'},
    {'type': 'live', 'media_file': 'video.mp4', 'first_comment': 'https://example.com'},
])
async def test_live_batch_rejects_invalid_media_and_unsupported_details(sandbox, monkeypatch, post):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    save = lambda _: pytest.fail('Invalid Live batch must not be persisted')
    monkeypatch.setattr(queue_api, 'save_posting_queue', save)
    with pytest.raises(HTTPException) as error:
        await queue_api.create_batch({'target_profiles': [PROFILE], 'posts': [post]})
    assert error.value.status_code == 400


@run_async_test
async def test_live_batch_requires_configured_profiles(sandbox):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    live.settings_path(PROFILE).unlink()
    with pytest.raises(HTTPException) as error:
        await queue_api.create_batch({'target_profiles': [PROFILE],
            'posts': [{'type': 'live', 'media_file': 'video.mp4'}]})
    assert error.value.status_code == 400


def queue_record(monkeypatch):
    execution = {'execution_id': 'exec_live', 'profile_id': PROFILE, 'status': 'running',
                 'scheduler_lease': {'lease_id': 'lease_live'}}
    queue = {'daily_batches': [{'posts': [{'type': 'live', 'executions': [execution]}]}]}
    monkeypatch.setattr(automation, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(automation, 'save_posting_queue', lambda value: True)
    monkeypatch.setattr(automation, 'with_queue_claim_lock', lambda fn: fn())
    record = dict(profile_id=PROFILE, task='live', status='running', stage='preparing',
                  queue_execution_id='exec_live', scheduler_lease={'lease_id': 'lease_live'})
    return record, execution


def test_queue_live_intent_and_progress_are_durable_and_slot_retained(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    live.update(record, 'stream_start_intent')
    assert execution['stage'] == 'stream_start_intent'
    assert live.get_status(PROFILE)['queue_execution_id'] == 'exec_live'
    live.update(record, 'ended', status='completed', broadcast_confirmed=True, elapsed_seconds=60)
    assert execution['status'] == 'running'
    assert execution['scheduler_lease']['lease_id'] == 'lease_live'
    live.sync_queue(record, finalize=True)
    assert execution['status'] == 'completed'
    assert not execution.get('scheduler_lease')
    assert execution['elapsed_seconds'] == 60


def test_queue_live_fencing_blocks_new_ingest(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    execution['scheduler_lease']['lease_id'] = 'different-owner'
    with pytest.raises(RuntimeError, match='ownership lost'):
        live.update(record, 'stream_start_intent')
    assert execution['status'] == 'running'
    assert 'stage' not in execution


@run_async_test
async def test_unresolved_live_cannot_run_now_or_resolve_without_end_confirmation(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    record, execution = queue_record(monkeypatch)
    execution['status'] = 'needs_review'
    queue = automation.load_posting_queue()
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: queue)
    with pytest.raises(HTTPException) as error:
        await queue_api.run_execution_now('exec_live')
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        queue_api.resolve_uncertain('exec_live', {'resolution': 'not_published'})
    assert error.value.status_code == 400


@run_async_test
async def test_queue_live_dispatch_reuses_claimed_lease(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    post = {'type': 'live', 'media_file': 'video.mp4'}
    claim = {'targetExec': execution, 'targetPost': post, 'lease': execution['scheduler_lease']}
    observed = {}
    async def fake_run(value, settings, video, stop):
        observed.update(value)
        assert value['scheduler_lease'] is claim['lease']
        assert video == '/data/shared_media/video.mp4'
        assert settings['stream_key'] == SETTINGS['stream_key']
        value['status'] = 'completed'
        live.CONTROLS.pop(PROFILE)
    monkeypatch.setattr(live, 'run_live', fake_run)
    monkeypatch.setattr(live.lifecycle, 'acquire', lambda *args, **kwargs: pytest.fail('Must not claim twice'))
    result = await live.execute_queue_live(claim)
    assert result['success']
    assert observed['queue_execution_id'] == 'exec_live'
    assert 'stream_key' not in observed


def test_stop_live_is_fenced_to_queue_execution(sandbox):
    stop = asyncio.Event()
    live.CONTROLS[PROFILE] = stop
    automation.active_automation_tasks[PROFILE] = {'queue_execution_id': 'current_execution'}
    with pytest.raises(RuntimeError, match='no longer active'):
        live.stop_live(PROFILE, 'old_execution')
    assert not stop.is_set()


def test_queued_session_review_must_resolve_queue_outcome(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    live.update(record, 'needs_review', status='needs_review')
    with pytest.raises(RuntimeError, match='batch queue'):
        live.clear_review(PROFILE)


def test_queue_live_review_clears_session_only_after_confirming_end(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    record, execution = queue_record(monkeypatch)
    live.update(record, 'needs_review', status='needs_review')
    live.sync_queue(record, finalize=True)
    queue = automation.load_posting_queue()
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = queue_api.resolve_uncertain('exec_live', {'resolution': 'not_published', 'confirmed_ended': True})
    assert result['execution']['review_status'] == 'resolved_not_published'
    assert live.get_status(PROFILE)['stage'] == 'reviewed'


@run_async_test
async def test_live_batch_rejects_duplicate_destinations(sandbox):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    with pytest.raises(HTTPException, match='different Facebook stream keys'):
        await queue_api.create_batch({'target_profiles': [PROFILE, PROFILE],
                                     'posts': [{'type': 'live', 'media_file': 'video.mp4'}]})


@pytest.mark.parametrize('loop,limit,muted', [(True, None, False), (True, 14401, False), (True, 0, False),
    (False, -1, False), (False, True, False), (False, 60.5, False), ('yes', 60, False), (False, None, 'yes')])
def test_playback_requires_bounded_duration_and_boolean_options(loop, limit, muted):
    with pytest.raises(ValueError):
        live.validate_playback(loop, limit, muted)


@run_async_test
async def test_live_batch_persists_playback_settings(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    queue = {'daily_batches': []}
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = await queue_api.create_batch({'target_profiles': [PROFILE], 'posts': [
        {'type': 'live', 'media_file': 'video.mp4', 'loop': True, 'muted': True, 'max_duration_seconds': 3600}]})
    post = result['batch']['posts'][0]
    assert post['loop'] is True
    assert post['muted'] is True
    assert post['max_duration_seconds'] == 3600


@run_async_test
@pytest.mark.parametrize('valid_result', [True, False])
async def test_browser_helper_receives_pin_and_only_verified_metadata_is_persisted(sandbox, monkeypatch, valid_result):
    package = live.resolve_live_brain(live.AUTOMATION_RUNNER.parent, live.ROOT_DIR / 'automation')
    pin = package.metadata()
    record = {'profile_id': PROFILE, 'task': 'live', 'status': 'running', 'stage': 'preparing',
              'brain': pin, 'brain_package': str(package.root), 'title': 'News title', 'caption': 'News caption', 'pinned_comment': 'Pinned message'}
    automation.active_automation_tasks[PROFILE] = record
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (json.dumps({'success': True, 'brain': pin,
        'state': 'prepared' if valid_result else 'live', 'template_id': 'setup_center'}).encode(), b'')
    create = AsyncMock(return_value=proc)
    monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
    if valid_result:
        await live.browser_action(PROFILE, 'prepare', SETTINGS['producer_url'], asyncio.Event())
        public = live.read_json(live.state_path(PROFILE))
        assert public['browser_state'] == 'prepared'
        assert public['browser_template'] == 'setup_center'
        assert public['brain'] == pin
        assert 'brain_package' not in public
        assert SETTINGS['stream_key'] not in json.dumps(public)
    else:
        with pytest.raises(RuntimeError, match='unverified'):
            await live.browser_action(PROFILE, 'prepare', SETTINGS['producer_url'], asyncio.Event())
    args = create.call_args.args
    assert args[args.index('--brain-digest') + 1] == pin['digest']
    assert SETTINGS['stream_key'] not in ' '.join(args)
    assert 'News title' not in ' '.join(args)
    assert json.loads(proc.communicate.call_args.args[0]) == {'title': 'News title', 'caption': 'News caption', 'pinned_comment': 'Pinned message'}


@pytest.mark.parametrize('title,caption', [(None, 'text'), ('Title', None), ('Title', ''), ('', 'Caption'), ('x'*256, 'Caption'), ('Title', 'x'*5001), ('Bad\x00title', 'Caption')])
def test_invalid_live_details(title, caption):
    with pytest.raises(ValueError):
        live.validate_details(title, caption)


@run_async_test
async def test_live_batch_preserves_title_caption_without_spinning(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    queue = {'daily_batches': []}
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = await queue_api.create_batch({'target_profiles': [PROFILE], 'posts': [
        {'type': 'live', 'media_file': 'video.mp4', 'title': '  News title  ',
         'base_caption': 'Caption\nខ្មែរ 😀', 'ai_spin': True}]})
    post = result['batch']['posts'][0]
    assert post['title'] == 'News title'
    assert post['base_caption'] == 'Caption\nខ្មែរ 😀'
    assert post['ai_spin'] is False
    assert post['executions'][0]['spun_caption'] == post['base_caption']


@pytest.mark.parametrize('comment',[123,'x'*1001,'bad\x00comment'])
def test_invalid_preset_comments(comment):
    with pytest.raises(ValueError):
        live.validate_details('Title','Caption',comment)


def test_preset_comment_normalizes_and_preserves_legacy_absence():
    assert live.validate_details('Title','Caption','  Pinned\nខ្មែរ 😀  ')['pinned_comment']=='Pinned\nខ្មែរ 😀'
    assert live.validate_details('Title','Caption','')['pinned_comment']==''
    assert 'pinned_comment' not in live.validate_details('Title','Caption')


@run_async_test
async def test_live_batch_preserves_preset_comment(sandbox,monkeypatch):
    from backend.routers import queue as queue_api
    queue={'daily_batches':[]}
    monkeypatch.setattr(queue_api,'PROFILES_DIR',live.PROFILES_DIR)
    monkeypatch.setattr(queue_api,'load_posting_queue',lambda:queue)
    monkeypatch.setattr(queue_api,'save_posting_queue',lambda value:True)
    result=await queue_api.create_batch({'target_profiles':[PROFILE],'posts':[
        {'type':'live','media_file':'video.mp4','title':'Title','base_caption':'Caption',
         'pinned_comment':'  Comment\nខ្មែរ 😀  '}]})
    assert result['batch']['posts'][0]['pinned_comment']=='Comment\nខ្មែរ 😀'
