"""Install one pinned Paddle runtime: CUDA on supported NVIDIA hosts, else CPU."""
from __future__ import annotations

import json
import argparse
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile

from bootstrap_torch_runtime import nvidia_smi
from engine.runtime_paths import runtime_root

VERSION = '3.2.2'
GPU_INDEX = 'https://www.paddlepaddle.org.cn/packages/stable/cu126/'
STATUS_FILE = runtime_root() / 'data/paddle_runtime.json'


def probe_runtime():
    # A new process is essential after replacing the shared `paddle` package.
    probe = r'''
import json
from importlib.metadata import version, PackageNotFoundError
try:
    import paddle
    compiled = paddle.is_compiled_with_cuda()
    available = bool(compiled and paddle.device.cuda.device_count() > 0)
    packages = {}
    for name in ('paddlepaddle', 'paddlepaddle-gpu'):
        try: packages[name] = version(name)
        except PackageNotFoundError: pass
    if available:
        paddle.set_device('gpu:0')
        # Exercise CUDA and cuDNN, not only the presence of the driver.
        x = paddle.zeros([1, 3, 16, 16])
        weight = paddle.ones([4, 3, 3, 3])
        paddle.nn.functional.conv2d(x, weight).numpy()
    print(json.dumps(dict(version=paddle.__version__, compiled_with_cuda=compiled,
                         cuda_available=available, packages=packages,
                         gpu_name=paddle.device.cuda.get_device_name(0) if available else None)))
except Exception as exc:
    print(json.dumps(dict(error=type(exc).__name__ + ': ' + str(exc))))
'''
    try:
        result = subprocess.run([sys.executable, '-c', probe], capture_output=True,
                                text=True, timeout=120, check=False)
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, OSError, ValueError, IndexError) as exc:
        return {'error': type(exc).__name__}


def ready(device):
    state = probe_runtime()
    expected = 'paddlepaddle-gpu' if device == 'cuda' else 'paddlepaddle'
    return (state.get('version') == VERSION
            and state.get('packages') == {expected: VERSION}
            and (state.get('cuda_available') is True if device == 'cuda'
                 else state.get('compiled_with_cuda') is False))


def install(device):
    package = 'paddlepaddle-gpu' if device == 'cuda' else 'paddlepaddle'
    with tempfile.TemporaryDirectory(prefix='paddle-runtime-') as directory:
        command = [sys.executable, '-m', 'pip', 'download', '--no-deps', '--dest', directory,
                   f'{package}=={VERSION}']
        if device == 'cuda':
            command.extend(['--index-url', GPU_INDEX])
        # Keep the old runtime intact until a replacement wheel is available.
        subprocess.run(command, check=True)
        wheels = list(Path(directory).glob('*.whl'))
        if len(wheels) != 1:
            raise RuntimeError('Expected exactly one pinned Paddle wheel')
        # CPU and GPU distributions own the same import paths; never co-install.
        subprocess.run([sys.executable, '-m', 'pip', 'uninstall', '-y',
                        'paddlepaddle', 'paddlepaddle-gpu'], check=True)
        command = [sys.executable, '-m', 'pip', 'install', str(wheels[0])]
        if device == 'cuda':
            command.extend(['--index-url', GPU_INDEX])
        subprocess.run(command, check=True)


def write_status(device, attempted, reason=None):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(dict(device=device, cuda_attempted=attempted,
        fallback_reason=reason, interpreter=sys.executable, runtime=probe_runtime()), indent=2)+'\n')


def configure_current_environment():
    desired = 'cuda' if nvidia_smi() else 'cpu'
    reason = None
    for device in ([desired, 'cpu'] if desired == 'cuda' else ['cpu']):
        try:
            if not ready(device):
                print(f'Installing PaddlePaddle {VERSION} {device} runtime...', flush=True)
                install(device)
            if not ready(device):
                raise RuntimeError(f'Paddle {device} runtime verification failed')
            write_status(device, desired == 'cuda', reason)
            print(f'PaddlePaddle {VERSION} {device} runtime is ready.', flush=True)
            return 0
        except (subprocess.SubprocessError, RuntimeError, OSError) as exc:
            if device == 'cpu':
                print(f'ERROR: Paddle CPU runtime could not be verified: {exc}', file=sys.stderr)
                return 1
            reason = 'CUDA installation or verification failed; using the CPU fallback.'
            print(f'WARNING: {reason} ({type(exc).__name__})', file=sys.stderr)
    return 1


def gpu_environment():
    return runtime_root() / f'data/python-runtime/paddle-gpu-{VERSION}/venv'


def remove_other_gpu_frameworks():
    """Strip copied Torch/CUDA 13 libraries before installing CUDA 12 wheels."""
    from importlib.metadata import distributions
    names = {d.metadata['Name'].casefold() for d in distributions() if d.metadata.get('Name')}
    remove = names & {'torch', 'torchvision', 'torchaudio', 'easyocr', 'triton',
                      'cuda-toolkit', 'cuda-bindings', 'cuda-pathfinder'}
    if any(name.endswith('-cu13') for name in names):
        remove |= {name for name in names if name.startswith('nvidia-')}
    if remove:
        subprocess.run([sys.executable, '-m', 'pip', 'uninstall', '-y', *sorted(remove)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure-isolated-environment', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.configure_isolated_environment:
        if Path(sys.prefix).resolve() != gpu_environment().resolve():
            parser.error('GPU installation must run in the dedicated Paddle environment')
        remove_other_gpu_frameworks()
        return configure_current_environment()
    if not nvidia_smi():
        # The main requirements provide the CPU build; leave Torch untouched.
        if not ready('cpu'):
            print('ERROR: Paddle CPU baseline is unavailable.', file=sys.stderr)
            return 1
        write_status('cpu', False)
        return 0
    target = gpu_environment()
    if not target.exists():
        print(f'Creating isolated Paddle GPU environment: {target}', flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(sys.prefix, target, symlinks=True)
    python = target / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    result = subprocess.run([str(python), str(Path(__file__).resolve()),
                             '--configure-isolated-environment'], check=False)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
