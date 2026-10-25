"""Run the source web UI and an authenticated backend against desktop data (Linux/WSL)."""
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def stop(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def interrupted(*_):
    raise KeyboardInterrupt


def main():
    if os.name != 'posix':
        raise SystemExit('Run web development in Linux or WSL with the installed Python runtime.')
    source = Path(__file__).resolve().parents[1]
    default_data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    runtime = Path(os.environ.get('AUTOMAT_FB_ROOT', str(default_data / 'com.isolatedbrowser.manager/runtime'))).expanduser().resolve()
    if not (runtime / 'profiles').is_dir():
        raise SystemExit('Desktop runtime not found. Run desktop setup first, or set AUTOMAT_FB_ROOT to your data root.')
    if not (source / 'manager-app/node_modules/.bin/vite').is_file():
        raise SystemExit('Install frontend dependencies first: cd manager-app && npm install')
    os.environ['AUTOMAT_FB_ROOT'] = str(runtime)
    sys.path.insert(0, str(source))
    # Use the same lock as the desktop/service. Never run two schedulers on one queue.
    from backend.runtime_service import exclusive_backend
    try:
        with exclusive_backend():
            pass
    except RuntimeError:
        raise SystemExit('This data root is already in use. Close the desktop app or stop its backend before running npm run dev:web.') from None
    try:
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            check.bind(('127.0.0.1', 5173))
    except OSError:
        raise SystemExit('Port 5173 is already in use. Stop the existing development server first.') from None

    env = os.environ.copy()
    env['MANAGER_API_TOKEN'] = secrets.token_hex(32)
    env['PYTHONPATH'] = str(source)
    # Web development is locally owned, not an independent cloud worker service.
    env.pop('WORKER_SERVICE_MODE', None)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    env['PORT'] = str(port)
    env['MANAGER_DEV_BACKEND_URL'] = f'http://127.0.0.1:{port}'
    children = []
    signal.signal(signal.SIGTERM, interrupted)
    try:
        backend = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'backend.main:app', '--fd', str(listener.fileno())],
            cwd=source, env=env, pass_fds=(listener.fileno(),), start_new_session=True,
        )
        children.append(backend)
        listener.close()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError('Backend startup failed; review the output above.')
            try:
                request = urllib.request.Request(env['MANAGER_DEV_BACKEND_URL'] + '/api/system/runtime-version',
                                                 headers={'X-Manager-Token': env['MANAGER_API_TOKEN']})
                with urllib.request.urlopen(request, timeout=2) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(.25)
        else:
            raise RuntimeError('Backend did not become ready within 60 seconds.')
        frontend = subprocess.Popen(['npm', 'run', 'dev'], cwd=source / 'manager-app', env=env,
                                    start_new_session=True)
        children.append(frontend)
        print('\nWeb app: http://127.0.0.1:5173\nUI changes reload automatically. Restart this command for Python changes.\nCtrl+C stops both servers.\n', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        listener.close()
        for child in reversed(children):
            stop(child)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
