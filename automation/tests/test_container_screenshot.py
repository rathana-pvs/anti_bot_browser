import sys
from pathlib import Path
from unittest.mock import Mock
import cv2
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.container_client import ContainerClient


def test_overlapping_captures_use_distinct_files_and_remove_both(monkeypatch):
    client = ContainerClient('profile_001')
    files, paths, nested = {}, [], []
    def execute(args, **kwargs):
        if args[0] == 'scrot':
            path = args[-1]
            paths.append(path)
            pixels = np.full((4, 4, 3), len(paths)*40, dtype=np.uint8)
            files[path] = cv2.imencode('.png', pixels)[1].tobytes()
            if len(paths) == 1:
                nested.append(client.screenshot())
        elif args[0] == 'rm':
            files.pop(args[-1], None)
        return Mock()
    client.exec_cmd = execute
    monkeypatch.setattr('engine.container_client.subprocess.run', lambda args, **kwargs: Mock(stdout=files[args[-1]]))
    first = client.screenshot()
    assert len(set(paths)) == 2
    assert np.all(first == 40)
    assert np.all(nested[0] == 80)
    assert files == {}


def test_decode_failure_removes_credential_bearing_capture(monkeypatch):
    client = ContainerClient('profile_001')
    client.exec_cmd = Mock()
    monkeypatch.setattr('engine.container_client.subprocess.run', lambda *args, **kwargs: Mock(stdout=b'broken PNG'))
    with pytest.raises(RuntimeError, match='Failed to decode'):
        client.screenshot()
    captured = client.exec_cmd.call_args_list[0].args[0][-1]
    assert client.exec_cmd.call_args_list[-1].args[0] == ['rm', '-f', captured]
