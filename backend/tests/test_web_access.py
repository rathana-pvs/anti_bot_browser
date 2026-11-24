from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import web_access as router_module
from backend.services import web_access as service


def make_client(monkeypatch, status):
    monkeypatch.setattr(service.web_access, 'status', lambda: dict(status))
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


def test_web_visitors_cannot_toggle_or_read_password(monkeypatch):
    client = make_client(monkeypatch, {'enabled': True, 'url': 'https://pc.example.ts.net:8443', 'password': 'secret-value'})
    started = []

    async def start(port):
        started.append(port)

    monkeypatch.setattr(service.web_access, 'turn_on', start)
    web = {'X-Manager-Web-Gateway': '1'}
    assert client.post('/api/system/web-access', json={'enabled': True}, headers=web).status_code == 403
    assert client.post('/api/system/web-access', json={'enabled': False}, headers=web).status_code == 403
    assert not started
    body = client.get('/api/system/web-access', headers=web).json()
    assert body['password'] is None and body['opened_from_web'] is True
    assert client.get('/api/system/web-access').json()['password'] == 'secret-value'


def test_start_errors_are_shown_to_the_user(monkeypatch):
    client = make_client(monkeypatch, {'enabled': False})

    async def start(port):
        raise service.WebAccessError('Tailscale is not signed in on this PC.')

    monkeypatch.setattr(service.web_access, 'turn_on', start)
    response = client.post('/api/system/web-access', json={'enabled': True})
    assert response.status_code == 409
    assert 'not signed in' in response.json()['detail']


import asyncio
import json
import socket
import stat


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def fake_tailscale(monkeypatch, calls, signed_in=lambda: True):
    async def run(*args, **_options):
        calls.append(args)
        if args[0] == 'status':
            state = 'Running' if signed_in() else 'NeedsLogin'
            return 0, json.dumps({'BackendState': state, 'Self': {'DNSName': 'pc.example.ts.net.'}})
        return 0, ''
    monkeypatch.setattr(service, '_tailscale', run)
    monkeypatch.setattr(service, '_tailscale_installed', lambda: True)


def test_remembers_choice_and_password_across_restarts(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'GATEWAY_PORT', free_port())
    monkeypatch.setattr(service, 'web_dist_dir', lambda: (tmp_path / 'web'))
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web' / 'index.html').write_text('<html></html>')
    calls = []
    fake_tailscale(monkeypatch, calls)
    settings = tmp_path / 'data' / 'web_access.json'

    async def scenario():
        first = service.WebAccess(settings)
        on = await first.turn_on(43941)
        assert on['enabled'] and on['remembered']
        assert stat.S_IMODE(settings.stat().st_mode) == 0o600
        await first.shutdown()  # App closes: sharing stops, the choice stays.
        assert json.loads(settings.read_text())['enabled'] is True

        second = service.WebAccess(settings)  # App starts again.
        second.resume(43941)
        for _ in range(100):
            if second.enabled:
                break
            await asyncio.sleep(0.05)
        assert second.status()['password'] == on['password']
        off = await second.turn_off()
        assert not off['enabled'] and not off['remembered']
        assert json.loads(settings.read_text()) == {'enabled': False, 'password': on['password']}

        third = service.WebAccess(settings)
        third.resume(43941)  # Saved as off: stays off after a restart.
        assert third.status()['starting'] is False and not third.enabled
        again = await third.turn_on(43941)  # Off and on again keeps the same password.
        assert again['password'] == on['password']

        fresh = await third.new_password()  # Only this replaces it, and the gateway uses it at once.
        assert fresh['enabled'] and fresh['password'] != on['password']
        assert json.loads(settings.read_text()) == {'enabled': True, 'password': fresh['password']}
        await third.shutdown()

    asyncio.run(scenario())
    assert ('serve', '--https=8443', 'off') in calls


def test_resume_keeps_trying_until_tailscale_is_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'GATEWAY_PORT', free_port())
    monkeypatch.setattr(service, 'RETRY_S', 0.05)
    monkeypatch.setattr(service, 'web_dist_dir', lambda: (tmp_path / 'web'))
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web' / 'index.html').write_text('<html></html>')
    settings = tmp_path / 'web_access.json'
    settings.write_text(json.dumps({'enabled': True, 'password': 'remembered-password-123'}))
    ready = {'value': False}
    fake_tailscale(monkeypatch, [], signed_in=lambda: ready['value'])

    async def scenario():
        access = service.WebAccess(settings)
        access.resume(43941)
        await asyncio.sleep(0.1)
        waiting = access.status()
        assert waiting['starting'] and 'not signed in' in waiting['error']
        ready['value'] = True
        for _ in range(100):
            if access.enabled:
                break
            await asyncio.sleep(0.05)
        assert access.status()['password'] == 'remembered-password-123'
        assert access.status()['error'] is None
        await access.shutdown()

    asyncio.run(scenario())


def test_backend_port_comes_from_env_or_uvicorn_arguments(monkeypatch):
    monkeypatch.setenv('PORT', '43941')
    assert service.backend_port_from_launch() == 43941
    monkeypatch.delenv('PORT')
    monkeypatch.setattr(service.sys, 'argv', ['uvicorn', 'backend.main:app', '--port', '3001'])
    assert service.backend_port_from_launch() == 3001
    monkeypatch.setattr(service.sys, 'argv', ['uvicorn'])
    assert service.backend_port_from_launch() is None


def test_web_visitors_cannot_make_a_new_password(monkeypatch):
    client = make_client(monkeypatch, {'enabled': True})
    made = []

    async def new_password():
        made.append(True)
        return {'enabled': True, 'password': 'new-password-value'}

    monkeypatch.setattr(service.web_access, 'new_password', new_password)
    assert client.post('/api/system/web-access/password', headers={'X-Manager-Web-Gateway': '1'}).status_code == 403
    assert not made
    assert client.post('/api/system/web-access/password').json()['password'] == 'new-password-value'


def test_tailscale_timeout_while_sharing_frees_the_port(monkeypatch, tmp_path):
    port = free_port()
    monkeypatch.setattr(service, 'GATEWAY_PORT', port)
    monkeypatch.setattr(service, 'web_dist_dir', lambda: (tmp_path / 'web'))
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web' / 'index.html').write_text('<html></html>')
    attempts = {'serve': 0}

    async def run(*args, **_options):
        if args[0] == 'status':
            return 0, json.dumps({'BackendState': 'Running', 'Self': {'DNSName': 'pc.example.ts.net.'}})
        if args[0] == 'serve' and args[-1] != 'off':
            attempts['serve'] += 1
            if attempts['serve'] == 1:
                raise service.WebAccessError('Tailscale did not answer in time. Check that it is running, then try again.')
        return 0, ''
    monkeypatch.setattr(service, '_tailscale', run)
    monkeypatch.setattr(service, '_tailscale_installed', lambda: True)

    async def scenario():
        access = service.WebAccess(tmp_path / 'web_access.json')
        try:
            await access.turn_on(43941)
            raise AssertionError('the first attempt should fail')
        except service.WebAccessError as error:
            assert 'did not answer' in str(error)
        with socket.socket() as probe:  # The failed attempt let go of the gateway port.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))
        on = await access.turn_on(43941)  # Trying again works instead of "already in use".
        assert on['enabled']
        await access.shutdown()

    asyncio.run(scenario())


def test_tailscale_timeout_shows_the_page_that_allows_sharing(monkeypatch, tmp_path):
    script = tmp_path / 'tailscale'
    script.write_text('#!/bin/sh\necho "Serve is not enabled on your tailnet."\n'
                      'echo "To enable, visit:"\necho "  https://login.tailscale.com/f/serve?node=abc123"\nsleep 30\n')
    script.chmod(0o755)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    monkeypatch.setattr(service, 'TAILSCALE_TIMEOUT_S', 0.5)
    try:
        asyncio.run(service._tailscale('serve', '--bg', '--https=8443', 'http://127.0.0.1:1'))
        raise AssertionError('should time out')
    except service.WebAccessError as error:
        assert 'https://login.tailscale.com/f/serve?node=abc123' in str(error)


def fresh_pc(monkeypatch, tmp_path, installed):
    """A PC where Tailscale is missing or signed out and has no operator permission yet."""
    monkeypatch.setattr(service, 'GATEWAY_PORT', free_port())
    monkeypatch.setattr(service, 'web_dist_dir', lambda: (tmp_path / 'web'))
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web' / 'index.html').write_text('<html></html>')
    pc = {'installed': installed, 'operator': False, 'signed_in': False, 'serve_allowed': False,
          'admin': [], 'opened': [], 'calls': []}
    monkeypatch.setattr(service, '_tailscale_installed', lambda: pc['installed'])
    monkeypatch.setattr(service, '_download_tailscale_installer', lambda folder: f'{folder}/install-tailscale.sh')
    monkeypatch.setattr(service, '_open_in_browser', pc['opened'].append)

    async def admin(script, purpose):
        pc['admin'].append(script)
        if 'install-tailscale.sh' in script:
            pc['installed'] = True
        if '--operator=' in script:
            pc['operator'] = True
    monkeypatch.setattr(service, '_run_as_admin', admin)

    async def run(*args, timeout=None, on_link=None):
        pc['calls'].append(args)
        if args[0] == 'status':
            state = 'Running' if pc['signed_in'] else 'NeedsLogin'
            return 0, json.dumps({'BackendState': state, 'Self': {'DNSName': 'pc.example.ts.net.'}})
        if args[-1] == 'off':
            return 0, ''
        if not pc['operator']:
            return 1, 'Access denied: prefs write access denied. Use "sudo tailscale set --operator=$USER"'
        if args[0] == 'up':
            on_link('https://login.tailscale.com/a/sign-in-123')
            pc['signed_in'] = True  # The person signs in on the page; "tailscale up" then returns.
            return 0, 'Success.'
        if args[0] == 'serve' and not pc['serve_allowed']:
            on_link('https://login.tailscale.com/f/serve?node=abc')
            pc['serve_allowed'] = True
        return 0, ''
    monkeypatch.setattr(service, '_tailscale', run)
    return pc


def test_turn_on_installs_signs_in_and_allows_sharing_on_a_new_pc(monkeypatch, tmp_path):
    pc = fresh_pc(monkeypatch, tmp_path, installed=False)

    async def scenario():
        access = service.WebAccess(tmp_path / 'web_access.json')
        on = await access.turn_on(43941)
        assert on['enabled'] and on['url'] == 'https://pc.example.ts.net:8443'
        assert on['setup_step'] is None and on['action_url'] is None
        await access.shutdown()

    asyncio.run(scenario())
    assert len(pc['admin']) == 1  # One password window: install and permission together.
    assert 'install-tailscale.sh' in pc['admin'][0] and '--operator=' in pc['admin'][0]
    assert pc['opened'] == ['https://login.tailscale.com/a/sign-in-123', 'https://login.tailscale.com/f/serve?node=abc']


def test_turn_on_asks_for_tailscale_permission_only_when_needed(monkeypatch, tmp_path):
    pc = fresh_pc(monkeypatch, tmp_path, installed=True)

    async def scenario():
        access = service.WebAccess(tmp_path / 'web_access.json')
        assert (await access.turn_on(43941))['enabled']
        await access.turn_off()
        assert (await access.turn_on(43941))['enabled']  # Already set up: no password or page this time.
        await access.shutdown()

    asyncio.run(scenario())
    assert len(pc['admin']) == 1 and pc['admin'][0].startswith('tailscale set --operator=')
    assert len(pc['opened']) == 2


def test_restart_never_asks_for_a_password_or_opens_pages(monkeypatch, tmp_path):
    pc = fresh_pc(monkeypatch, tmp_path, installed=True)
    monkeypatch.setattr(service, 'RETRY_S', 0.05)
    settings = tmp_path / 'web_access.json'
    settings.write_text(json.dumps({'enabled': True, 'password': 'remembered-password-123'}))

    async def scenario():
        access = service.WebAccess(settings)
        access.resume(43941)
        await asyncio.sleep(0.2)
        assert not access.enabled and access.status()['starting']
        await access.shutdown()

    asyncio.run(scenario())
    assert pc['admin'] == [] and pc['opened'] == []
    assert not any(call[0] == 'up' for call in pc['calls'])


def test_closed_password_window_is_explained(monkeypatch, tmp_path):
    pkexec = tmp_path / 'pkexec'
    pkexec.write_text('#!/bin/sh\nexit 126\n')
    pkexec.chmod(0o755)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    try:
        asyncio.run(service._run_as_admin('tailscale set --operator=me', 'give the app permission to use Tailscale'))
        raise AssertionError('should fail')
    except service.WebAccessError as error:
        assert 'password was not entered' in str(error)


def test_tailscale_pages_are_reported_while_the_command_waits(monkeypatch, tmp_path):
    script = tmp_path / 'tailscale'
    script.write_text('#!/bin/sh\necho "To authenticate, visit:"\necho "  https://login.tailscale.com/a/xyz"\n'
                      'sleep 0.3\necho "Success."\n')
    script.chmod(0o755)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    seen = []
    code, output = asyncio.run(service._tailscale('up', timeout=5, on_link=seen.append))
    assert code == 0 and seen == ['https://login.tailscale.com/a/xyz'] and output.endswith('Success.')


def test_turn_on_opens_the_dns_page_and_waits_for_the_https_name(monkeypatch, tmp_path):
    pc = fresh_pc(monkeypatch, tmp_path, installed=True)
    pc['operator'] = pc['signed_in'] = pc['serve_allowed'] = True
    monkeypatch.setattr(service, 'DNS_POLL_S', 0.01)
    name = {'value': 'pc.'}  # MagicDNS/HTTPS off: no ts.net name yet.

    async def run(*args, timeout=None, on_link=None):
        if args[0] == 'status':
            if pc['opened']:  # The person turns them on in the page that opened.
                name['value'] = 'pc.example.ts.net.'
            return 0, json.dumps({'BackendState': 'Running', 'Self': {'DNSName': name['value']}})
        return 0, ''
    monkeypatch.setattr(service, '_tailscale', run)

    async def scenario():
        access = service.WebAccess(tmp_path / 'web_access.json')
        on = await access.turn_on(43941)
        assert on['enabled'] and on['url'] == 'https://pc.example.ts.net:8443'
        await access.shutdown()

        name['value'] = 'pc.'
        pc['opened'].clear()
        restarted = service.WebAccess(tmp_path / 'web_access.json')
        try:  # After a restart it only reports the problem; it never opens pages by itself.
            await restarted.start(43941, 'remembered-password-123')
            raise AssertionError('should fail')
        except service.WebAccessError as error:
            assert 'admin/dns' in str(error)

    asyncio.run(scenario())
    assert pc['opened'] == []
