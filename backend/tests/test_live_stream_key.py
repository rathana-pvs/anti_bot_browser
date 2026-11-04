"""Prepared stream credentials must be fresh, stable and private."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
from modules.live_stream_key import LiveStreamKey
from backend.tests.test_live_service import sandbox, PROFILE, SETTINGS
from backend.services import live_service as live, automation_service as automation


def control(text, x, y):
    return {'text': text, 'center': (x,y), 'bounds': [x-20,y-10,x+20,y+10], 'confidence': .99}


def task_for_key(monkeypatch):
    monkeypatch.setattr('modules.live_stream_key.time.sleep', lambda _: None)
    task = Mock()
    frame = Mock(template_id='setup_center', image=np.zeros((1080,1920,3), dtype=np.uint8),
                 signals={'active_surface': False, 'ended_surface': False},
                 regions={'setup_center': [control('Stream key',650,450), control('Copy',950,485)]})
    task.capture.return_value = frame
    task.vision.read_text.side_effect = lambda *args, **kwargs: frame.regions['setup_center']
    task.same.side_effect = lambda a,b: a['center'] == b['center']
    return task, frame


@pytest.mark.parametrize('copy_x', [450,950])
def test_copies_same_key_twice_from_verified_control(monkeypatch, copy_x):
    task, frame = task_for_key(monkeypatch)
    frame.regions['setup_center'][1] = control('Copy',copy_x,485)
    task.client.read_clipboard.return_value = 'FB-current-prepared-key'
    assert LiveStreamKey(task).read() == 'FB-current-prepared-key'
    assert task.click.call_count == 2


def test_waits_for_copied_button_feedback_before_second_copy(monkeypatch):
    task, frame = task_for_key(monkeypatch)
    initial = frame.regions['setup_center']
    copied = [initial[0], control('Copied!',950,485)]
    task.vision.read_text.side_effect = [initial,initial,copied,initial]
    task.client.read_clipboard.return_value = 'FB-current-prepared-key'
    assert LiveStreamKey(task).read() == 'FB-current-prepared-key'
    assert task.click.call_count == 2


def test_waits_for_checkmark_only_feedback_without_clicking_unknown_control(monkeypatch):
    task, frame = task_for_key(monkeypatch)
    initial = frame.regions['setup_center']
    task.vision.read_text.side_effect = [initial,initial,[initial[0]],initial]
    task.client.read_clipboard.return_value = 'FB-current-prepared-key'
    assert LiveStreamKey(task).read() == 'FB-current-prepared-key'
    assert task.click.call_count == 2


@pytest.mark.parametrize('values', [['FB-current-prepared-key','FB-key-changed-during-copy'],
                                   ['not a key'], ['live-key-probe-unchanged-marker']])
def test_changed_invalid_or_unchanged_clipboard_never_returns_key(monkeypatch, values):
    task, _ = task_for_key(monkeypatch)
    task.client.read_clipboard.side_effect = values
    with pytest.raises(RuntimeError):
        LiveStreamKey(task).read()


@pytest.mark.parametrize('problem', ['active', 'unknown', 'duplicate', 'moved'])
def test_unverified_or_ambiguous_copy_never_reads_secret(monkeypatch, problem):
    task, frame = task_for_key(monkeypatch)
    if problem == 'active': frame.signals['active_surface'] = True
    if problem == 'unknown': frame.template_id = None
    if problem == 'duplicate': frame.regions['setup_center'].append(control('Copy',960,480))
    if problem == 'moved': task.same.return_value = False; task.same.side_effect = None
    with pytest.raises(RuntimeError): LiveStreamKey(task).read()
    task.client.read_clipboard.assert_not_called()


@pytest.mark.parametrize('old_key', ['', SETTINGS['stream_key']])
@pytest.mark.parametrize('key', ['FB-newly-verified-key', None, 'malformed key'])
def test_browser_prepare_uses_key_in_memory_without_saving_it(sandbox, monkeypatch, old_key, key):
    async def scenario():
        saved = {**SETTINGS, 'stream_key': old_key}
        live.private_write(live.settings_path(PROFILE), saved)
        package = live.resolve_live_brain(live.AUTOMATION_RUNNER.parent, live.ROOT_DIR / 'automation')
        record = dict(profile_id=PROFILE, task='live', status='running', stage='preparing',
                      brain=package.metadata(), brain_package=str(package.root))
        automation.active_automation_tasks[PROFILE] = record
        proc=AsyncMock();proc.returncode=0;proc.stdin.write=Mock();proc.stdin.close=Mock()
        proc.stdout.readline.side_effect = [json.dumps({'success':True, 'state':'prepared',
            'brain':package.metadata(), 'prepared_stream_key':key}).encode(), b'']
        monkeypatch.setattr(live.asyncio,'create_subprocess_exec',AsyncMock(return_value=proc))
        private_settings = saved.copy()
        if key == 'FB-newly-verified-key':
            assert await live.browser_action(PROFILE,'prepare',SETTINGS['producer_url'],asyncio.Event(),
                                             prepared_settings=private_settings) == 'prepared'
            assert private_settings['stream_key'] == key
            assert live.read_json(live.settings_path(PROFILE))['stream_key'] == old_key
            assert live.settings_path(PROFILE).stat().st_mode & 0o777 == 0o600
            assert key not in json.dumps(record)
            assert key not in live.state_path(PROFILE).read_text()
            assert key not in json.dumps(live.get_settings(PROFILE))
        else:
            with pytest.raises(RuntimeError,match='could not be verified'):
                await live.browser_action(PROFILE,'prepare',SETTINGS['producer_url'],asyncio.Event(),
                                          prepared_settings=private_settings)
            assert live.read_json(live.settings_path(PROFILE))['stream_key'] == old_key
    asyncio.run(scenario())


def test_settings_allow_initial_missing_key_but_still_validate_destination(sandbox):
    live.private_write(live.settings_path(PROFILE), {**SETTINGS,'stream_key':''})
    assert live.save_settings(PROFILE, {**SETTINGS,'stream_key':''})['key_saved'] is False
    assert live.validate_settings({**SETTINGS,'stream_key':''},require_key=False)['stream_key'] == ''
    with pytest.raises(ValueError):
        live.validate_settings({**SETTINGS,'server_url':'rtmps://untrusted/rtmp/'},require_key=False)


def test_actual_destination_is_excluded_until_sender_cleanup(sandbox):
    first = live.acquire_stream_destination(SETTINGS)
    try:
        with pytest.raises(RuntimeError, match='Another local sender'):
            live.acquire_stream_destination(SETTINGS)
        assert SETTINGS['stream_key'] not in str(first.name)
    finally:
        first.close()
    second = live.acquire_stream_destination(SETTINGS)
    second.close()


def test_launch_ignores_even_malformed_legacy_key(sandbox):
    live.private_write(live.settings_path(PROFILE), {**SETTINGS,'stream_key':'old invalid stored key'})
    assert live.launch_settings(PROFILE)['stream_key'] == ''
