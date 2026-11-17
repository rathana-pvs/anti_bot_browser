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
    async def run(*args):
        calls.append(args)
        if args[0] == 'status':
            state = 'Running' if signed_in() else 'NeedsLogin'
            return 0, json.dumps({'BackendState': state, 'Self': {'DNSName': 'pc.example.ts.net.'}})
        return 0, ''
    monkeypatch.setattr(service, '_tailscale', run)


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
