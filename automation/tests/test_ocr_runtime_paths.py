import json
from pathlib import Path

import pytest

from engine.ocr_runtime_paths import paddle_gpu_python, shared_ocr_python


def marker(root, **updates):
    python = paddle_gpu_python(root)
    python.parent.mkdir(parents=True)
    python.touch()
    data = dict(device='cuda', interpreter=str(python), runtime=dict(version='3.2.2'))
    data.update(updates)
    (root/'data/paddle_runtime.json').write_text(json.dumps(data))
    return python


def test_verified_paddle_environment_is_not_used_for_easyocr(tmp_path):
    target = marker(tmp_path)
    normal = Path('/normal/python')
    assert shared_ocr_python(tmp_path, 'paddleocr-v5', normal) == target
    assert shared_ocr_python(tmp_path, 'paddleocr-v6', normal) == target
    assert shared_ocr_python(tmp_path, 'easyocr', normal) == normal


@pytest.mark.parametrize('updates', [dict(device='cpu'), dict(interpreter='/other/python'),
                                    dict(runtime=dict(version='3.3.0'))])
def test_unverified_marker_does_not_select_gpu(tmp_path, updates):
    marker(tmp_path, **updates)
    normal = Path('/normal/python')
    assert shared_ocr_python(tmp_path, 'paddleocr-v5', normal) == normal


def test_missing_environment_and_invalid_status_use_default(tmp_path):
    normal = Path('/normal/python')
    assert shared_ocr_python(tmp_path, 'paddleocr-v5', normal) == normal
    python = marker(tmp_path)
    python.unlink()
    assert shared_ocr_python(tmp_path, 'paddleocr-v5', normal) == normal
    (tmp_path/'data/paddle_runtime.json').write_text('broken')
    assert shared_ocr_python(tmp_path, 'paddleocr-v5', normal) == normal
