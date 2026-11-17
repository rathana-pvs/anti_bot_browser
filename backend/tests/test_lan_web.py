import argparse
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from scripts import lan_web

HOST = '192.168.1.20'
BASE = f'http://{HOST}:5173'
PASSWORD = 'test-password-long-enough'


@pytest.fixture
def gateway(monkeypatch):
    calls = []
    real_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        if request.url.path == '/api/profiles':
            return httpx.Response(200, stream=httpx.ByteStream(
                b'[{"id":"profile_001","status":"running","container":{"ws_port":6081}}]'),
                headers={'Content-Type': 'application/json'})
        return httpx.Response(200, stream=httpx.ByteStream(b'web app or API response'), headers={'X-Upstream': 'yes'})

    monkeypatch.setattr(lan_web.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)))
    with TestClient(lan_web.create_app(HOST, 5173, PASSWORD), base_url=BASE) as client:
        yield client, calls


@pytest.mark.parametrize('path', ['/', '/api/profiles', '/shared_media/video.mp4', '/@vite/client'])
def test_lan_requires_login_before_serving_pages_api_or_media(gateway, path):
    client, calls = gateway
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers['www-authenticate'].startswith('Basic ')
    assert not calls


@pytest.mark.parametrize('authorization', ['Basic !!!!', 'Bearer abc', 'Basic bWFuYWdlcjp3cm9uZw=='])
def test_lan_rejects_invalid_credentials(gateway, authorization):
    client, calls = gateway
    assert client.get('/', headers={'Authorization': authorization}).status_code == 401
    assert not calls


def test_login_creates_http_only_session_and_streams_to_fixed_loopback(gateway):
    client, calls = gateway
    response = client.get('/', auth=('manager', PASSWORD))
    assert response.status_code == 200
    assert response.content == b'web app or API response'
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert response.headers['cache-control'] == 'no-store'
    response = client.post('/api/example?mode=test', content=b'payload', headers={
        'Origin': BASE, 'X-Manager-Token': 'client-supplied-secret',
        'X-Forwarded-For': 'spoofed', 'Forwarded': 'spoofed',
    })
    assert response.status_code == 200
    request = calls[-1]
    assert str(request.url) == 'http://127.0.0.1:5173/api/example?mode=test'
    assert request.content == b'payload'
    assert request.headers['origin'] == 'http://127.0.0.1:5173'
    assert all(key not in request.headers for key in ['authorization', 'cookie', 'x-manager-token', 'x-forwarded-for', 'forwarded'])


def test_lan_blocks_cross_origin_and_wrong_host_even_with_login(gateway):
    client, calls = gateway
    for headers in [{'Origin': 'https://untrusted.example'}, {'Origin': 'null'}, {'Host': 'untrusted.example:5173'}]:
        assert client.post('/api/example', auth=('manager', PASSWORD), headers=headers).status_code == 403
    assert not calls


def test_restarting_gateway_invalidates_previous_session(gateway):
    client, calls = gateway
    client.get('/', auth=('manager', PASSWORD))
    saved = client.cookies.get(lan_web.COOKIE)
    with TestClient(lan_web.create_app(HOST, 5173, PASSWORD), base_url=BASE) as restarted:
        assert restarted.get('/', headers={'Cookie': f'{lan_web.COOKIE}={saved}'}).status_code == 401


@pytest.mark.parametrize('address', ['0.0.0.0', '127.0.0.1', '8.8.8.8', '::', '::1', 'hostname'])
def test_gateway_requires_a_specific_private_ipv4(address):
    with pytest.raises(argparse.ArgumentTypeError):
        lan_web.private_address(address)


def test_gateway_rejects_weak_password():
    with pytest.raises(ValueError, match='16 characters'):
        lan_web.create_app(HOST, 5173, 'short')


@pytest.mark.parametrize('public_origin', ['http://manager.example.ts.net', 'https://example.com',
    'https://manager.example.ts.net.evil.test', 'https://user:password@manager.example.ts.net',
    'https://manager.example.ts.net/path', 'https://manager.example.ts.net?extra=1'])
def test_public_gateway_rejects_untrusted_origins(public_origin):
    with pytest.raises(ValueError):
        lan_web.create_app('127.0.0.1', 5175, PASSWORD, public_origin=public_origin)


def test_public_gateway_must_bind_loopback():
    with pytest.raises(ValueError, match='loopback'):
        lan_web.create_app(HOST, 5175, PASSWORD, public_origin='https://manager.example.ts.net')


@pytest.mark.parametrize('mode', ['tls', 'funnel'])
def test_https_login_cookie_and_phone_websocket(mode, monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(lan_web.httpx, 'AsyncClient', lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(b'app')))))
    public = 'https://manager.example.ts.net' if mode == 'funnel' else f'https://{HOST}:5173'
    app = (lan_web.create_app('127.0.0.1', 5175, PASSWORD, public_origin=public) if mode == 'funnel'
           else lan_web.create_app(HOST, 5173, PASSWORD, tls=True))

    class Remote:
        subprotocol = None

        def __init__(self):
            self.frames = asyncio.Queue()

        async def send(self, frame):
            await self.frames.put(frame)

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.frames.get()

    @asynccontextmanager
    async def connect(url, **kwargs):
        assert url == 'ws://127.0.0.1:5173/api/devices/phone/live?user_id=10'
        assert kwargs['origin'] == 'http://127.0.0.1:5173'
        yield Remote()

    monkeypatch.setattr(lan_web, 'connect', connect)
    with TestClient(app, base_url=public) as client:
        assert client.get('/').status_code == 401
        response = client.get('/', auth=('manager', PASSWORD))
        assert response.status_code == 200
        assert 'Secure' in response.headers['set-cookie']
        assert 'HttpOnly' in response.headers['set-cookie']
        assert client.post('/api/example', headers={'Origin':public.replace('https:', 'http:')}).status_code == 403
        assert client.get(public.replace('https:', 'http:') + '/').status_code == 401
        if mode == 'funnel':
            # Funnel can rewrite Host to its fixed loopback target; unrelated hosts still fail.
            assert client.get('/', headers={'Host':'127.0.0.1:5175'}).status_code == 200
            assert client.get('/', headers={'Host':'other.example.ts.net'}).status_code == 403
        with client.websocket_connect(public.replace('https:', 'wss:') + '/api/devices/phone/live?user_id=10',
                                      headers={'Origin':public}) as socket:
            socket.send_bytes(b'phone video')
            assert socket.receive_bytes() == b'phone video'
            socket.send_text('{"type":"key"}')
            assert socket.receive_text() == '{"type":"key"}'


def test_websocket_rejects_missing_login_and_cross_origin(gateway):
    client, calls = gateway
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(BASE.replace('http:', 'ws:') + '/api/live', headers={'Origin': BASE}):
            pytest.fail('Unauthenticated WebSocket connected')
    client.get('/', auth=('manager', PASSWORD))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(BASE.replace('http:', 'ws:') + '/api/live', headers={'Origin': 'https://untrusted.example'}):
            pytest.fail('Cross-origin WebSocket connected')


@pytest.mark.parametrize('path,target,protocol', [
    ('/api/live?test=1', 'ws://127.0.0.1:5173/api/live?test=1', 'test-protocol'),
    ('/_lan/vnc/profile_001?port=22', 'ws://127.0.0.1:6081/', 'binary'),
])
def test_session_cookie_authenticates_websocket_and_relays_text_and_binary(gateway, monkeypatch, path, target, protocol):
    client, calls = gateway
    client.get('/', auth=('manager', PASSWORD))

    class Remote:
        subprotocol = protocol

        def __init__(self):
            self.frames = asyncio.Queue()

        async def send(self, frame):
            await self.frames.put(frame)

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.frames.get()

    @asynccontextmanager
    async def connect(url, **kwargs):
        assert url == target
        assert kwargs['origin'] == 'http://127.0.0.1:5173'
        assert kwargs['proxy'] is None
        assert kwargs['subprotocols'] == [protocol]
        yield Remote()

    monkeypatch.setattr(lan_web, 'connect', connect)
    with client.websocket_connect(BASE.replace('http:', 'ws:') + path, headers={'Origin': BASE}, subprotocols=[protocol]) as socket:
        assert socket.accepted_subprotocol == protocol
        socket.send_text('hello')
        assert socket.receive_text() == 'hello'
        socket.send_bytes(b'video')
        assert socket.receive_bytes() == b'video'


@pytest.mark.parametrize('path,protocol', [('/_lan/vnc/profile_002', 'binary'), ('/_lan/vnc/profile_001', 'other')])
def test_vnc_rejects_unknown_account_and_wrong_protocol(gateway, path, protocol):
    client, calls = gateway
    client.get('/', auth=('manager', PASSWORD))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(BASE.replace('http:', 'ws:') + path, headers={'Origin': BASE}, subprotocols=[protocol]):
            pytest.fail('Invalid screen connection was accepted')


@pytest.mark.parametrize('status,container,runtime,expected', [
    ('running', {'ws_port': 6088}, {}, 'ws://127.0.0.1:6088/'),
    ('running', {'vnc_port': 5901}, {}, 'ws://127.0.0.1:6081/'),
    ('stopped', {'ws_port': 6081}, {}, None),
    ('paused', {'ws_port': 6081}, {}, None),
    ('running', {'ws_port': 6081}, {'kind': 'android'}, None),
    ('running', {'ws_port': 22}, {}, None),
    ('running', {'ws_port': True}, {}, None),
    ('running', {'ws_port': 70000}, {}, None),
    ('running', {}, {}, None),
])
def test_vnc_uses_only_running_pc_account_saved_port(status, container, runtime, expected):
    async def check():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[{
            'id': 'profile_001', 'status': status, 'container': container, 'runtime': runtime,
        }]))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await lan_web.vnc_target(client, 'profile_001', 'http://127.0.0.1:5173') == expected
            assert await lan_web.vnc_target(client, '../profile_001', 'http://127.0.0.1:5173') is None
    asyncio.run(check())
