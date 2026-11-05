#!/usr/bin/env python3
"""Development only: install packages the requirements list but this environment lacks.

Dev mode reuses an environment set up from another branch and skips the version check that makes an
installed app run setup again. A package already installed, at any version, is never changed: setup
owns those, and a failure here only prints, because the app can still start without the package.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = (ROOT / 'automation/requirements.txt', ROOT / 'backend/requirements.txt')
# Their bootstrap scripts choose the build for this machine and own the shared cv2 files.
SETUP_OWNED = {'opencv-contrib-python', 'opencv-python-headless', 'easyocr', 'paddleocr', 'paddlex', 'paddlepaddle'}
PIP = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check']
INSTALL_TIMEOUT_S = 600


def installed(name: str) -> bool:
    try:
        version(name)
        return True
    except PackageNotFoundError:
        return False


def missing_requirements(files=REQUIREMENTS) -> list[str]:
    missing = {}
    for path in files:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            requirement = line.split('#')[0].strip()
            match = re.match(r'[A-Za-z0-9._-]+', requirement)
            if not match or ';' in requirement:
                continue
            name = match.group(0).lower().replace('_', '-')
            if name not in SETUP_OWNED and not installed(name):
                missing[name] = requirement
    return list(missing.values())


def would_change(requirements: list[str]) -> list[str]:
    """Already-installed packages that installing these would replace, such as a shared dependency."""
    result = subprocess.run([*PIP, '--dry-run', '--quiet', '--report', '-', *requirements],
                            capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S, check=True)
    planned = [item['metadata']['name'] for item in json.loads(result.stdout)['install']]
    return [name for name in planned if installed(name)]


def sync() -> None:
    requirements = missing_requirements()
    if not requirements:
        return
    print(f'[dev requirements] {sys.executable} is missing: {", ".join(requirements)}', flush=True)
    try:
        changes = would_change(requirements)
        if changes:
            print(f'[dev requirements] Not installed, because it would change {", ".join(changes)}. '
                  'Run setup repair instead.', flush=True)
            return
        subprocess.run([*PIP, '--quiet', *requirements], timeout=INSTALL_TIMEOUT_S, check=True)
        print('[dev requirements] Installed.', flush=True)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        print(f'[dev requirements] Could not install them: {exc}', flush=True)


if __name__ == '__main__':
    sync()
