"""Select the isolated Paddle worker without changing the automation/Torch venv."""
import json
from pathlib import Path
import sys


def paddle_gpu_python(root: Path) -> Path:
    env = root / 'data/python-runtime/paddle-gpu-3.2.2/venv'
    return env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def shared_ocr_python(root: Path, engine: str, default: Path) -> Path:
    if engine == 'easyocr':
        return default
    candidate = paddle_gpu_python(root)
    try:
        status = json.loads((root/'data/paddle_runtime.json').read_text())
        if (status.get('device') == 'cuda'
                and status.get('runtime', {}).get('version') == '3.2.2'
                and Path(status.get('interpreter', '')).absolute() == candidate.absolute()
                and candidate.is_file()):
            return candidate
    except (OSError, ValueError, TypeError):
        pass
    return default
