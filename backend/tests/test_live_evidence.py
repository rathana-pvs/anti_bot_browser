"""Live evidence retains full browser frames, independently of OCR success."""
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
from modules.live_evidence import LiveFailureEvidence, screenshot_label


@pytest.mark.parametrize('source,template', [
    ('prepare', None), ('inspect', None), ('end', 'live_dashboard'),
    ('inspect', 'broadcast_ended'), ('post_details_dialog', None),
    ('pinned_comment_settings', None),
])
def test_full_frame_is_saved_even_without_recognized_controls(tmp_path, monkeypatch, source, template):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    image = np.random.default_rng(1).integers(0, 256, (100, 200, 3), dtype=np.uint8)
    evidence.observe(image, [], source, {'template_id': template})
    assert not (tmp_path / 'profiles').exists()
    directory = Path(evidence.save(source))
    stem = '01_' + screenshot_label(source, 'failed')
    saved = cv2.imread(str(directory / f'{stem}.png'))
    np.testing.assert_array_equal(saved, image)
    metadata = json.loads((directory / f'{stem}.json').read_text())
    assert metadata['redaction'] == 'none'
    assert metadata['checks'] == {'template_id': template}
    assert json.loads((directory / 'result.json').read_text())['redacted'] is False
    assert len(list(directory.glob('*.png'))) == 1  # No cropped replacement.


def test_observation_copies_frame_and_latest_frame_replaces_previous(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    evidence.observe(np.zeros((100, 200, 3), dtype=np.uint8), [], 'prepare')
    image = np.full((100, 200, 3), 180, dtype=np.uint8)
    evidence.observe(image, [], 'inspect')
    image[:] = 0  # Later screenshot-buffer reuse must not change evidence.
    directory = Path(evidence.save('inspect'))
    assert (cv2.imread(str(directory / '01_check_failed.png')) == 180).all()


def test_metadata_keeps_controls_without_copying_arbitrary_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    image = np.full((100, 200, 3), 180, dtype=np.uint8)
    evidence.observe(image, [
        {'text': 'Save', 'bounds': [10, 10, 30, 16], 'confidence': .95},
        {'text': 'FB-PRIVATE-STREAM-KEY', 'bounds': [40, 20, 120, 27]},
    ], 'prepare')
    directory = Path(evidence.save('prepare'))
    metadata = json.loads((directory / '01_live_setup_failed.json').read_text())
    assert [c['label'] for c in metadata['controls']] == ['save']
    assert 'PRIVATE' not in json.dumps(metadata)
    np.testing.assert_array_equal(cv2.imread(str(directory / '01_live_setup_failed.png')), image)


def test_no_observation_does_not_create_empty_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    assert LiveFailureEvidence('profile_009').save('prepare') is None
    assert not (tmp_path / 'profiles').exists()


def test_confirmed_comment_saves_exact_last_frame(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_007')
    evidence.observe(np.zeros((100, 200, 3), dtype=np.uint8), [], 'live_comment')
    confirmed = np.random.default_rng(7).integers(0, 256, (100, 200, 3), dtype=np.uint8)
    evidence.observe(confirmed, [], 'live_comment')
    directory = Path(evidence.save('comment', status='confirmed'))
    np.testing.assert_array_equal(cv2.imread(str(directory / '01_comment_confirmed.png')), confirmed)
    assert json.loads((directory / 'result.json').read_text())['status'] == 'confirmed'


@pytest.mark.parametrize('capture_result', ['saved', 'missing', 'error'])
def test_confirmed_comment_returns_evidence_without_changing_success(monkeypatch, capture_result):
    import live_browser

    task = Mock()
    task.run.return_value = {'success': True, 'state': 'commented'}
    task.failure_evidence.save.return_value = '/profile/automation_evidence/comment' if capture_result == 'saved' else None
    if capture_result == 'error':
        task.failure_evidence.save.side_effect = OSError('disk full')
    monkeypatch.setattr(live_browser, 'FacebookLiveTask', Mock(return_value=task))
    monkeypatch.setattr(sys, 'argv', ['live_browser', '--profile', 'profile_007', '--action', 'comment',
                                    '--producer-url', 'https://www.facebook.com/live/producer/'])
    output = StringIO()
    monkeypatch.setattr(sys, 'stdin', StringIO('{"comment_after_end":true}'))
    monkeypatch.setattr(sys, 'stdout', output)
    live_browser.main()
    result = json.loads(output.getvalue())
    task.failure_evidence.save.assert_called_once_with('comment', status='confirmed', state='commented', directory=None)
    task.run.assert_called_once()  # Evidence failure must never repost.
    assert result['success'] is True
    assert result['state'] == 'commented'
    assert result.get('evidence_capture_failed', False) == (capture_result != 'saved')
    assert ('evidence_dir' in result) == (capture_result == 'saved')


@pytest.mark.parametrize('capture_fails', [True, False])
def test_browser_cli_preserves_original_failure_and_returns_evidence(monkeypatch, capture_fails):
    import live_browser

    task = Mock()
    task.run.side_effect = RuntimeError('Live caption review was interrupted (composer_unconfirmed)')
    if capture_fails:
        task.failure_evidence.save.side_effect = OSError('disk full')
    else:
        task.failure_evidence.save.return_value = '/profile/automation_evidence/run'
    monkeypatch.setattr(live_browser, 'FacebookLiveTask', Mock(return_value=task))
    monkeypatch.setattr(sys, 'argv', ['live_browser', '--profile', 'profile_009', '--action', 'prepare',
                                    '--producer-url', 'https://www.facebook.com/live/producer/'])
    output = StringIO()
    monkeypatch.setattr(sys, 'stdin', StringIO('{}'))
    monkeypatch.setattr(sys, 'stdout', output)
    live_browser.main()
    result = json.loads(output.getvalue())
    assert result['success'] is False
    assert 'composer_unconfirmed' in result['error']
    if capture_fails:
        assert result['evidence_capture_failed'] is True
    else:
        assert result['evidence_dir'] == '/profile/automation_evidence/run'


@pytest.mark.parametrize('state', ['live', 'ended', 'unconfirmed'])
@pytest.mark.parametrize('capture_fails', [False, True])
def test_read_only_inspection_retains_evidence_without_changing_result(monkeypatch, state, capture_fails):
    import live_browser

    task = Mock()
    task.run.return_value = {'success': True, 'state': state}
    task.failure_evidence.save.return_value = '/profile/automation_evidence/inspection'
    if capture_fails:
        task.failure_evidence.save.side_effect = OSError('disk full')
    monkeypatch.setattr(live_browser, 'FacebookLiveTask', Mock(return_value=task))
    monkeypatch.setattr(sys, 'argv', ['live_browser', '--profile', 'profile_009', '--action', 'inspect',
                                    '--producer-url', 'https://www.facebook.com/live/producer/'])
    output = StringIO()
    monkeypatch.setattr(sys, 'stdin', StringIO('{}'))
    monkeypatch.setattr(sys, 'stdout', output)
    live_browser.main()
    result = json.loads(output.getvalue())
    assert result['success'] is True
    assert result['state'] == state
    task.failure_evidence.save.assert_called_once_with('inspect', status='observed', state=state, directory=None)
    assert result.get('evidence_capture_failed', False) == capture_fails
    assert ('evidence_dir' in result) != capture_fails


def test_comment_failure_returns_fixed_reason_code(monkeypatch):
    import live_browser
    from live_comment_status import LiveCommentFailure
    task = Mock()
    task.run.side_effect = LiveCommentFailure('entry_deadline_exceeded')
    task.failure_evidence.save.return_value = None
    monkeypatch.setattr(live_browser, 'FacebookLiveTask', Mock(return_value=task))
    monkeypatch.setattr(sys, 'argv', ['live_browser', '--profile', 'profile_009', '--action', 'comment',
                                    '--producer-url', 'https://www.facebook.com/live/producer/'])
    output = StringIO()
    monkeypatch.setattr(sys, 'stdin', StringIO('{"pinned_comment":"PRIVATE COMMENT"}'))
    monkeypatch.setattr(sys, 'stdout', output)
    live_browser.main()
    result = json.loads(output.getvalue())
    assert result['failure_code'] == 'entry_deadline_exceeded'
    assert 'PRIVATE COMMENT' not in output.getvalue()


@pytest.mark.parametrize('action,state', [('prepare', 'prepared'), ('start', 'live'), ('end', 'ended')])
def test_each_live_step_that_worked_saves_into_the_job_folder(monkeypatch, action, state):
    import live_browser

    task = Mock()
    task.run.return_value = {'success': True, 'state': state}
    task.failure_evidence.save.return_value = '/profile/automation_evidence/job_live'
    monkeypatch.setattr(live_browser, 'FacebookLiveTask', Mock(return_value=task))
    monkeypatch.setenv('LIVE_EVIDENCE_DIR', '/profile/automation_evidence/job_live')
    monkeypatch.setattr(sys, 'argv', ['live_browser', '--profile', 'profile_009', '--action', action,
                                    '--producer-url', 'https://www.facebook.com/live/producer/'])
    monkeypatch.setattr(sys, 'stdin', StringIO('{}'))
    monkeypatch.setattr(sys, 'stdout', StringIO())
    live_browser.main()
    task.failure_evidence.save.assert_called_once_with(
        action, status='confirmed', state=state, directory='/profile/automation_evidence/job_live')


def test_one_live_job_keeps_every_step_in_order_with_clear_names(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    job = tmp_path / 'profiles' / 'profile_009' / 'automation_evidence' / '20261108T000000_000000Z_live'
    job.mkdir(parents=True)
    for action, status, state in [('prepare', 'confirmed', 'prepared'), ('start', 'confirmed', 'live'),
                                  ('end', 'confirmed', 'ended'), ('comment', 'failed', None),
                                  ('inspect', 'observed', 'ended')]:
        evidence = LiveFailureEvidence('profile_009')  # each step is its own process
        evidence.observe(np.zeros((10, 10, 3), dtype=np.uint8), [], action)
        assert evidence.save(action, status, state=state, directory=str(job)) == str(job)
    assert sorted(path.name for path in job.glob('*.png')) == [
        '01_ready_to_go_live.png', '02_live_confirmed.png', '03_live_ended.png',
        '04_comment_not_confirmed.png', '05_check_live_ended.png']


def test_a_folder_outside_the_profile_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    evidence.observe(np.zeros((10, 10, 3), dtype=np.uint8), [], 'start')
    with pytest.raises(ValueError):
        evidence.save('start', 'confirmed', state='live', directory=str(tmp_path / 'elsewhere'))
