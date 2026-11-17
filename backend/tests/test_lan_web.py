import argparse
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.services import web_gateway as lan_web

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
        assert kwargs['compression'] is None
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


TS_ORIGIN = 'https://manager.example.ts.net:8443'


@pytest.fixture
def desktop_gateway(monkeypatch, tmp_path):
    (tmp_path / 'index.html').write_text('<html>app</html>')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'main.js').write_text('console.log(1)')
    (tmp_path.parent / 'secret.txt').write_text('outside')
    calls = []
    real_client = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'api response'))

    monkeypatch.setattr(lan_web.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)))
    app = lan_web.create_app('127.0.0.1', 5176, PASSWORD, upstream_port=43941, public_origin=TS_ORIGIN,
                             static_dir=tmp_path, backend_token='desktop-token')
    with TestClient(app, base_url=TS_ORIGIN) as client:
        yield client, calls


def test_desktop_gateway_serves_built_app_after_login(desktop_gateway):
    client, calls = desktop_gateway
    assert client.get('/').status_code == 401
    assert client.get('/', auth=('manager', PASSWORD)).text == '<html>app</html>'
    assert client.get('/assets/main.js').text == 'console.log(1)'
    # Client-side routes and paths outside the build fall back to the app, never other files.
    assert client.get('/accounts/profile_001').text == '<html>app</html>'
    assert client.get('/..%2Fsecret.txt').text == '<html>app</html>'
    assert client.post('/', headers={'Origin': TS_ORIGIN}).status_code == 404
    assert not calls


def test_desktop_gateway_adds_backend_token_only_server_side(desktop_gateway):
    client, calls = desktop_gateway
    client.get('/', auth=('manager', PASSWORD))
    response = client.post('/api/example?x=1', content=b'body', headers={
        'Origin': TS_ORIGIN, 'X-Manager-Token': 'client-guess', 'X-Manager-Web-Gateway': '0'})
    assert response.status_code == 200
    request = calls[-1]
    assert str(request.url) == 'http://127.0.0.1:43941/api/example?x=1'
    assert request.headers['x-manager-token'] == 'desktop-token'
    assert request.headers['x-manager-web-gateway'] == '1'
    assert 'desktop-token' not in response.text and 'desktop-token' not in str(response.headers)
    client.get('/shared_media/clip.mp4')
    assert calls[-1].url.path == '/shared_media/clip.mp4'


def test_desktop_gateway_accepts_only_its_tailscale_port_origin(desktop_gateway):
    client, calls = desktop_gateway
    client.get('/', auth=('manager', PASSWORD))
    assert client.post('/api/x', headers={'Origin': 'https://manager.example.ts.net'}).status_code == 403
    assert client.get('/', headers={'Host': 'manager.example.ts.net:8443'}).status_code == 200


def test_desktop_gateway_websocket_carries_backend_token(desktop_gateway, monkeypatch):
    client, calls = desktop_gateway
    client.get('/', auth=('manager', PASSWORD))

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
        assert url == 'ws://127.0.0.1:43941/api/devices/phone/live'
        assert kwargs['additional_headers']['X-Manager-Token'] == 'desktop-token'
        yield Remote()

    monkeypatch.setattr(lan_web, 'connect', connect)
    with client.websocket_connect('wss://manager.example.ts.net:8443/api/devices/phone/live',
                                  headers={'Origin': TS_ORIGIN}) as socket:
        socket.send_text('ping')
        assert socket.receive_text() == 'ping'


def test_desktop_gateway_requires_built_app(tmp_path):
    with pytest.raises(ValueError, match='built web app'):
        lan_web.create_app('127.0.0.1', 5176, PASSWORD, public_origin=TS_ORIGIN, static_dir=tmp_path, backend_token='t')


def test_desktop_gateway_compresses_and_caches_built_files(desktop_gateway, tmp_path):
    client, calls = desktop_gateway
    client.get('/', auth=('manager', PASSWORD))
    big = 'console.log("speed");\n' * 500
    (tmp_path / 'assets' / 'app-abc123.js').write_text(big)
    response = client.get('/assets/app-abc123.js', headers={'Accept-Encoding': 'gzip'})
    assert response.headers['content-encoding'] == 'gzip'
    assert response.text == big
    assert response.headers['cache-control'] == 'private, max-age=31536000, immutable'
    assert 'x-gateway-cacheable' not in response.headers
    page = client.get('/')
    assert page.headers['cache-control'] == 'private, no-cache'
    plain = client.get('/assets/app-abc123.js', headers={'Accept-Encoding': 'identity'})
    assert 'content-encoding' not in plain.headers and plain.text == big


def test_gateway_compresses_api_json_but_not_media(monkeypatch, tmp_path):
    (tmp_path / 'index.html').write_text('<html>app</html>')
    payload = b'[' + b'{"id":"profile_001","status":"running"},' * 200 + b'{}]'
    real_client = httpx.AsyncClient

    def respond(request):
        if request.url.path == '/api/profiles':
            return httpx.Response(200, stream=httpx.ByteStream(payload), headers={'Content-Type': 'application/json'})
        return httpx.Response(200, stream=httpx.ByteStream(b'\x00' * 4096), headers={'Content-Type': 'video/mp4'})

    monkeypatch.setattr(lan_web.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)))
    app = lan_web.create_app('127.0.0.1', 5176, PASSWORD, upstream_port=43941, public_origin=TS_ORIGIN,
                             static_dir=tmp_path, backend_token='t')
    with TestClient(app, base_url=TS_ORIGIN) as client:
        client.get('/', auth=('manager', PASSWORD))
        api = client.get('/api/profiles', headers={'Accept-Encoding': 'gzip'})
        assert api.headers['content-encoding'] == 'gzip' and api.content == payload
        assert api.headers['cache-control'] == 'no-store'
        media = client.get('/shared_media/clip.mp4', headers={'Accept-Encoding': 'gzip'})
        assert 'content-encoding' not in media.headers and len(media.content) == 4096
