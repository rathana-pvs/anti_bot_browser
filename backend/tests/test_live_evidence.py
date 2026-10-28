"""Live diagnostics persist masked observations without credentials or raw OCR."""
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
from modules.live_evidence import LiveFailureEvidence


def test_failure_saves_only_control_pixels_and_safe_recognition_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    image = np.full((100, 200, 3), 180, dtype=np.uint8)
    items = [
        {'text': 'Save', 'bounds': [10, 10, 30, 16], 'confidence': .95},
        {'text': 'FB-PRIVATE-STREAM-KEY', 'bounds': [40, 20, 120, 27], 'confidence': .99},
        {'text': 'PRIVATE caption', 'bounds': [40, 40, 120, 47], 'confidence': .99},
        # Even a recognized label may not reveal an oversized OCR box.
        {'text': 'Title', 'bounds': [0, 0, 200, 100], 'confidence': .99},
    ]
    evidence.observe(image, items, 'post_details_dialog', {'save_count': 1, 'heading_found': False})
    assert not (tmp_path / 'profiles').exists()  # No evidence writes on observation/success.
    directory = Path(evidence.save('prepare'))
    saved = cv2.imread(str(directory / '01_failure_redacted.png'))
    expected = np.zeros_like(image)
    expected[10:16, 10:30] = image[10:16, 10:30]
    np.testing.assert_array_equal(saved, expected)
    metadata = json.loads((directory / '01_failure_redacted.json').read_text())
    assert metadata['checks'] == {'save_count': 1, 'heading_found': False}
    assert [c['label'] for c in metadata['controls']] == ['save']
    assert 'PRIVATE' not in json.dumps(metadata)
    assert (directory / 'result.json').is_file()


def test_no_observation_does_not_create_empty_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    assert LiveFailureEvidence('profile_009').save('prepare') is None
    assert not (tmp_path / 'profiles').exists()


def test_latest_observation_replaces_previous_frame(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    image = np.full((100, 200, 3), 180, dtype=np.uint8)
    evidence.observe(image, [{'text': 'Save', 'bounds': [10, 10, 30, 16]}], 'prepare')
    evidence.observe(image, [], 'post_details_dialog', {'save_count': 0})
    directory = Path(evidence.save('prepare'))
    assert not cv2.imread(str(directory / '01_failure_redacted.png')).any()
    assert json.loads((directory / '01_failure_redacted.json').read_text())['checks']['save_count'] == 0


def dialog_observation():
    image = np.full((1000, 1200, 3), 30, dtype=np.uint8)
    cv2.rectangle(image, (350, 100), (850, 900), (245, 245, 245), -1)
    items = [{'text': 'Create post', 'bounds': [500, 120, 640, 145], 'confidence': .98},
             {'text': 'Save', 'bounds': [740, 850, 800, 880], 'confidence': .98}]
    return image, items


def test_modal_evidence_retains_contents_and_masks_credential_background(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    image, items = dialog_observation()
    evidence.observe(image, items, 'post_details_dialog')
    directory = Path(evidence.save('prepare'))
    saved = cv2.imread(str(directory / '01_failure_redacted.png'))
    np.testing.assert_array_equal(saved[300:700, 400:800], image[300:700, 400:800])
    assert not saved[:, :300].any()
    assert not saved[:, 900:].any()
    assert not saved[:90].any()
    assert json.loads((directory / '01_failure_redacted.json').read_text())['redaction'] == 'post_details_dialog_only'


def test_last_confirmed_modal_is_saved_when_latest_frame_cannot_be_identified(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    evidence = LiveFailureEvidence('profile_009')
    image, items = dialog_observation()
    evidence.observe(image, items, 'post_details_dialog')
    evidence.observe(image, [], 'post_details_dialog', {'save_count': 0})
    directory = Path(evidence.save('prepare'))
    assert not cv2.imread(str(directory / '01_failure_redacted.png')).any()
    assert cv2.imread(str(directory / '02_last_confirmed_dialog.png')).any()


def test_credential_panel_is_never_retained_as_dialog():
    image, items = dialog_observation()
    items.append({'text': 'Stream key', 'bounds': [400, 400, 600, 430]})
    assert LiveFailureEvidence.dialog_bounds(image, items) is None


def test_pinned_settings_evidence_excludes_left_and_center_credential_panels():
    image = np.full((100, 200, 3), 180, dtype=np.uint8)
    evidence = LiveFailureEvidence('profile_009')
    evidence.observe(image, [], 'pinned_comment_settings')
    saved, metadata = evidence.observation
    assert not saved[:, :120].any()
    assert not saved[:, 180:].any()
    assert not saved[:13].any()
    np.testing.assert_array_equal(saved[13:99, 120:180], image[13:99, 120:180])
    assert metadata['redaction'] == 'right_post_settings_column_only'


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
