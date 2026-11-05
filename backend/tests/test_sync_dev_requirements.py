import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

path = Path(__file__).resolve().parents[2] / 'automation/sync_dev_requirements.py'
spec = importlib.util.spec_from_file_location('sync_dev_requirements', path)
sync_dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_dev)


@pytest.fixture
def pip(monkeypatch):
    calls = []
    plan = {'install': [{'metadata': {'name': 'newpkg'}}]}

    def run(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(plan))

    monkeypatch.setattr(sync_dev.subprocess, 'run', run)
    monkeypatch.setattr(sync_dev, 'installed', lambda name: name in {'present', 'numpy'})
    return SimpleNamespace(calls=calls, plan=plan)


def test_only_missing_packages_count_whatever_version_is_installed(tmp_path, pip):
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('# comment\npresent==1.0\nnewpkg==2.0  # why\nother_pkg[extra]>=3\n'
                            'paddleocr==3.7.0\nopencv-contrib-python==4.10.0.84\nmarked==1; sys_platform == "win32"\n')
    assert sync_dev.missing_requirements([requirements]) == ['newpkg==2.0', 'other_pkg[extra]>=3']


def test_nothing_missing_never_calls_pip(monkeypatch, pip):
    monkeypatch.setattr(sync_dev, 'missing_requirements', lambda: [])
    sync_dev.sync()
    assert pip.calls == []


def test_missing_package_is_installed_after_a_dry_run(monkeypatch, pip):
    monkeypatch.setattr(sync_dev, 'missing_requirements', lambda: ['newpkg==2.0'])
    sync_dev.sync()
    assert '--dry-run' in pip.calls[0]
    assert pip.calls[1][-1] == 'newpkg==2.0' and '--dry-run' not in pip.calls[1]


def test_install_that_would_change_an_installed_package_is_refused(monkeypatch, pip, capsys):
    monkeypatch.setattr(sync_dev, 'missing_requirements', lambda: ['newpkg==2.0'])
    pip.plan['install'].append({'metadata': {'name': 'numpy'}})
    sync_dev.sync()
    assert len(pip.calls) == 1 and 'numpy' in capsys.readouterr().out


def test_failed_install_never_stops_the_app(monkeypatch, capsys):
    monkeypatch.setattr(sync_dev, 'missing_requirements', lambda: ['newpkg==2.0'])

    def offline(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, 'pip')

    monkeypatch.setattr(sync_dev.subprocess, 'run', offline)
    sync_dev.sync()
    assert 'Could not install' in capsys.readouterr().out
