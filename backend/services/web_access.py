"""Turn private web access to this app on and off from Settings.

Turning it on starts the password-protected gateway on loopback and publishes it with
Tailscale Serve, so only devices signed in to the owner's Tailscale network can reach it.
It never uses Funnel and leaves every other Serve/Funnel rule on this PC untouched.
The choice and its password are remembered, so web access comes back after a restart.
The password changes only when the owner asks for a new one.
"""
import asyncio
import getpass
from contextlib import contextmanager, suppress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request

import uvicorn

from backend.config import BACKEND_DIR, DATA_DIR, ROOT_DIR
from backend.services.web_gateway import USERNAME, create_app

logger = logging.getLogger('automat.web_access')

GATEWAY_PORT = int(os.environ.get('MANAGER_WEB_GATEWAY_PORT', '5176'))
HTTPS_PORT = int(os.environ.get('MANAGER_WEB_HTTPS_PORT', '8443'))
TAILSCALE_TIMEOUT_S = 20
RETRY_S = 30
# How long Turn on waits for the person to finish a step: a password window or a Tailscale page.
SETUP_WAIT_S = 300
TAILSCALE_INSTALLER_URL = 'https://tailscale.com/install.sh'
# Where the tailnet owner turns on MagicDNS and HTTPS certificates, which give this PC its https name.
TAILSCALE_DNS_PAGE = 'https://login.tailscale.com/admin/dns'
DNS_POLL_S = 3
_TAILSCALE_LINK = re.compile(r'https://login\.tailscale\.com/\S+')


class WebAccessError(RuntimeError):
    pass


class _NoHttpsName(WebAccessError):
    """Signed in, but MagicDNS or HTTPS certificates are off for this tailnet."""


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


def _system_environment() -> dict[str, str]:
    """Keep the packaged app's bundled libraries out of system programs it runs."""
    env = os.environ.copy()
    if getattr(sys, 'frozen', False) and sys.platform.startswith('linux'):
        original = env.pop('LD_LIBRARY_PATH_ORIG', None)
        if original:
            env['LD_LIBRARY_PATH'] = original
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return env


def _tailscale_installed() -> bool:
    return shutil.which('tailscale') is not None


def _needs_operator(output: str) -> bool:
    lowered = output.lower()
    return 'access denied' in lowered or 'operator' in lowered


async def _tailscale(*args: str, timeout: float | None = None, on_link=None) -> tuple[int, str]:
    """Run a tailscale command. on_link gets the first login.tailscale.com page it prints."""
    binary = shutil.which('tailscale')
    if not binary:
        raise WebAccessError('Tailscale is not installed on this PC. Install it from tailscale.com, sign in, then try again.')
    process = await asyncio.create_subprocess_exec(
        binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, env=_system_environment())
    chunks: list[bytes] = []
    reported = False

    async def read_all() -> None:
        nonlocal reported
        while chunk := await process.stdout.read(4096):
            chunks.append(chunk)
            if on_link and not reported:
                link = _TAILSCALE_LINK.search(b''.join(chunks).decode(errors='replace'))
                if link:
                    reported = True
                    on_link(link.group(0))
        await process.wait()

    try:
        await asyncio.wait_for(read_all(), TAILSCALE_TIMEOUT_S if timeout is None else timeout)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        # "tailscale serve" and "tailscale up" wait, without exiting, until the person finishes on a
        # Tailscale page (allow sharing, or sign in), and print that page. Show it instead of a bare timeout.
        link = _TAILSCALE_LINK.search(b''.join(chunks).decode(errors='replace'))
        if link:
            raise WebAccessError(f'Tailscale is waiting for you to finish on its page. Open {link.group(0)} '
                                 'in a browser, finish there, then try again.') from None
        raise WebAccessError('Tailscale did not answer in time. Check that it is running, then try again.') from None
    except BaseException:
        with suppress(ProcessLookupError):
            process.kill()
        raise
    return process.returncode, b''.join(chunks).decode(errors='replace').strip()


async def _run_as_admin(script: str, purpose: str) -> None:
    """Run a setup command as administrator; Ubuntu asks for the password in its own window."""
    pkexec = shutil.which('pkexec')
    if not pkexec:
        raise WebAccessError(f'This PC cannot ask for the administrator password, so the app could not {purpose}. '
                             f'Run this in a terminal, then try again: sudo sh -c {shlex.quote(script)}')
    process = await asyncio.create_subprocess_exec(
        pkexec, '/bin/sh', '-c', script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, env=_system_environment())
    try:
        output, _ = await asyncio.wait_for(process.communicate(), SETUP_WAIT_S)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise WebAccessError(f'The app waited too long to {purpose}. Turn web access on again.') from None
    if process.returncode in (126, 127):  # pkexec: the password window was closed or the password was wrong.
        raise WebAccessError(f'The password was not entered, so the app could not {purpose}. '
                             'Turn web access on again and enter your password.')
    if process.returncode != 0:
        detail = output.decode(errors='replace').strip()[-300:]
        raise WebAccessError(f'The app could not {purpose}: {detail or "unknown error"}')


def _download_tailscale_installer(folder: str) -> str:
    path = os.path.join(folder, 'install-tailscale.sh')
    try:
        with urllib.request.urlopen(TAILSCALE_INSTALLER_URL, timeout=60) as response, open(path, 'wb') as handle:
            handle.write(response.read())
    except OSError as error:
        raise WebAccessError(f'Tailscale could not be downloaded. Check the internet connection, then try again. ({error})') from None
    return path


def _open_in_browser(link: str) -> None:
    """Open a Tailscale page in this PC's browser. The link is also shown in Settings in case this fails."""
    opener = shutil.which('xdg-open')
    if not opener:
        return
    try:
        subprocess.Popen([opener, link], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=_system_environment(), start_new_session=True)
    except OSError:
        logger.warning('web_access_open_link_failed', extra={'event': 'web_access_open_link_failed'})


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
        raise _NoHttpsName('This PC has no Tailscale HTTPS name. Turn on MagicDNS and HTTPS certificates at https://login.tailscale.com/admin/dns, then try again.')
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
        # While Turn on sets Tailscale up: what it is doing, and the Tailscale page the person must finish.
        self._step: str | None = None
        self._action_url: str | None = None
        self._operator_set = False

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
            'setup_step': self._step,
            'action_url': self._action_url,
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
        self._error = None
        try:
            await self._set_up_tailscale()
            await self.start(backend_port, password, interactive=True)
        except WebAccessError as error:
            self._error = str(error)
            raise
        finally:
            self._step = self._action_url = None
        self._save(True, password)
        return self.status()

    def _show_page(self, step: str):
        def show(link: str) -> None:
            self._step, self._action_url = step, link
            _open_in_browser(link)
        return show

    async def _set_operator(self) -> None:
        """Let this user's app control Tailscale without sudo (asks for the password once)."""
        self._step = 'Enter your password in the window that opened, to let the app use Tailscale.'
        await _run_as_admin(f'tailscale set --operator={shlex.quote(getpass.getuser())}', 'give the app permission to use Tailscale')
        self._operator_set = True

    async def _tailscale_with_permission(self, *args: str, step: str) -> tuple[int, str]:
        """Run a tailscale command for Turn on; if Tailscale needs permission first, ask for it and try again."""
        code, output = await _tailscale(*args, timeout=SETUP_WAIT_S, on_link=self._show_page(step))
        if code != 0 and _needs_operator(output) and not self._operator_set:
            await self._set_operator()
            self._step = None
            code, output = await _tailscale(*args, timeout=SETUP_WAIT_S, on_link=self._show_page(step))
        return code, output

    async def _wait_for_https_name(self) -> str:
        """Open Tailscale's DNS page and continue once MagicDNS and HTTPS certificates are on."""
        self._show_page('Turn on MagicDNS and HTTPS Certificates in the Tailscale page that opened. '
                        'Web access continues by itself after that.')(TAILSCALE_DNS_PAGE)
        deadline = asyncio.get_running_loop().time() + SETUP_WAIT_S
        while True:
            await asyncio.sleep(DNS_POLL_S)
            try:
                name = await tailscale_name()
            except _NoHttpsName:
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                continue
            self._step = self._action_url = None
            return name

    async def _set_up_tailscale(self) -> None:
        """Turn on only: install Tailscale, give permission and sign in, as far as this PC needs."""
        if not _tailscale_installed():
            self._step = 'Downloading Tailscale…'
            with tempfile.TemporaryDirectory(prefix='tailscale-setup-') as folder:
                installer = await asyncio.to_thread(_download_tailscale_installer, folder)
                self._step = 'Enter your password in the window that opened, to install Tailscale.'
                user = shlex.quote(getpass.getuser())
                await _run_as_admin(f'sh {shlex.quote(installer)} && tailscale set --operator={user}', 'install Tailscale')
            self._operator_set = True
            if not _tailscale_installed():
                raise WebAccessError('Tailscale was installed but cannot be found. Restart the app, then try again.')
        code, output = await _tailscale('status', '--json')
        try:
            state = json.loads(output).get('BackendState') if code == 0 or output.startswith('{') else None
        except json.JSONDecodeError:
            state = None
        if state == 'Running':
            return
        self._step = 'Starting Tailscale…'
        code, output = await self._tailscale_with_permission(
            'up', step='Sign in to Tailscale in the browser page that opened. Web access continues by itself after that.')
        if code != 0:
            raise WebAccessError(f'Tailscale sign-in did not finish: {output[-300:] or "unknown error"}')
        self._step = self._action_url = None

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

    async def start(self, backend_port: int, password: str, interactive: bool = False) -> dict:
        """interactive: the person pressed Turn on, so ask for permission and open Tailscale pages."""
        async with self._lock:
            if self.enabled:
                return self.status()
            dist = web_dist_dir()
            if dist is None:
                raise WebAccessError('The web version of the app is missing. Reinstall or update the app '
                                     '(from source: cd manager-app && npm run build).')
            try:
                name = await tailscale_name()
            except _NoHttpsName:
                if not interactive:
                    raise
                name = await self._wait_for_https_name()
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
            serve = ('serve', '--bg', f'--https={HTTPS_PORT}', f'http://127.0.0.1:{GATEWAY_PORT}')
            try:
                if interactive:
                    code, output = await self._tailscale_with_permission(
                        *serve, step='Allow sharing in the Tailscale page that opened. Web access continues by itself after that.')
                else:
                    code, output = await _tailscale(*serve)
            except BaseException:
                # Free the port, or every later attempt fails with "already in use".
                await self._stop_server(server, task)
                raise
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
