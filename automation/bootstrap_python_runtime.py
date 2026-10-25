#!/usr/bin/env python3
"""Prepare the tested private Python runtime without changing system Python."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import venv

PYTHON_VERSION = '3.12'
UV_VERSION = '0.13.5'


def compatible(python: Path) -> bool:
    try:
        result = subprocess.run(
            [str(python), '-I', '-c',
             'import sys, struct, venv, ensurepip; '
             'raise SystemExit(0 if sys.version_info[:2] == (3, 12) '
             'and struct.calcsize("P") == 8 else 1)'],
            capture_output=True, timeout=15, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def managed_python(root: Path) -> Path:
    storage = root / 'data' / 'python-runtime'
    bootstrap = storage / 'bootstrap'
    uv = bootstrap / 'bin' / 'uv'
    if not uv.is_file():
        print('Preparing the private Python downloader...', flush=True)
        venv.EnvBuilder(with_pip=True).create(bootstrap)
        subprocess.run([
            str(bootstrap / 'bin' / 'python'), '-m', 'pip', 'install',
            '--disable-pip-version-check', '--only-binary=:all:', f'uv=={UV_VERSION}',
        ], check=True)
    env = os.environ.copy()
    env.update(
        UV_PYTHON_INSTALL_DIR=str(storage / 'interpreters'),
        UV_PYTHON_INSTALL_BIN='0',
        UV_CACHE_DIR=str(storage / 'cache'),
        UV_SYSTEM_CERTS='true',
    )
    print(f'Preparing private Python {PYTHON_VERSION} for OCR compatibility...', flush=True)
    subprocess.run([str(uv), '--no-config', 'python', 'install', PYTHON_VERSION],
                   env=env, check=True)
    result = subprocess.run([
        str(uv), '--no-config', 'python', 'find', '--managed-python',
        '--no-python-downloads', PYTHON_VERSION,
    ], env=env, capture_output=True, text=True, check=True)
    python = Path(result.stdout.strip())
    if not python.is_absolute() or not compatible(python):
        raise RuntimeError('The downloaded Python 3.12 runtime could not be verified.')
    return python


def prepare(root: Path) -> Path:
    root = root.resolve()
    environment = root / 'automation' / 'venv'
    python = environment / 'bin' / 'python'
    if compatible(python):
        print(f'Python {PYTHON_VERSION} application environment is already ready.', flush=True)
        return python

    interpreter = None
    for name in ('python3.12', 'python3'):
        candidate = shutil.which(name)
        if candidate and compatible(Path(candidate)):
            interpreter = Path(candidate)
            break
    if interpreter is None:
        interpreter = managed_python(root)

    # Resolve/download a usable interpreter before moving the existing venv.
    # Models, profiles, sessions and configuration are outside this directory.
    backup = None
    if environment.exists() or environment.is_symlink():
        backup = root / 'data' / 'python-runtime' / 'backups' / f'venv-{uuid.uuid4().hex}'
        backup.parent.mkdir(parents=True, exist_ok=True)
        environment.rename(backup)
        print(f'Preserved the incompatible Python environment at {backup}', flush=True)
    try:
        subprocess.run([str(interpreter), '-I', '-m', 'venv', str(environment)], check=True)
        if not compatible(python):
            raise RuntimeError('The new application environment is not Python 3.12.')
    except BaseException:
        if environment.is_symlink():
            environment.unlink()
        elif environment.exists():
            shutil.rmtree(environment)
        if backup is not None:
            backup.rename(environment)
        raise
    # Only the installer's final dependency checks may mark the new venv ready.
    (root / 'data' / 'runtime-installed-version').unlink(missing_ok=True)
    print(f'Python {PYTHON_VERSION} application environment prepared.', flush=True)
    return python


def main() -> int:
    if len(sys.argv) != 2:
        print('Usage: bootstrap_python_runtime.py RUNTIME_ROOT', file=sys.stderr)
        return 2
    try:
        prepare(Path(sys.argv[1]))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'ERROR: Could not prepare Python {PYTHON_VERSION}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
