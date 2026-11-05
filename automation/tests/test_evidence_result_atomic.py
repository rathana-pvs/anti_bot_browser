"""A crash or serialization error cannot expose half a final result."""
import json
from unittest.mock import patch

import pytest

from engine.evidence import EvidenceRecorder


def recorder(tmp_path):
    value = EvidenceRecorder.__new__(EvidenceRecorder)
    value.directory = str(tmp_path)
    value.run_id = 'run_FacebookReelTask'
    return value


def test_complete_result_replaces_previous_snapshot_atomically(tmp_path):
    value = recorder(tmp_path)
    path = tmp_path / 'result.json'
    path.write_text('{"status":"pending_profile_verification"}')
    import os
    replace = os.replace

    def inspect(temporary, destination):
        assert json.loads(path.read_text())['status'] == 'pending_profile_verification'
        with open(temporary) as stream:
            assert json.load(stream)['status'] == 'published'
        replace(temporary, destination)

    with patch('engine.evidence.os.replace', side_effect=inspect):
        value.write_result('published', first_comment='submitted_verified')
    assert json.loads(path.read_text())['first_comment'] == 'submitted_verified'
    assert not list(tmp_path.glob('.result-*.tmp'))


@pytest.mark.parametrize('failure', ['serialization', 'replace'])
def test_failed_write_preserves_previous_result_and_removes_temporary(tmp_path, failure):
    value = recorder(tmp_path)
    path = tmp_path / 'result.json'
    original = '{"status":"uncertain"}'
    path.write_text(original)
    if failure == 'serialization':
        with pytest.raises(TypeError):
            value.write_result('published', unsupported=object())
    else:
        with patch('engine.evidence.os.replace', side_effect=OSError('interrupted')):
            with pytest.raises(OSError):
                value.write_result('published')
    assert path.read_text() == original
    assert not list(tmp_path.glob('.result-*.tmp'))
