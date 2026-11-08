"""Live admission, secret isolation and recovery without Facebook or Docker."""
import asyncio
import json
import os
from functools import wraps
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.services import live_service as live, automation_service as automation, container_lifecycle as lifecycle, android_devices
from backend.routers.live import router

PROFILE = 'profile_001'
SETTINGS = {'server_url': 'rtmps://live-api-s.facebook.com:443/rtmp/',
            'stream_key': 'FB-test-secret-key-123456', 'producer_url': 'https://www.facebook.com/live/producer/'}


@pytest.mark.parametrize('message,reason', [
    ('Server error: invalid stream key: FB-PRIVATE', 'invalid_stream_key'),
    ('Server error: Publishing failed: rtmps://host/FB-PRIVATE', 'server_rejected_stream'),
    ('Connection timed out: rtmps://host/FB-PRIVATE', 'connection_timeout'),
    ('Cannot read RTMP handshake response: rtmps://host/FB-PRIVATE', 'rtmp_handshake_failed'),
    ('Error in the pull function: rtmps://host/FB-PRIVATE', 'connection_closed'),
    ('Failed to resolve hostname: private-host', 'dns_failed'),
    ('Error initializing output stream 0:0', 'encoder_initialization_failed'),
    ('Failed to configure output pad on Parsed_scale_0', 'filter_initialization_failed'),
    ('Input/output error: rtmps://host/FB-PRIVATE', 'output_io_failed'),
    ('Unrecognized error containing FB-PRIVATE', None),
])
def test_encoder_diagnostics_never_include_credentials(message, reason):
    from automation.live_encoder import classify_encoder_error
    assert classify_encoder_error(message) == reason


@pytest.mark.parametrize('action', ['close', 'pause', 'news_feed'])
@pytest.mark.parametrize('failure', [None, RuntimeError, asyncio.TimeoutError, asyncio.CancelledError])
def test_optional_finish_preserves_confirmed_outcome(sandbox, monkeypatch, action, failure):
    async def scenario():
        record = dict(profile_id=PROFILE, comment_status='confirmed', evidence_dir='comment-proof',
                      completion_reason='comment_verified_after_end', status='running', stage='stopping')
        before = record.copy()
        stop = asyncio.Event()
        browser = AsyncMock(side_effect=failure('optional failure') if failure else None)
        monkeypatch.setattr(live.random, 'choice', lambda _: action)
        monkeypatch.setattr(live, 'browser_action', browser)
        if action == 'pause':
            stop.wait = AsyncMock(side_effect=asyncio.TimeoutError)
        await live.optional_finish(record, SETTINGS, stop)
        assert {key:record[key] for key in before} == before
        events = [item['event'] for item in record['logs']]
        assert events[0] == f'optional_finish_{action}_selected'
        expected = 'failed' if action == 'news_feed' and failure else 'completed'
        assert events[-1] == f'optional_finish_{action}_{expected}'
        assert 'optional failure' not in json.dumps(record['logs'])
        assert browser.await_count == (1 if action == 'news_feed' else 0)
    asyncio.run(scenario())


def test_optional_finish_records_missing_logo_as_skipped(sandbox, monkeypatch):
    async def scenario():
        record = dict(profile_id=PROFILE, comment_status='confirmed', status='running')
        monkeypatch.setattr(live.random, 'choice', lambda _: 'news_feed')
        monkeypatch.setattr(live, 'browser_action', AsyncMock(return_value='logo_missing'))
        await live.optional_finish(record, SETTINGS, asyncio.Event())
        assert record['logs'][-1]['event'] == 'optional_finish_news_feed_skipped'
        assert record['status'] == 'running'
    asyncio.run(scenario())


def test_optional_finish_log_failure_cannot_fail_live(sandbox, monkeypatch):
    async def scenario():
        record = dict(profile_id=PROFILE, comment_status='confirmed', status='running')
        monkeypatch.setattr(live, 'live_event', Mock(side_effect=OSError('disk full')))
        await live.optional_finish(record, SETTINGS, asyncio.Event())
        assert record['status'] == 'running'
    asyncio.run(scenario())


def test_optional_finish_never_runs_before_comment_confirmation(sandbox, monkeypatch):
    async def scenario():
        browser = AsyncMock()
        monkeypatch.setattr(live, 'browser_action', browser)
        monkeypatch.setattr(live.random, 'choice', Mock(side_effect=AssertionError('Must not choose an action')))
        await live.optional_finish({'profile_id':PROFILE, 'comment_status':'attempted'}, SETTINGS, asyncio.Event())
        browser.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('elapsed,invoked', [(20, True), (250, True)])
def test_comment_after_end_has_its_own_budget(sandbox, monkeypatch, elapsed, invoked):
    async def scenario():
        record = dict(profile_id=PROFILE, task='live', stage='starting', status='running',
                      broadcast_confirmed=True, pinned_comment='Test comment')
        monkeypatch.setattr(live.time, 'monotonic', lambda: elapsed)
        action = AsyncMock()
        monkeypatch.setattr(live, 'browser_action', action)
        await live.comment_after_live(record, SETTINGS, asyncio.Event())
        assert bool(action.await_count) is invoked
        assert record['comment_status'] == ('confirmed' if invoked else 'needs_review')
        if not invoked: assert record['comment_error_code'] == 'startup_budget_insufficient'
        # Neither skipped nor completed attempts may automatically replay.
        await live.comment_after_live(record, SETTINGS, asyncio.Event())
        assert action.await_count == int(invoked)
    asyncio.run(scenario())


def test_comment_failure_preserves_reason_without_stopping_live(sandbox, monkeypatch):
    async def scenario():
        record = dict(profile_id=PROFILE, task='live', stage='starting', status='running',
                      broadcast_confirmed=True, pinned_comment='Test comment',
                      comment_error_code='entry_deadline_exceeded')
        monkeypatch.setattr(live, 'browser_action', AsyncMock(side_effect=RuntimeError('Comment failed')))
        await live.comment_after_live(record, SETTINGS, asyncio.Event())
        assert record['status'] == 'running'
        assert record['comment_status'] == 'needs_review'
        assert record['comment_error_code'] == 'entry_deadline_exceeded'
    asyncio.run(scenario())


@pytest.mark.parametrize('raw_code', ['entry_deadline_exceeded', 'PRIVATE-COMMENT'])
def test_comment_helper_failure_code_is_allowlisted(sandbox, monkeypatch, raw_code):
    async def scenario():
        package = live.resolve_live_brain(live.AUTOMATION_RUNNER.parent, live.ROOT_DIR / 'automation')
        record = dict(profile_id=PROFILE, task='live', status='running', stage='starting',
                      brain=package.metadata(), brain_package=str(package.root))
        automation.active_automation_tasks[PROFILE] = record
        proc = AsyncMock(); proc.returncode = 0; proc.stdin.write = Mock(); proc.stdin.close = Mock()
        proc.stdout.readline.side_effect = [json.dumps({'success':False,'error':'Comment failed',
                                                       'failure_code':raw_code}).encode(), b'']
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
        with pytest.raises(RuntimeError, match='Comment failed'):
            await live.browser_action(PROFILE,'comment',SETTINGS['producer_url'],asyncio.Event())
        assert record['comment_error_code'] == (raw_code if raw_code=='entry_deadline_exceeded' else 'comment_failed')
        assert 'PRIVATE-COMMENT' not in live.state_path(PROFILE).read_text()
    asyncio.run(scenario())


@pytest.mark.parametrize('code', ['account_locked', 'identity_verification_required'])
def test_live_helper_account_restriction_uses_fixed_reason(sandbox, monkeypatch, code):
    async def scenario():
        package = live.resolve_live_brain(live.AUTOMATION_RUNNER.parent, live.ROOT_DIR / 'automation')
        record = dict(profile_id=PROFILE, task='live', status='running', stage='commenting',
                      brain=package.metadata(), brain_package=str(package.root))
        automation.active_automation_tasks[PROFILE] = record
        proc = AsyncMock(); proc.returncode = 0; proc.stdin.write = Mock(); proc.stdin.close = Mock()
        proc.stdout.readline.side_effect = [json.dumps({'success':False, 'account_block':code,
                                                       'error':'Untrusted diagnostic'}).encode(), b'']
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
        with pytest.raises(RuntimeError) as error:
            await live.browser_action(PROFILE,'comment',SETTINGS['producer_url'],asyncio.Event())
        assert str(error.value) == live.ACCOUNT_BLOCK_ERRORS[code]
        assert record['account_block'] == code
    asyncio.run(scenario())


@pytest.mark.parametrize('comment_confirmed', [True, False])
@pytest.mark.parametrize('helper_exit', [None, 1])
@pytest.mark.parametrize('after_end_flow', [True, False])
def test_confirmed_comment_finishes_on_end_action_without_waiting_for_banner(sandbox, monkeypatch, comment_confirmed, helper_exit, after_end_flow):
    async def scenario():
        package = live.resolve_live_brain(live.AUTOMATION_RUNNER.parent, live.ROOT_DIR / 'automation')
        record = dict(profile_id=PROFILE, task='live', status='running', stage='live',
                      brain=package.metadata(), brain_package=str(package.root),
                      comment_status='confirmed' if comment_confirmed else 'needs_review',
                      end_before_comment=after_end_flow)
        automation.active_automation_tasks[PROFILE] = record
        proc = AsyncMock(); proc.returncode = helper_exit
        proc.stdin.write = Mock(); proc.stdin.close = Mock(); proc.kill = Mock()
        proc.stdout.readline.side_effect = [
            json.dumps({'event': 'end_confirmation_click_sent'}).encode(),
            json.dumps({'success': False, 'error': 'End banner was not recognized'}).encode(), b'']
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
        if comment_confirmed or after_end_flow:
            await live.end_broadcast(record, SETTINGS, asyncio.Event())
            assert record['end_action_sent'] is True
            assert record['final_browser_state'] == 'unconfirmed'
            assert proc.stdout.readline.await_count == 1
            assert record['logs'][-1]['event'] == 'end_action_completed'
        else:
            with pytest.raises(RuntimeError, match='End banner'):
                await live.end_broadcast(record, SETTINGS, asyncio.Event())
            assert not record.get('end_action_sent')
        assert proc.kill.call_count == (1 if helper_exit is None else 0)
    asyncio.run(scenario())


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(live.random, 'choice', lambda _: 'close')
    profiles = tmp_path / 'profiles'
    (profiles / PROFILE).mkdir(parents=True)
    (profiles / PROFILE / 'config.json').write_text('{}')
    media = profiles / 'shared_media'
    media.mkdir()
    (media / 'video.mp4').write_bytes(b'fake video')
    monkeypatch.setattr(live, 'PROFILES_DIR', profiles)
    monkeypatch.setattr(android_devices, 'PROFILES_DIR', profiles)
    monkeypatch.setattr(live, 'SHARED_MEDIA_DIR', media)
    monkeypatch.setattr(live, 'LIVE_DIR', tmp_path / 'live')
    monkeypatch.setattr(live, 'CONTROLS', {})
    monkeypatch.setattr(automation, 'active_automation_tasks', {})
    monkeypatch.setattr(lifecycle, 'OWNERS_FILE', tmp_path / 'owners.json')
    monkeypatch.setattr(lifecycle, 'LOCK_FILE', tmp_path / 'owners.lock')
    monkeypatch.setattr(lifecycle, 'SHUTTING_DOWN', False)
    monkeypatch.setattr(automation, 'get_scheduler_config', lambda: {'lease_ttl_ms': 60000})
    monkeypatch.setattr(automation, 'load_posting_queue', lambda: {})
    monkeypatch.setattr(automation, 'can_acquire_scheduler_slot', lambda *args, **kwargs: {'allowed': True})
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
    assert live.clear_review(PROFILE)['stage'] == 'reviewed'


@pytest.mark.parametrize('stage,confirmed,expected', [
    ('preparing', False, 'failed'), ('preparing', True, 'needs_review'),
    ('stream_start_intent', False, 'needs_review'), ('starting', False, 'needs_review'),
    ('live', True, 'needs_review'), ('ending', True, 'needs_review'),
])
def test_restart_distinguishes_setup_from_possible_broadcast(sandbox, stage, confirmed, expected):
    live.private_write(live.state_path(PROFILE), {'profile_id': PROFILE, 'status': 'running',
                       'stage': stage, 'broadcast_confirmed': confirmed, 'logs': []})
    result = live.get_status(PROFILE)
    assert result['status'] == result['stage'] == expected
    assert result['ended_at']
    assert not live.CONTROLS  # Recovery never launches a replacement broadcast.


def test_cannot_clear_review_before_container_cleanup(sandbox):
    live.private_write(live.state_path(PROFILE), {'profile_id': PROFILE, 'status': 'needs_review', 'stage': 'needs_review'})
    lifecycle.acquire(PROFILE, 'old-session')
    with pytest.raises(RuntimeError, match='container'):
        live.clear_review(PROFILE)


def test_publisher_admission_is_shared(sandbox, monkeypatch):
    monkeypatch.setattr(automation, 'can_acquire_scheduler_slot', lambda *args, **kwargs: {'allowed': False, 'reason': 'profile_busy'})
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
    assert args[args.index('--duration')+1] == '300'
    assert '--loop' not in args
    assert '--has-audio' in args
    silent = live.encoder_command(PROFILE, '/data/shared_media/video.mp4', 300, False, 'session')
    assert '--has-audio' not in silent


def test_loop_option_is_passed_to_ffmpeg_not_docker():
    args = live.encoder_command(PROFILE, '/data/shared_media/video.mp4', 60, True, 'session', loop=True)
    assert args[:3] == ['docker', 'exec', '-i']
    assert args.index('--loop') > args.index('python3')


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
        self.lines = asyncio.Queue()
    def write(self, value):
        self.sent += value
    async def drain(self):
        pass
    def close(self):
        pass
    async def readline(self):
        return await self.lines.get()
    def activate(self):
        self.lines.put_nowait(b'phase=playback\n')
        self.lines.put_nowait(b'out_time_us=2000000\n')
    async def wait(self):
        await self.done.wait()
        return self.returncode
    def finish(self, code=0):
        self.returncode = code
        self.done.set()
        self.lines.put_nowait(b'')
    def kill(self):
        self.finish(-9)


@pytest.mark.parametrize('stuck', [True, False])
def test_start_timeout_or_error_ends_observed_broadcast(sandbox, monkeypatch, stuck):
    async def scenario():
        encoder, actions = FakeEncoder(), []
        monkeypatch.setattr(live, 'LIVE_SESSION_LIMIT_SECONDS', .08)
        monkeypatch.setattr(live, 'LIVE_END_RESERVE_SECONDS', .03)
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        async def command(*args, **kwargs):
            if 'ffprobe' in args:
                return b'{"format":{"duration":"300"},"streams":[{"codec_type":"video"}]}'
            encoder.finish()
            return b''
        async def browser(_, action, *args, **kwargs):
            actions.append(action)
            if action == 'prepare': kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
            if action == 'start':
                if stuck: await asyncio.Event().wait()
                raise RuntimeError('Live confirmation failed')
            if action == 'inspect': return 'live'
            if action == 'end': return 'ended'
        async def cleanup(_):
            encoder.finish()
            return {'stopped':True}
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        monkeypatch.setattr(live, 'command', command)
        monkeypatch.setattr(live, 'browser_action', browser)
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=encoder))
        monkeypatch.setattr(automation, '_stop_container_with_retries', cleanup)
        await live.start_live(PROFILE, 'video.mp4', pinned_comment='comment')
        for _ in range(100):
            if PROFILE not in live.CONTROLS: break
            await asyncio.sleep(.01)
        saved = live.get_status(PROFILE)
        assert PROFILE not in live.CONTROLS
        assert actions == ['prepare', 'start', 'inspect', 'end']
        assert saved['final_browser_state'] == 'ended'
        assert saved['status'] == ('stopped' if stuck else 'needs_review')
        if stuck:
            assert any(x['event'] == 'live_duration_limit_reached' for x in saved['logs'])
    asyncio.run(scenario())


def test_broadcast_limit_does_not_interrupt_comment_after_end(sandbox, monkeypatch):
    async def scenario():
        encoder = FakeEncoder()
        monkeypatch.setattr(live, 'LIVE_SESSION_LIMIT_SECONDS', .08)
        monkeypatch.setattr(live, 'LIVE_END_RESERVE_SECONDS', .03)
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        async def command(*args, **kwargs):
            if 'ffprobe' in args:
                return b'{"format":{"duration":"300"},"streams":[{"codec_type":"video"}]}'
            encoder.finish()
            return b''
        async def browser(_, action, producer_url, stop_event, **kwargs):
            if action == 'prepare': kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
            if action == 'comment':
                await asyncio.sleep(.15)  # Outlasts the limit that would have fired.
                assert not stop_event.is_set()
            return 'ended' if action == 'end' else None
        async def cleanup(_):
            encoder.finish()
            return {'stopped':True}
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        monkeypatch.setattr(live, 'command', command)
        monkeypatch.setattr(live, 'browser_action', browser)
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=encoder))
        monkeypatch.setattr(automation, '_stop_container_with_retries', cleanup)
        await live.start_live(PROFILE, 'video.mp4', pinned_comment='comment')
        for _ in range(200):
            if PROFILE not in live.CONTROLS: break
            await asyncio.sleep(.01)
        saved = live.get_status(PROFILE)
        assert saved['status'] == 'completed'
        assert saved['comment_status'] == 'confirmed'
        assert not any(x['event'] == 'live_duration_limit_reached' for x in saved['logs'])
    asyncio.run(scenario())


@pytest.mark.parametrize('failure,expected', [('prepare', 'failed'), ('start', 'needs_review'), ('comment', 'needs_review'), ('end', 'needs_review'), (None, 'completed')])
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
            if 'touch' in args:
                encoder.activate()
            else:
                encoder.finish()
            return b''
        monkeypatch.setattr(live, 'command', command)
        async def create(*args, **kwargs):
            assert live.read_json(live.state_path(PROFILE))['stage'] == 'stream_start_intent'
            assert ('--loop' in args) is loop
            assert args[args.index('--duration') + 1] == str(min(limit if limit is not None else 300.0, 120))
            assert args[args.index('--max-session-seconds') + 1] == '120'
            assert ('--has-audio' in args) is (not muted)
            return encoder
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
        async def browser(_, action, url, event, **kwargs):
            if action == 'prepare':
                kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
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
        public = await live.start_live(PROFILE, 'video.mp4', loop=loop, max_duration_seconds=limit, muted=muted, pinned_comment='Live comment')
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
        if failure not in {'prepare', 'start', 'end'}:
            assert calls.index('comment') > calls.index('start')
            assert calls.count('comment') == 1
            assert saved['comment_status'] == ('needs_review' if failure == 'comment' else 'confirmed')
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


def test_stop_after_live_keeps_encoder_until_facebook_ends(sandbox, monkeypatch):
    async def scenario():
        encoder = FakeEncoder()
        actions = []
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        async def command(*args, **kwargs):
            if 'ffprobe' in args:
                return b'{"format":{"duration":"300"},"streams":[{"codec_type":"video"}]}'
            if 'touch' in args:
                encoder.activate()
            else:
                encoder.finish()
            return b''
        monkeypatch.setattr(live, 'command', command)
        async def create(*args, **kwargs):
            return encoder
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
        async def browser(_, action, *args, **kwargs):
            if action == 'prepare':
                kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
            actions.append(action)
            if action == 'start':
                asyncio.get_running_loop().call_later(.01, live.stop_live, PROFILE)
            if action == 'end':
                assert encoder.returncode is None
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
        assert encoder.returncode == 0
    asyncio.run(scenario())

def run_async_test(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapped


@run_async_test
async def test_cancellation_during_end_waits_for_same_helper(sandbox, monkeypatch):
    record = {'profile_id': PROFILE, 'stage': 'live', 'status': 'running', 'task': 'live'}
    entered, release = asyncio.Event(), asyncio.Event()
    stop = asyncio.Event()
    async def end(*args):
        entered.set()
        await release.wait()
        return 'ended'
    action = AsyncMock(side_effect=end)
    monkeypatch.setattr(live, 'browser_action', action)
    ending = asyncio.create_task(live.end_broadcast(record, SETTINGS, stop))
    await entered.wait()
    ending.cancel()
    await asyncio.sleep(0)
    assert not ending.done()
    assert stop.is_set()
    release.set()
    await ending
    assert record['final_browser_state'] == 'ended'
    assert action.await_count == 1


@run_async_test
@pytest.mark.parametrize('end_fails', [False, True])
async def test_cancelled_confirmed_broadcast_ends_before_container_cleanup(sandbox, monkeypatch, end_fails):
    encoder = FakeEncoder()
    stop = asyncio.Event()
    record = dict(profile_id=PROFILE, task='live', session_id='cancel-test',
                  status='running', stage='preparing', logs=[], lifecycle_active=True)
    automation.active_automation_tasks[PROFILE] = record
    actions = []
    async def command(*args, **kwargs):
        if 'ffprobe' in args:
            return b'{"format":{"duration":"30"},"streams":[{"codec_type":"video"}]}'
        encoder.finish(1)
        return b'{}'
    async def browser(_, action, *args, **kwargs):
        actions.append(action)
        if action == 'prepare':
            kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
        if action == 'start':
            raise asyncio.CancelledError()
        if action == 'inspect':
            return 'live'
        if action == 'end':
            assert encoder.returncode is None
            if end_fails:
                raise RuntimeError('Facebook did not confirm End')
            return 'ended'
    async def comment(*args):
        raise asyncio.CancelledError()
    async def cleanup(_):
        assert encoder.returncode == 1
        actions.append('cleanup')
    monkeypatch.setattr(automation, 'ensure_profile_container_ready', AsyncMock())
    monkeypatch.setattr(automation, 'stop_profile_container_after_report', cleanup)
    monkeypatch.setattr(live, 'command', command)
    monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=encoder))
    monkeypatch.setattr(live, 'browser_action', browser)
    await live.run_live(record, dict(SETTINGS), '/video.mp4', stop)
    assert actions == ['prepare', 'start', 'inspect', 'end', 'cleanup']
    assert record['status'] == ('needs_review' if end_fails else 'stopped')
    assert record['final_browser_state'] == ('live' if end_fails else 'ended')
    if end_fails:
        report = live.read_json(Path(record['evidence_dir']) / 'diagnostics.json')
        assert report['failure_code'] == 'task_cancelled'
        assert report['cleanup_failure_code'] == 'workflow_error'
    assert not record['lifecycle_active']


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
async def test_live_batch_rejects_invalid_saved_settings(sandbox):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    live.private_write(live.settings_path(PROFILE), {**SETTINGS, 'server_url': 'rtmps://untrusted/rtmp/'})
    with pytest.raises(HTTPException) as error:
        await queue_api.create_batch({'target_profiles': [PROFILE],
            'posts': [{'type': 'live', 'media_file': 'video.mp4'}]})
    assert error.value.status_code == 400


def test_live_needs_no_saved_settings_or_key(sandbox):
    live.settings_path(PROFILE).unlink()
    settings = live.launch_settings(PROFILE)
    assert settings['server_url'] == live.DEFAULT_SERVER_URL
    assert settings['producer_url'] == SETTINGS['producer_url']
    assert settings['stream_key'] == ''


def test_live_stays_unavailable_for_phone_profiles(sandbox, monkeypatch):
    live.settings_path(PROFILE).unlink()
    monkeypatch.setattr(android_devices, 'is_android_profile', lambda profile_id: True)
    with pytest.raises(ValueError, match='Android'):
        live.launch_settings(PROFILE)


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


def test_live_account_block_finalizes_as_skipped_and_keeps_evidence(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    live.update(record, 'skipped_auth_required', status='skipped_auth_required',
                evidence_dir='blocked-account-evidence', ended_at=live.now())
    assert execution['status'] == 'running'  # Cleanup retains admission.
    live.sync_queue(record, finalize=True)
    assert execution['status'] == 'skipped_auth_required'
    assert execution['evidence_dir'] == 'blocked-account-evidence'
    assert not execution.get('scheduler_lease')


def test_queue_live_fencing_blocks_new_ingest(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    execution['scheduler_lease']['lease_id'] = 'different-owner'
    with pytest.raises(RuntimeError, match='ownership lost'):
        live.update(record, 'stream_start_intent')
    assert execution['status'] == 'running'
    assert 'stage' not in execution


def test_progress_write_renews_owned_lease(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    execution['scheduler_lease'] = {'lease_id': 'lease_live', 'expires_at': '2000-01-01T00:00:00+00:00'}
    record['scheduler_lease'] = {'lease_id': 'lease_live', 'expires_at': '2000-01-01T00:00:00+00:00'}
    live.update(record, 'commenting')
    # The stale expiry is pushed forward so a long comment tail cannot lapse it.
    assert execution['scheduler_lease']['lease_id'] == 'lease_live'
    assert execution['scheduler_lease']['expires_at'] > '2000-01-01T00:00:00+00:00'
    assert record['scheduler_lease']['expires_at'] == execution['scheduler_lease']['expires_at']


def test_confirmed_comment_finalizes_after_self_lease_reclaim(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    # The scheduler reclaimed this attempt's own lease during the comment tail
    # (no other owner took over): the confirmed outcome must still be recorded.
    execution['scheduler_lease'] = None
    execution['last_scheduler_lease'] = {'lease_id': 'lease_live'}
    execution['status'] = 'failed_before_publish'
    record.update(status='completed', broadcast_confirmed=True, comment_status='confirmed',
                  completion_reason='comment_verified_after_end', ended_at=live.now())
    live.sync_queue(record, finalize=True)
    assert execution['status'] == 'completed'
    assert execution['comment_status'] == 'confirmed'
    assert execution['completion_reason'] == 'comment_verified_after_end'


def test_reclaim_by_other_owner_never_overwrites_outcome(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    # A different owner reran the attempt: our confirmed success must not clobber
    # theirs, whether they still hold the lease or have since released it.
    record.update(status='completed', broadcast_confirmed=True, ended_at=live.now())
    execution['scheduler_lease'] = {'lease_id': 'new-owner'}
    with pytest.raises(RuntimeError, match='ownership lost'):
        live.sync_queue(record, finalize=True)
    execution['scheduler_lease'] = None
    execution['last_scheduler_lease'] = {'lease_id': 'new-owner'}
    with pytest.raises(RuntimeError, match='ownership lost'):
        live.sync_queue(record, finalize=True)


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
    prior = {'profile_id': PROFILE, 'status': 'needs_review', 'stage': 'needs_review', 'queue_execution_id': 'older-exec', 'session_id': 'older-session'}
    live.private_write(live.state_path(PROFILE), prior)
    post = {'type': 'live', 'media_file': 'video.mp4'}
    claim = {'targetExec': execution, 'targetPost': post, 'lease': execution['scheduler_lease']}
    observed = {}
    async def fake_run(value, settings, video, stop):
        observed.update(value)
        assert value['scheduler_lease'] is claim['lease']
        assert video == '/data/shared_media/video.mp4'
        assert settings['stream_key'] == ''  # Filled only by verified native Copy during preparation.
        value['status'] = 'completed'
        live.CONTROLS.pop(PROFILE)
    monkeypatch.setattr(live, 'run_live', fake_run)
    monkeypatch.setattr(live.lifecycle, 'acquire', lambda *args, **kwargs: pytest.fail('Must not claim twice'))
    result = await live.execute_queue_live(claim)
    assert result['success']
    assert observed['queue_execution_id'] == 'exec_live'
    assert 'stream_key' not in observed
    archives = list((live.LIVE_DIR / PROFILE / 'history').glob('*.json'))
    assert len(archives) == 1
    assert live.read_json(archives[0]) == prior


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
    with pytest.raises(HTTPException, match='Select each profile only once'):
        await queue_api.create_batch({'target_profiles': [PROFILE, PROFILE],
                                     'posts': [{'type': 'live', 'media_file': 'video.mp4'}]})


@pytest.mark.parametrize('named', [True, False])
@run_async_test
async def test_live_batch_ignores_cached_keys_when_destinations_are_copied(sandbox, monkeypatch, named):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    profiles = [f'profile_{number:03}' for number in range(1, 6)]
    keys = [SETTINGS['stream_key'], SETTINGS['stream_key'], 'FB-other-secret-key-123',
            'FB-other-secret-key-123', 'FB-unique-secret-key-123']
    for pid, key in zip(profiles, keys):
        directory = live.PROFILES_DIR / pid
        directory.mkdir(exist_ok=True)
        # Identical names must still be distinguishable by ID.
        (directory / 'config.json').write_text(json.dumps({'name': 'Same name'} if named else {}))
        live.save_settings(pid, {**SETTINGS, 'stream_key': key})
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: {'daily_batches': []})
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = await queue_api.create_batch({'target_profiles': profiles,
                                          'posts': [{'type': 'live', 'media_file': 'video.mp4'}]})
    assert result['batch']
    assert all(key not in json.dumps(result) for key in keys)



@pytest.mark.parametrize('loop,limit,muted', [(True, None, False), (True, 14401, False), (True, 0, False),
    (False, -1, False), (False, True, False), (False, 60.5, False), ('yes', 60, False), (False, None, 'yes')])
def test_playback_requires_bounded_duration_and_boolean_options(loop, limit, muted):
    with pytest.raises(ValueError):
        live.validate_playback(loop, limit, muted)


@pytest.mark.parametrize('enabled,comment', [(True,None),(True,''),(True,'  '),('true','hello'),(1,'hello')])
def test_end_after_comment_requires_boolean_and_nonempty_comment(enabled, comment):
    with pytest.raises(ValueError):
        live.validate_end_after_comment(enabled, comment)


@pytest.mark.parametrize('finish_fails', [False, True])
@pytest.mark.parametrize('enabled,comment_ok,end_ok', [(True,True,True),(True,False,True),
                                                      (False,True,True),(True,True,False)])
@pytest.mark.parametrize('cleanup_ok', [True, False])
def test_end_then_comment_then_container_cleanup_before_success(sandbox, monkeypatch, enabled, comment_ok, end_ok, cleanup_ok, finish_fails):
    monkeypatch.setattr(live.random, 'choice', lambda _: 'news_feed')
    async def scenario():
        encoder = FakeEncoder()
        actions, commands = [], []
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        async def command(*args, **kwargs):
            if 'ffprobe' in args:
                return b'{"format":{"duration":"4"},"streams":[{"codec_type":"video"}]}'
            commands.append(args)
            if 'touch' in args:
                encoder.activate()
                asyncio.get_running_loop().call_later(.01, encoder.finish)
            elif 'sh' in args:
                encoder.finish(1)  # Intentional stop after End must not fail a completed broadcast.
            return b''
        async def browser(_, action, *args, **kwargs):
            actions.append(action)
            if action == 'prepare': kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
            if action == 'comment':
                assert encoder.returncode == 1  # End happened; sender stopped, browser stays open.
                if not comment_ok: raise RuntimeError('Comment unconfirmed')
            if action == 'end':
                assert encoder.returncode is None
                if not end_ok: raise RuntimeError('Facebook did not confirm End')
            if action == 'inspect': return 'live'
            if action == 'finish':
                assert automation.active_automation_tasks[PROFILE]['comment_status'] == 'confirmed'
                if finish_fails: raise RuntimeError('Optional navigation failed')
        async def cleanup(_):
            if end_ok and comment_ok:
                assert automation.active_automation_tasks[PROFILE]['status'] == 'running'
            actions.append('cleanup')
            encoder.finish()
            if not cleanup_ok:
                raise RuntimeError('Container did not stop')
            return {'stopped': True}
        monkeypatch.setattr(automation,'ensure_profile_container_ready',ready)
        monkeypatch.setattr(live,'command',command)
        monkeypatch.setattr(live.asyncio,'create_subprocess_exec',AsyncMock(return_value=encoder))
        monkeypatch.setattr(live,'browser_action',browser)
        monkeypatch.setattr(automation,'_stop_container_with_retries',cleanup)
        await live.start_live(PROFILE,'video.mp4',pinned_comment='Test comment',end_after_comment=enabled)
        for _ in range(100):
            if PROFILE not in live.CONTROLS: break
            await asyncio.sleep(.01)
        result = live.get_status(PROFILE)
        assert result['end_after_comment'] is True
        assert actions.count('end') == 1
        assert actions.count('comment') == (1 if end_ok else 0)
        if end_ok:
            assert actions.index('start') < actions.index('end') < actions.index('comment') < actions.index('cleanup')
        assert not any('touch' in command for command in commands)
        assert result['status'] == ('completed' if end_ok and comment_ok and cleanup_ok else 'needs_review')
        if end_ok and comment_ok:
            assert actions.index('comment') < actions.index('finish') < actions.index('cleanup')
            assert result['completion_reason'] == 'comment_verified_after_end'
            assert result['elapsed_seconds'] == 0
        else:
            assert result.get('completion_reason') is None
        if end_ok and not comment_ok: assert result['comment_status'] == 'needs_review'
    asyncio.run(scenario())


@run_async_test
async def test_live_batch_persists_playback_settings(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    queue = {'daily_batches': []}
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = await queue_api.create_batch({'target_profiles': [PROFILE], 'posts': [
        {'type': 'live', 'media_file': 'video.mp4', 'playback_mode': 'manual', 'loop': True, 'muted': True, 'max_duration_seconds': 3600}]})
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
    proc.stdout.readline.side_effect = [b'{"event":"go_live_requested"}\n',
        b'{"event":"go_live_click_sent"}\n', b'{"event":"go_live_dialog_requested"}\n',
        b'{"event":"go_live_dialog_click_sent"}\n', json.dumps({'success': True, 'brain': pin,
        'state': 'prepared' if valid_result else 'live', 'template_id': 'setup_center'}).encode(), b'']
    proc.stdin.write = Mock()
    proc.stdin.close = Mock()
    create = AsyncMock(return_value=proc)
    monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
    if valid_result:
        await live.browser_action(PROFILE, 'prepare', SETTINGS['producer_url'], asyncio.Event())
        public = live.read_json(live.state_path(PROFILE))
        assert public['browser_state'] == 'prepared'
        assert public['browser_template'] == 'setup_center'
        assert public['brain'] == pin
        assert [entry['event'] for entry in public['logs']] == [
            'prepare_requested', 'go_live_requested', 'go_live_click_sent',
            'go_live_dialog_requested', 'go_live_dialog_click_sent', 'prepare_prepared']
        assert all(entry['message'] and entry['level'] for entry in public['logs'])
        assert 'brain_package' not in public
        assert SETTINGS['stream_key'] not in json.dumps(public)
    else:
        with pytest.raises(RuntimeError, match='unverified'):
            await live.browser_action(PROFILE, 'prepare', SETTINGS['producer_url'], asyncio.Event())
    args = create.call_args.args
    assert args[args.index('--brain-digest') + 1] == pin['digest']
    assert SETTINGS['stream_key'] not in ' '.join(args)
    assert 'News title' not in ' '.join(args)
    assert json.loads(proc.stdin.write.call_args.args[0]) == {'title': 'News title', 'caption': 'News caption', 'pinned_comment': 'Pinned message'}


@pytest.mark.parametrize('title,caption', [(None, 'text'), ('Title', None), ('Title', ''), ('', 'Caption'), ('x'*256, 'Caption'), ('Title', 'x'*5001), ('Bad\x00title', 'Caption')])
def test_invalid_live_details(title, caption):
    with pytest.raises(ValueError):
        live.validate_details(title, caption)


@run_async_test
@pytest.mark.parametrize('inside_profile', [True, False])
@pytest.mark.parametrize('success', [True, False])
async def test_browser_failure_links_only_own_profile_evidence(sandbox, monkeypatch, inside_profile, success):
    package = live.resolve_live_brain(live.AUTOMATION_RUNNER.parent, live.ROOT_DIR / 'automation')
    record = {'profile_id': PROFILE, 'task': 'live', 'status': 'running', 'stage': 'preparing',
              'brain': package.metadata(), 'brain_package': str(package.root),
              'title': 'Title', 'caption': 'Caption'}
    automation.active_automation_tasks[PROFILE] = record
    directory = (live.PROFILES_DIR / PROFILE / 'automation_evidence' / 'failure'
                 if inside_profile else sandbox / 'outside')
    directory.mkdir(parents=True)
    proc = AsyncMock()
    proc.returncode = 0
    proc.stdin.write = Mock()
    proc.stdin.close = Mock()
    proc.stdout.readline.side_effect = [json.dumps({'success': success,
        'state': 'unconfirmed', 'brain': package.metadata(),
        'error': 'Live caption review was interrupted (composer_unconfirmed)',
        'evidence_dir': str(directory)}).encode(), b'']
    monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', AsyncMock(return_value=proc))
    if success:
        assert await live.browser_action(PROFILE, 'inspect', SETTINGS['producer_url'], asyncio.Event()) == 'unconfirmed'
    else:
        with pytest.raises(RuntimeError, match='composer_unconfirmed'):
            await live.browser_action(PROFILE, 'prepare', SETTINGS['producer_url'], asyncio.Event())
    public = live.read_json(live.state_path(PROFILE))
    # The job's own folder is made before its first step; a folder outside the profile never replaces it.
    job_folder = Path(public['evidence_dir'])
    assert job_folder.parent == (live.PROFILES_DIR / PROFILE / 'automation_evidence').resolve()
    assert job_folder.name.endswith('_live') and job_folder != directory.resolve()


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


@pytest.mark.parametrize('final_state', ['unconfirmed', 'live', 'ended', 'error'])
@pytest.mark.parametrize('exit_code', [0, 1])
def test_early_encoder_exit_observes_facebook_before_container_cleanup(sandbox, monkeypatch, final_state, exit_code):
    async def scenario():
        encoder = FakeEncoder()
        actions = []
        start_cancelled = asyncio.Event()
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        async def command(*args, **kwargs):
            return b'{"format":{"duration":"10"},"streams":[{"codec_type":"video"}]}'
        async def create(*args, **kwargs):
            return encoder
        async def browser(_, action, url, event, **kwargs):
            if action == 'prepare':
                kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
            actions.append(action)
            if action == 'start':
                live.live_event(automation.active_automation_tasks[PROFILE], 'go_live_requested')
                encoder.finish(exit_code)
                try:
                    await asyncio.Event().wait()
                finally:
                    start_cancelled.set()
            if action == 'inspect':
                assert start_cancelled.is_set()
                if final_state == 'error':
                    raise RuntimeError('Inspection unavailable')
                return final_state
            if action == 'end':
                assert final_state == 'live'
        async def cleanup(_):
            assert 'inspect' in actions
            actions.append('cleanup')
            return {'stopped': True}
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        monkeypatch.setattr(live, 'command', command)
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
        monkeypatch.setattr(live, 'browser_action', browser)
        monkeypatch.setattr(automation, '_stop_container_with_retries', cleanup)
        await live.start_live(PROFILE, 'video.mp4')
        for _ in range(100):
            if PROFILE not in live.CONTROLS:
                break
            await asyncio.sleep(.01)
        saved = live.get_status(PROFILE)
        assert saved['encoder_exit_code'] == exit_code
        assert saved['status'] == ('completed' if final_state == 'live' and exit_code == 0 else 'needs_review')
        assert actions.count('start') == 1
        assert actions[-1] == 'cleanup'
        assert ('end' in actions) == (final_state == 'live')
        assert saved['logs'][0]['event'] == 'go_live_requested'
        if final_state != 'error':
            assert saved['final_browser_state'] == ('ended' if final_state == 'live' else final_state)
        if final_state != 'live' and exit_code == 0:
            assert 'Video finished' in saved['error']
        assert SETTINGS['stream_key'] not in json.dumps(saved)
        if saved['status'] == 'needs_review':
            directory = Path(saved['evidence_dir'])
            assert (directory / 'diagnostics.json').is_file()
            assert live.read_json(directory / 'result.json')['screenshot_available'] is False
    asyncio.run(scenario())


@run_async_test
@pytest.mark.parametrize('reason', ['invalid_stream_key', 'rtmps://private/FB-SECRET', None])
async def test_failure_diagnostics_allowlist_and_screenshot_preservation(sandbox, monkeypatch, reason):
    directory = live.PROFILES_DIR / PROFILE / 'automation_evidence' / 'inspection'
    directory.mkdir(parents=True)
    (directory / '01_failure.png').write_bytes(b'original redacted image')
    record = {'profile_id': PROFILE, 'task': 'live', 'session_id': 'session',
              'status': 'needs_review', 'stage': 'needs_review', 'encoder_exit_code': 1,
              'evidence_dir': str(directory), 'error': 'private raw failure'}
    monkeypatch.setattr(live, 'command', AsyncMock(return_value=json.dumps({
        'failure_reason': reason, 'stderr': 'FB-SECRET'}).encode()))
    await live.save_failure_diagnostics(record)
    report = live.read_json(directory / 'diagnostics.json')
    assert report['encoder_failure_reason'] == (reason if reason == 'invalid_stream_key' else 'unknown_encoder_failure')
    assert 'FB-SECRET' not in json.dumps(report)
    assert 'private raw failure' not in json.dumps(report)
    assert (directory / '01_failure.png').read_bytes() == b'original redacted image'


@run_async_test
async def test_diagnostic_write_failure_preserves_original_error(sandbox, monkeypatch):
    record = {'profile_id': PROFILE, 'status': 'failed', 'stage': 'failed', 'error': 'original'}
    monkeypatch.setattr(live, 'private_write', Mock(side_effect=OSError('disk full')))
    await live.save_failure_diagnostics(record)
    assert record['error'] == 'original'


@run_async_test
async def test_preflight_failure_retains_metadata_without_browser(sandbox):
    record = {'profile_id': PROFILE, 'task': 'live', 'status': 'failed', 'stage': 'failed',
              'error': 'proxy unreachable with private details'}
    await live.save_failure_diagnostics(record, 'preparing')
    directory = Path(record['evidence_dir'])
    assert live.read_json(directory / 'result.json')['screenshot_available'] is False
    report = live.read_json(directory / 'diagnostics.json')
    assert report['failure_stage'] == 'preparing'
    assert report['encoder_exit_code'] is None
    assert 'private details' not in json.dumps(report)


@pytest.mark.parametrize('mode,loop,limit,expected', [
    ('auto', True, 60, 10.154), ('manual', False, None, 10.154),
    ('manual', False, 5, 5), ('manual', True, 60, 60)])
def test_confirmed_playback_starts_at_zero_after_confirmation(sandbox, monkeypatch, mode, loop, limit, expected):
    async def scenario():
        encoder = FakeEncoder()
        lines = asyncio.Queue()
        lines.put_nowait(b'phase=startup\n')
        async def readline():
            return await lines.get()
        encoder.readline = readline
        actions = []
        async def ready(_):
            lifecycle.mark_used(PROFILE, lifecycle.OWNER_TOKEN.get())
        def complete():
            lines.put_nowait(b'out_time_us=1000000\n')
            lines.put_nowait(b'')
            encoder.finish()
        async def command(*args, **kw):
            if 'ffprobe' in args:
                return b'{"format":{"duration":"10.154"},"streams":[{"codec_type":"video"}]}'
            if 'touch' in args:
                record = automation.active_automation_tasks[PROFILE]
                assert record['broadcast_confirmed']
                assert 'start' in actions
                assert record['elapsed_seconds'] == 0
                actions.append('switch')
                lines.put_nowait(b'phase=playback\n')
                asyncio.get_running_loop().call_later(.03, complete)
            else:
                encoder.finish()
            return b''
        async def create(*args, **kw):
            assert args[args.index('--duration') + 1] == str(expected)
            assert ('--loop' in args) == (mode == 'manual' and loop)
            assert SETTINGS['stream_key'] not in ' '.join(args)
            return encoder
        async def browser(_, action, *args, **kwargs):
            if action == 'prepare':
                kwargs['prepared_settings']['stream_key'] = SETTINGS['stream_key']
            actions.append(action)
            if action == 'end':
                assert 'switch' in actions
                assert encoder.done.is_set() and encoder.returncode == 0
        async def cleanup(_):
            return {'stopped': True}
        monkeypatch.setattr(automation, 'ensure_profile_container_ready', ready)
        monkeypatch.setattr(live, 'command', command)
        monkeypatch.setattr(live.asyncio, 'create_subprocess_exec', create)
        monkeypatch.setattr(live, 'browser_action', browser)
        monkeypatch.setattr(automation, '_stop_container_with_retries', cleanup)
        await live.start_live(PROFILE, 'video.mp4', loop=loop, max_duration_seconds=limit, playback_mode=mode)
        for _ in range(100):
            if PROFILE not in live.CONTROLS:
                break
            await asyncio.sleep(.01)
        saved = live.get_status(PROFILE)
        assert saved['status'] == 'completed'
        assert saved['duration_seconds'] == expected
        assert saved['source_duration_seconds'] == 10.154
        assert saved['playback_mode'] == mode
        assert saved['playback_started_at']
        assert saved['broadcast_confirmed']
        assert actions == ['prepare', 'start', 'switch', 'end']
        assert saved['encoder_exit_code'] == 0
        assert saved['loop'] == (mode == 'manual' and loop)
    asyncio.run(scenario())


def test_auto_ignores_hidden_manual_controls():
    assert live.validate_playback(True, 120, True, 'auto') == {
        'playback_mode': 'auto', 'loop': False, 'max_duration_seconds': None, 'muted': True}
    with pytest.raises(ValueError, match='Automatic or Manual'):
        live.validate_playback(playback_mode='unexpected')


@run_async_test
async def test_new_live_batch_defaults_to_auto_and_clears_hidden_limits(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: {'daily_batches': []})
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    result = await queue_api.create_batch({'target_profiles': [PROFILE], 'posts': [
        {'type': 'live', 'media_file': 'video.mp4', 'loop': True, 'max_duration_seconds': 60}]})
    post = result['batch']['posts'][0]
    assert post['playback_mode'] == 'auto'
    assert post['loop'] is False
    assert post['max_duration_seconds'] is None


@run_async_test
async def test_direct_live_api_defaults_to_automatic(sandbox, monkeypatch):
    start = AsyncMock(return_value={'status': 'running', 'stage': 'preparing'})
    monkeypatch.setattr(live, 'start_live', start)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post(f'/api/profiles/{PROFILE}/live/start', json={'filename': 'video.mp4'})
    assert result.status_code == 200
    assert start.call_args.args[-1] == 'auto'
    assert start.call_args.kwargs['end_after_comment'] is False


@run_async_test
@pytest.mark.parametrize('setting, expected', [({}, False), ({'end_after_comment': True}, True), ({'end_after_comment': False}, False)])
async def test_end_after_comment_is_saved_in_batch_and_forwarded_by_direct_api(sandbox, monkeypatch, setting, expected):
    from backend.routers import queue as queue_api
    monkeypatch.setattr(queue_api, 'PROFILES_DIR', live.PROFILES_DIR)
    monkeypatch.setattr(queue_api, 'load_posting_queue', lambda: {'daily_batches': []})
    monkeypatch.setattr(queue_api, 'save_posting_queue', lambda value: True)
    batch = await queue_api.create_batch({'target_profiles':[PROFILE], 'posts':[
        {'type':'live','media_file':'video.mp4','pinned_comment':'Comment',**setting}]})
    assert batch['batch']['posts'][0]['end_after_comment'] is True
    start = AsyncMock(return_value={'status':'running'})
    monkeypatch.setattr(live, 'start_live', start)
    app = FastAPI(); app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        result = await client.post(f'/api/profiles/{PROFILE}/live/start',json={
            'filename':'video.mp4','pinned_comment':'Comment',**setting})
    assert result.status_code == 200
    assert start.call_args.kwargs['end_after_comment'] is expected


def test_older_jobs_keep_manual_options_with_confirmed_startup():
    playback = live.validate_playback(True, 60, False)
    assert playback['playback_mode'] == 'manual'
    assert playback['loop'] is True
    assert playback['max_duration_seconds'] == 60


@run_async_test
async def test_new_direct_live_preserves_old_review_without_blocking(sandbox, monkeypatch):
    previous = {'profile_id': PROFILE, 'session_id': 'old', 'status': 'needs_review',
                'stage': 'needs_review', 'error': 'Unconfirmed previous publication', 'queue_execution_id': 'old-exec'}
    live.private_write(live.state_path(PROFILE), previous)
    async def fake_run(record, *args):
        record['lifecycle_active'] = False
    monkeypatch.setattr(live, 'run_live', fake_run)
    result = await live.start_live(PROFILE, 'video.mp4', playback_mode='auto')
    assert result['status'] == 'running'
    assert result['session_id'] != 'old'
    archives = list((live.LIVE_DIR / PROFILE / 'history').glob('*.json'))
    assert len(archives) == 1
    assert live.read_json(archives[0]) == previous
    assert os.stat(archives[0]).st_mode & 0o777 == 0o600


@run_async_test
async def test_new_live_still_blocks_active_profile_despite_old_review(sandbox):
    live.private_write(live.state_path(PROFILE), {'profile_id': PROFILE, 'status': 'needs_review', 'stage': 'needs_review'})
    live.CONTROLS[PROFILE] = asyncio.Event()
    with pytest.raises(RuntimeError, match='active task'):
        await live.start_live(PROFILE, 'video.mp4')
    assert live.get_status(PROFILE)['status'] == 'needs_review'


@run_async_test
async def test_same_unresolved_live_cannot_bypass_review_via_failed_retry(sandbox, monkeypatch):
    from backend.routers import queue as queue_api
    from fastapi import HTTPException
    record, execution = queue_record(monkeypatch)
    execution.update(status='failed_before_publish', stage='failed')
    prior = {'profile_id': PROFILE, 'status': 'needs_review', 'stage': 'needs_review',
             'queue_execution_id': 'exec_live', 'session_id': 'original-session', 'error': 'Unknown publication'}
    live.private_write(live.state_path(PROFILE), prior)
    monkeypatch.setattr(queue_api, 'load_posting_queue', automation.load_posting_queue)
    save = Mock()
    monkeypatch.setattr(queue_api, 'save_posting_queue', save)
    with pytest.raises(HTTPException) as error:
        await queue_api.run_execution_now('exec_live')
    assert error.value.status_code == 409
    assert 'same Live attempt' in error.value.detail
    save.assert_not_called()
    assert live.get_status(PROFILE) == prior


@run_async_test
async def test_scheduler_cannot_republish_same_unresolved_live(sandbox, monkeypatch):
    record, execution = queue_record(monkeypatch)
    prior = {'profile_id': PROFILE, 'status': 'needs_review', 'stage': 'needs_review',
             'queue_execution_id': 'exec_live', 'session_id': 'original-session', 'error': 'Unknown publication'}
    live.private_write(live.state_path(PROFILE), prior)
    run = AsyncMock()
    monkeypatch.setattr(live, 'run_live', run)
    result = await live.execute_queue_live({'targetExec': execution, 'targetPost': {'type': 'live', 'media_file': 'video.mp4'},
                                          'lease': execution['scheduler_lease']})
    assert result['status'] == 'needs_review'
    assert execution['status'] == 'needs_review'
    assert execution['session_id'] == 'original-session'
    assert live.get_status(PROFILE) == prior
    run.assert_not_called()
