import subprocess
from unittest.mock import Mock, call

import pytest

import bootstrap_paddle_runtime as bootstrap


@pytest.mark.parametrize('device,state,expected', [
    ('cuda', dict(version='3.2.2', packages={'paddlepaddle-gpu': '3.2.2'}, cuda_available=True), True),
    ('cuda', dict(version='3.2.2', packages={'paddlepaddle-gpu': '3.2.2', 'paddlepaddle': '3.2.2'}, cuda_available=True), False),
    ('cuda', dict(version='3.2.2', packages={'paddlepaddle-gpu': '3.2.2'}, cuda_available=False), False),
    ('cpu', dict(version='3.2.2', packages={'paddlepaddle': '3.2.2'}, compiled_with_cuda=False), True),
    ('cpu', dict(version='3.2.2', packages={'paddlepaddle-gpu': '3.2.2'}, compiled_with_cuda=True), False),
])
def test_requires_one_matching_distribution(monkeypatch, device, state, expected):
    monkeypatch.setattr(bootstrap, 'probe_runtime', lambda: state)
    assert bootstrap.ready(device) is expected


def test_gpu_install_downloads_before_removing_cpu(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if 'download' in command:
            directory = command[command.index('--dest')+1]
            (bootstrap.Path(directory)/'paddlepaddle_gpu.whl').touch()
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    bootstrap.install('cuda')
    assert 'download' in commands[0] and bootstrap.GPU_INDEX in commands[0]
    assert 'uninstall' in commands[1]
    assert commands[1][-2:] == ['paddlepaddle', 'paddlepaddle-gpu']
    assert 'install' in commands[2]


def test_failed_download_keeps_existing_runtime(monkeypatch):
    run = Mock(side_effect=subprocess.CalledProcessError(1, 'pip'))
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        bootstrap.install('cuda')
    assert run.call_count == 1
    assert 'download' in run.call_args.args[0]


def test_gpu_failure_verifies_cpu_fallback(monkeypatch):
    monkeypatch.setattr(bootstrap, 'nvidia_smi', lambda: '/usr/bin/nvidia-smi')
    monkeypatch.setattr(bootstrap, 'ready', Mock(side_effect=[False, True, True]))
    install = Mock(side_effect=subprocess.CalledProcessError(1, 'pip'))
    monkeypatch.setattr(bootstrap, 'install', install)
    status = Mock()
    monkeypatch.setattr(bootstrap, 'write_status', status)
    assert bootstrap.configure_current_environment() == 0
    install.assert_called_once_with('cuda')
    assert status.call_args.args[0] == 'cpu'
    assert status.call_args.args[1] is True


def test_unverifiable_cpu_is_an_error(monkeypatch):
    monkeypatch.setattr(bootstrap, 'nvidia_smi', lambda: None)
    monkeypatch.setattr(bootstrap, 'ready', lambda device: False)
    monkeypatch.setattr(bootstrap, 'install', lambda device: None)
    status = Mock()
    monkeypatch.setattr(bootstrap, 'write_status', status)
    assert bootstrap.configure_current_environment() == 1
    status.assert_not_called()


def test_main_delegates_gpu_installation_to_isolated_python(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.sys, 'argv', ['bootstrap_paddle_runtime.py'])
    monkeypatch.setattr(bootstrap, 'nvidia_smi', lambda: '/usr/bin/nvidia-smi')
    target = tmp_path/'venv'
    target.mkdir()
    monkeypatch.setattr(bootstrap, 'gpu_environment', lambda: target)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    assert bootstrap.main() == 0
    command = run.call_args.args[0]
    assert command[0].startswith(str(target))
    assert command[-1] == '--configure-isolated-environment'
    assert 'uninstall' not in command


def test_removes_copied_framework_libraries_not_paddle_dependencies(monkeypatch):
    from types import SimpleNamespace
    import importlib.metadata
    names = ['torch', 'easyocr', 'nvidia-cudnn-cu13', 'nvidia-cublas', 'paddlepaddle', 'numpy']
    monkeypatch.setattr(importlib.metadata, 'distributions', lambda:
                        [SimpleNamespace(metadata={'Name': name}) for name in names])
    run = Mock()
    monkeypatch.setattr(bootstrap.subprocess, 'run', run)
    bootstrap.remove_other_gpu_frameworks()
    command = run.call_args.args[0]
    assert all(name in command for name in names[:4])
    assert 'paddlepaddle' not in command and 'numpy' not in command
