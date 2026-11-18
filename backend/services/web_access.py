"""Turn private web access to this app on and off from Settings.

Turning it on starts the password-protected gateway on loopback and publishes it with
Tailscale Serve, so only devices signed in to the owner's Tailscale network can reach it.
It never uses Funnel and leaves every other Serve/Funnel rule on this PC untouched.
The choice and its password are remembered, so web access comes back after a restart.
The password changes only when the owner asks for a new one.
"""
import asyncio
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys

import uvicorn

from backend.config import BACKEND_DIR, DATA_DIR, ROOT_DIR
from backend.services.web_gateway import USERNAME, create_app

logger = logging.getLogger('automat.web_access')

GATEWAY_PORT = int(os.environ.get('MANAGER_WEB_GATEWAY_PORT', '5176'))
HTTPS_PORT = int(os.environ.get('MANAGER_WEB_HTTPS_PORT', '8443'))
TAILSCALE_TIMEOUT_S = 20
RETRY_S = 30


class WebAccessError(RuntimeError):
    pass


class _EmbeddedServer(uvicorn.Server):
    """Runs inside the backend's event loop; the backend keeps its own signal handling."""

    @contextmanager
    def capture_signals(self):
        yield


def backend_port_from_launch() -> int | None:
    """The port this backend was started on: PORT, or uvicorn's --port argument."""
    value = os.environ.get('PORT')
    if not value and '--port' in sys.argv[:-1]:
        value = sys.argv[sys.argv.index('--port') + 1]
    try:
        port = int(value or '')
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def web_dist_dir() -> Path | None:
    """The built web UI: shipped with the runtime, or the source build during development."""
    candidates = [os.environ.get('MANAGER_WEB_DIST'), ROOT_DIR / 'web', BACKEND_DIR.parent / 'manager-app' / 'dist']
    for candidate in candidates:
        if candidate and (Path(candidate) / 'index.html').is_file():
            return Path(candidate)
    return None


async def _tailscale(*args: str) -> tuple[int, str]:
    binary = shutil.which('tailscale')
    if not binary:
        raise WebAccessError('Tailscale is not installed on this PC. Install it from tailscale.com, sign in, then try again.')
    process = await asyncio.create_subprocess_exec(
        binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), TAILSCALE_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise WebAccessError('Tailscale did not answer in time. Check that it is running, then try again.') from None
    return process.returncode, output.decode(errors='replace').strip()


async def tailscale_name() -> str:
    code, output = await _tailscale('status', '--json')
    try:
        status = json.loads(output) if code == 0 else {}
    except json.JSONDecodeError:
        status = {}
    if status.get('BackendState') != 'Running':
        raise WebAccessError('Tailscale is not signed in on this PC. Run "sudo tailscale up", sign in, then try again.')
    name = ((status.get('Self') or {}).get('DNSName') or '').rstrip('.').lower()
    if not name.endswith('.ts.net'):
        raise WebAccessError('This PC has no Tailscale HTTPS name. Turn on MagicDNS and HTTPS certificates in the Tailscale admin console.')
    return name


class WebAccess:
    def __init__(self, settings_path: Path | None = None):
        self._lock = asyncio.Lock()
        self._server: _EmbeddedServer | None = None
        self._task: asyncio.Task | None = None
        self._url: str | None = None
        self._password: str | None = None
        self._settings_path = settings_path or DATA_DIR / 'web_access.json'
        self._resume_task: asyncio.Task | None = None
        self._error: str | None = None
        self._backend_port: int | None = None

    @property
    def enabled(self) -> bool:
        return self._task is not None and not self._task.done()

    def _saved(self) -> dict:
        try:
            saved = json.loads(self._settings_path.read_text())
            return saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, enabled: bool, password: str | None) -> None:
        """Only this user can read the file: it holds the web login password."""
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._settings_path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump({'enabled': enabled, 'password': password}, handle)
        os.replace(temporary, self._settings_path)

    def status(self) -> dict:
        on = self.enabled
        return {
            'enabled': on,
            'remembered': bool(self._saved().get('enabled')),
            'starting': not on and self._resume_task is not None and not self._resume_task.done(),
            'error': None if on else self._error,
            'url': self._url if on else None,
            'username': USERNAME if on else None,
            'password': self._password if on else None,
            'https_port': HTTPS_PORT,
            'web_app_built': web_dist_dir() is not None,
        }

    def _saved_password(self) -> str | None:
        password = self._saved().get('password')
        return password if isinstance(password, str) and len(password) >= 16 else None

    async def turn_on(self, backend_port: int) -> dict:
        """The Settings button. Keeps the saved password; makes one only the first time."""
        self._cancel_resume()
        if self.enabled:
            return self.status()
        password = self._saved_password() or secrets.token_urlsafe(18)
        try:
            await self.start(backend_port, password)
        except WebAccessError as error:
            self._error = str(error)
            raise
        self._save(True, password)
        return self.status()

    async def turn_off(self) -> dict:
        self._cancel_resume()
        self._save(False, self._saved_password())
        self._error = None
        return await self.stop()

    async def new_password(self) -> dict:
        """Replace the password; devices signed in with the old one must sign in again."""
        password = secrets.token_urlsafe(18)
        self._save(bool(self._saved().get('enabled')), password)
        if self.enabled and self._backend_port:
            port = self._backend_port
            await self.stop()
            await self.start(port, password)
        return self.status()

    def resume(self, backend_port: int | None) -> None:
        """At app start, turn web access back on if it was on, with the same password."""
        password = self._saved_password()
        if not self._saved().get('enabled') or password is None:
            return
        if not backend_port:
            self._error = 'The app could not find its own address, so web access did not turn back on. Turn it on again.'
            return
        self._resume_task = asyncio.create_task(self._resume(backend_port, password))

    async def _resume(self, backend_port: int, password: str) -> None:
        # Tailscale can come up after the app when the PC starts, so keep trying until it works.
        while True:
            try:
                await self.start(backend_port, password)
                self._error = None
                return
            except Exception as error:  # Keep retrying; the reason is shown in Settings.
                self._error = str(error)
                logger.warning('web_access_resume_failed', extra={'event': 'web_access_resume_failed', 'detail': str(error)})
            await asyncio.sleep(RETRY_S)

    def _cancel_resume(self) -> None:
        if self._resume_task is not None and not self._resume_task.done():
            self._resume_task.cancel()
        self._resume_task = None

    async def shutdown(self) -> None:
        """App closing: stop sharing now, but keep the saved choice for the next start."""
        self._cancel_resume()
        await self.stop()

    async def start(self, backend_port: int, password: str) -> dict:
        async with self._lock:
            if self.enabled:
                return self.status()
            dist = web_dist_dir()
            if dist is None:
                raise WebAccessError('The web version of the app is missing. Reinstall or update the app '
                                     '(from source: cd manager-app && npm run build).')
            name = await tailscale_name()
            url = f'https://{name}' + ('' if HTTPS_PORT == 443 else f':{HTTPS_PORT}')
            app = create_app('127.0.0.1', GATEWAY_PORT, password, upstream_port=backend_port, public_origin=url,
                             static_dir=dist, backend_token=os.environ.get('MANAGER_API_TOKEN', ''))
            # Bind here: a failed bind inside uvicorn calls sys.exit, which would stop the whole backend.
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(('127.0.0.1', GATEWAY_PORT))
                listener.listen(128)
            except OSError:
                listener.close()
                raise WebAccessError(f'Port {GATEWAY_PORT} on this PC is already in use, so web access could not start.') from None
            # Screen frames are already compressed by VNC, and deflating them again on this event loop
            # halved the frame rate and slowed every other backend request.
            server = _EmbeddedServer(uvicorn.Config(app, log_level='warning', access_log=False, proxy_headers=False,
                                                    ws_per_message_deflate=False))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started and not task.done():
                await asyncio.sleep(0.05)
            if not server.started:
                listener.close()
                raise WebAccessError('Web access could not start. Review the app log for details.')
            code, output = await _tailscale('serve', '--bg', f'--https={HTTPS_PORT}', f'http://127.0.0.1:{GATEWAY_PORT}')
            if code != 0:
                await self._stop_server(server, task)
                if 'access denied' in output.lower() or 'operator' in output.lower():
                    raise WebAccessError('Tailscale needs permission once. Run "sudo tailscale set --operator=$USER" '
                                         'in a terminal on this PC, then try again.')
                raise WebAccessError(f'Tailscale could not share the app: {output[-300:] or "unknown error"}')
            self._server, self._task, self._url, self._password = server, task, url, password
            self._backend_port = backend_port
            logger.info('web_access_enabled', extra={'event': 'web_access_enabled'})
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            if self._task is None:
                return self.status()
            server, task = self._server, self._task
            self._server = self._task = self._url = self._password = None
            try:
                code, output = await _tailscale('serve', f'--https={HTTPS_PORT}', 'off')
                if code != 0:
                    logger.warning('web_access_serve_off_failed', extra={'event': 'web_access_serve_off_failed', 'detail': output[-300:]})
            except WebAccessError as error:
                logger.warning('web_access_serve_off_failed', extra={'event': 'web_access_serve_off_failed', 'detail': str(error)})
            await self._stop_server(server, task)
            logger.info('web_access_disabled', extra={'event': 'web_access_disabled'})
            return self.status()

    @staticmethod
    async def _stop_server(server, task):
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception:  # The gateway is already gone; nothing else to clean up.
            pass


web_access = WebAccess()
