"""Password-protected gateway that puts the loopback web UI and API behind one login.

Two upstream shapes are supported:
- a development web server (Vite) that already proxies the API (``scripts/lan_web.py``);
- the desktop backend itself (``static_dir`` + ``backend_token``): the gateway serves the
  built web UI and adds the backend's session token server side, so it never reaches a browser.
"""
import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
from functools import lru_cache
import gzip
from http.cookies import SimpleCookie, CookieError
import ipaddress
import mimetypes
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
import zlib

import httpx
from fastapi import FastAPI, Request, WebSocket
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException


HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}
PRIVATE_NETWORKS = [ipaddress.ip_network(network) for network in
                    ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
COOKIE = 'manager_lan_session'
USERNAME = 'manager'
# Added by the gateway (never accepted from a client) so the backend can tell web requests apart.
GATEWAY_HEADER = 'x-manager-web-gateway'
BACKEND_PREFIXES = ('/api/', '/shared_media/')
STRIPPED_HEADERS = HOP_HEADERS | {'host', 'authorization', 'cookie', 'origin', 'x-manager-token',
                                  'forwarded', GATEWAY_HEADER}


async def vnc_target(client, profile_id, upstream):
    """Resolve only a running account's saved display port, never a client-supplied port."""
    if not re.fullmatch(r'profile_\d+', profile_id):
        return None
    try:
        response = await client.get(upstream + '/api/profiles')
        response.raise_for_status()
        profiles = response.json()
        if not isinstance(profiles, list):
            return None
        profile = next((item for item in profiles if isinstance(item, dict) and item.get('id') == profile_id), None)
        if not profile or profile.get('status') != 'running' or (profile.get('runtime') or {}).get('kind') == 'android':
            return None
        container = profile.get('container') or {}
        port = container.get('ws_port')
        if port is None and type(container.get('vnc_port')) is int:
            port = container['vnc_port'] + 180
        if type(port) is not int or not 1024 <= port <= 65535:
            return None
        return f'ws://127.0.0.1:{port}/'
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def private_address(value):
    try:
        address = ipaddress.ip_address(value)
        if any(address in network for network in PRIVATE_NETWORKS):
            return str(address)
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Choose this PC\'s private Wi-Fi/LAN IPv4 address, not 0.0.0.0 or a public IP.')


def static_file(root: Path, path: str):
    """A file inside the built web UI, or its index.html for client-side routes."""
    candidate = (root / path.lstrip('/')).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate
    return root / 'index.html'


# Text the browser downloads often; media and images are already compressed.
COMPRESSIBLE = ('text/', 'application/json', 'application/javascript', 'image/svg+xml')
MIN_COMPRESS_BYTES = 1024
# Internal marker between the static handler and the auth middleware; never sent to browsers.
CACHEABLE_MARK = 'x-gateway-cacheable'


def accepts_gzip(headers) -> bool:
    return 'gzip' in headers.get('accept-encoding', '').lower()


def compressible(content_type: str) -> bool:
    content_type = content_type.lower()
    return content_type.startswith(COMPRESSIBLE) and not content_type.startswith('text/event-stream')


@lru_cache(maxsize=64)
def _gzipped(path: str, mtime_ns: int) -> bytes:
    return gzip.compress(Path(path).read_bytes(), compresslevel=6, mtime=0)


def static_response(file: Path, request_headers):
    """Built files: hashed assets are cached for good, the page itself is re-checked every load."""
    content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
    cache = ('private, max-age=31536000, immutable' if file.parent.name == 'assets'
             else 'private, no-cache')
    stat = file.stat()
    if compressible(content_type) and stat.st_size >= MIN_COMPRESS_BYTES and accepts_gzip(request_headers):
        return Response(_gzipped(str(file), stat.st_mtime_ns), media_type=content_type, headers={
            'Content-Encoding': 'gzip', 'Vary': 'Accept-Encoding', 'Cache-Control': cache})
    return FileResponse(file, media_type=content_type, headers={'Cache-Control': cache})


async def gzip_stream(chunks):
    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
    async for chunk in chunks:
        if data := compressor.compress(chunk):
            yield data
    yield compressor.flush()


def create_app(host, port, password, upstream_port=5173, tls=False, public_origin=None,
               static_dir=None, backend_token=None):
    if public_origin:
        parsed = urlsplit(public_origin)
        try:
            public_port = parsed.port
        except ValueError:
            public_port = -1
        if (host != '127.0.0.1' or parsed.scheme != 'https' or not parsed.hostname
                or not re.fullmatch(r'[a-z0-9.-]+\.ts\.net', parsed.hostname)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or public_port == -1):
            raise ValueError('Tailscale HTTPS requires a loopback listener and the exact https://device.tailnet.ts.net origin')
        named = parsed.hostname if public_port in (None, 443) else f'{parsed.hostname}:{public_port}'
        origin = f'https://{named}'
        authorities = {parsed.hostname, named, f'{host}:{port}'}
    else:
        private_address(host)
        origin = f'{"https" if tls else "http"}://{host}:{port}'
        authorities = {f'{host}:{port}'}
    if len(password) < 16:
        raise ValueError('LAN password must have at least 16 characters')
    static_root = Path(static_dir).resolve() if static_dir else None
    if static_root and not (static_root / 'index.html').is_file():
        raise ValueError(f'The built web app was not found in {static_root}')
    secure_session = tls or bool(public_origin)
    upstream = f'http://127.0.0.1:{upstream_port}'
    # Backend mode: the desktop session token is added here and stays on this PC.
    upstream_headers = {'X-Manager-Token': backend_token, GATEWAY_HEADER: '1'} if backend_token is not None else {}
    if backend_token == '':
        upstream_headers.pop('X-Manager-Token')
    session = secrets.token_urlsafe(32)
    credentials = f'{USERNAME}:{password}'.encode()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5), trust_env=False,
                                     headers=upstream_headers) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(headers):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('cookie', ''))
            saved = cookie.get(COOKIE)
            if saved and secrets.compare_digest(saved.value, session):
                return True
        except (CookieError, TypeError):
            pass
        try:
            scheme, encoded = headers.get('authorization', '').split(' ', 1)
            return scheme.lower() == 'basic' and secrets.compare_digest(
                base64.b64decode(encoded, validate=True), credentials)
        except (ValueError, TypeError):
            return False

    @app.middleware('http')
    async def authenticate(request, call_next):
        if request.headers.get('host') not in authorities or request.headers.get('origin', origin) != origin:
            return PlainTextResponse('This gateway accepts only its own address and same-origin requests.', status_code=403)
        if not authorized(request.headers):
            return PlainTextResponse('Login required', status_code=401, headers={
                'WWW-Authenticate': 'Basic realm="Manager web access", charset="UTF-8"', 'Cache-Control': 'no-store'})
        response = await call_next(request)
        # Browser WebSocket APIs cannot set Authorization; use a per-process session cookie.
        response.set_cookie(COOKIE, session, httponly=True, samesite='strict', secure=secure_session)
        # Built app files set their own caching; everything else is never stored.
        if response.headers.get(CACHEABLE_MARK) == '1':
            del response.headers[CACHEABLE_MARK]
        else:
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def proxy(request: Request, path: str):
        raw_path = request.scope.get('raw_path', b'/').decode('ascii')
        if static_root and not raw_path.startswith(BACKEND_PREFIXES):
            if request.method not in ('GET', 'HEAD'):
                return PlainTextResponse('Not found', status_code=404)
            response = static_response(static_file(static_root, path), request.headers)
            response.headers[CACHEABLE_MARK] = '1'
            return response
        # The fixed upstream is loopback; paths can never select another server.
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() not in STRIPPED_HEADERS and not key.lower().startswith('x-forwarded-')}
        headers['origin'] = upstream
        query = request.scope.get('query_string', b'').decode('ascii')
        url = upstream + raw_path + (f'?{query}' if query else '')
        outgoing = app.state.client.build_request(request.method, url, headers=headers, content=request.stream())
        try:
            result = await app.state.client.send(outgoing, stream=True)
        except httpx.RequestError:
            return PlainTextResponse('The app on this PC is not reachable right now.', status_code=502)
        compress = (accepts_gzip(request.headers) and request.method != 'HEAD'
                    and 'content-encoding' not in result.headers and 'content-range' not in result.headers
                    and compressible(result.headers.get('content-type', ''))
                    and int(result.headers.get('content-length') or MIN_COMPRESS_BYTES) >= MIN_COMPRESS_BYTES)
        body = gzip_stream(result.aiter_raw()) if compress else result.aiter_raw()
        response = StreamingResponse(body, status_code=result.status_code,
                                     background=BackgroundTask(result.aclose))
        dropped = HOP_HEADERS | {'set-cookie'} | ({'content-length'} if compress else set())
        response.raw_headers = [(key, value) for key, value in result.headers.raw
                                if key.decode().lower() not in dropped]
        if compress:
            response.raw_headers += [(b'content-encoding', b'gzip'), (b'vary', b'Accept-Encoding')]
        return response

    @app.websocket('/{path:path}')
    async def websocket_proxy(websocket: WebSocket, path: str):
        if (websocket.headers.get('host') not in authorities or websocket.headers.get('origin') != origin
                or not authorized(websocket.headers)):
            await websocket.close(code=1008)
            return
        query = websocket.scope.get('query_string', b'').decode('ascii')
        raw_path = websocket.scope.get('raw_path', b'/').decode('ascii')
        url = f'ws://127.0.0.1:{upstream_port}' + raw_path + (f'?{query}' if query else '')
        protocols = websocket.scope.get('subprotocols') or None
        extra = {}
        if path.startswith('_lan/vnc/'):
            if not protocols or 'binary' not in protocols:
                await websocket.close(code=1008)
                return
            url = await vnc_target(app.state.client, path.removeprefix('_lan/vnc/'), upstream)
            if url is None:
                await websocket.close(code=1013, reason='Start the selected PC account before opening its screen.')
                return
            protocols = ['binary']
        elif upstream_headers:
            extra['additional_headers'] = upstream_headers
        try:
            # Loopback hop: compressing here only costs CPU and adds delay to every screen frame.
            async with connect(url, origin=upstream, subprotocols=protocols, max_size=None, proxy=None,
                               compression=None, **extra) as remote:
                await websocket.accept(subprotocol=remote.subprotocol)

                async def to_remote():
                    while True:
                        message = await websocket.receive()
                        if message['type'] == 'websocket.disconnect':
                            return
                        await remote.send(message.get('text') if message.get('text') is not None else message['bytes'])

                async def to_browser():
                    async for message in remote:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)

                tasks = [asyncio.create_task(to_remote()), asyncio.create_task(to_browser())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, WebSocketException, TimeoutError):
            pass
        finally:
            try:
                await websocket.close()
            except RuntimeError:
                pass

    return app
