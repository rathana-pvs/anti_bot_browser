"""Password-protected LAN gateway to the existing loopback web development UI."""
import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie, CookieError
import ipaddress
import os
from pathlib import Path
import re
import secrets
import ssl
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import StreamingResponse
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException


HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}
PRIVATE_NETWORKS = [ipaddress.ip_network(network) for network in
                    ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
COOKIE = 'manager_lan_session'


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


def create_app(host, port, password, upstream_port=5173, tls=False, public_origin=None):
    if public_origin:
        parsed = urlsplit(public_origin)
        if (host != '127.0.0.1' or parsed.scheme != 'https' or not parsed.hostname
                or not re.fullmatch(r'[a-z0-9.-]+\.ts\.net', parsed.hostname)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/') or parsed.port not in (None, 443)):
            raise ValueError('Tailscale HTTPS requires a loopback listener and the exact https://device.tailnet.ts.net origin')
        origin = f'https://{parsed.hostname}'
        authorities = {parsed.hostname, f'{host}:{port}'}
    else:
        private_address(host)
        origin = f'{"https" if tls else "http"}://{host}:{port}'
        authorities = {f'{host}:{port}'}
    if len(password) < 16:
        raise ValueError('LAN password must have at least 16 characters')
    secure_session = tls or bool(public_origin)
    upstream = f'http://127.0.0.1:{upstream_port}'
    session = secrets.token_urlsafe(32)
    credentials = f'manager:{password}'.encode()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5), trust_env=False) as client:
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
        from starlette.responses import PlainTextResponse
        if request.headers.get('host') not in authorities or request.headers.get('origin', origin) != origin:
            return PlainTextResponse('This gateway accepts only its LAN address and same-origin requests.', status_code=403)
        if not authorized(request.headers):
            return PlainTextResponse('LAN login required', status_code=401, headers={
                'WWW-Authenticate': 'Basic realm="Manager LAN", charset="UTF-8"', 'Cache-Control': 'no-store'})
        response = await call_next(request)
        # Browser WebSocket APIs cannot set Authorization; use a per-process session cookie.
        response.set_cookie(COOKIE, session, httponly=True, samesite='strict', secure=secure_session)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def proxy(request: Request, path: str):
        # The fixed upstream is loopback; paths can never select another server.
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() not in HOP_HEADERS | {'host', 'authorization', 'cookie', 'origin', 'x-manager-token', 'forwarded'}
                   and not key.lower().startswith('x-forwarded-')}
        headers['origin'] = upstream
        query = request.scope.get('query_string', b'').decode('ascii')
        raw_path = request.scope.get('raw_path', b'/').decode('ascii')
        url = upstream + raw_path + (f'?{query}' if query else '')
        outgoing = app.state.client.build_request(request.method, url, headers=headers, content=request.stream())
        try:
            result = await app.state.client.send(outgoing, stream=True)
        except httpx.RequestError:
            from starlette.responses import PlainTextResponse
            return PlainTextResponse('Local web app unavailable. Start npm run dev:web on this PC.', status_code=502)
        from starlette.background import BackgroundTask
        response = StreamingResponse(result.aiter_raw(), status_code=result.status_code,
                                     background=BackgroundTask(result.aclose))
        response.raw_headers = [(key, value) for key, value in result.headers.raw
                                if key.decode().lower() not in HOP_HEADERS | {'set-cookie'}]
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
        if path.startswith('_lan/vnc/'):
            if not protocols or 'binary' not in protocols:
                await websocket.close(code=1008)
                return
            url = await vnc_target(app.state.client, path.removeprefix('_lan/vnc/'), upstream)
            if url is None:
                await websocket.close(code=1013, reason='Start the selected PC account before opening its screen.')
                return
            protocols = ['binary']
        try:
            async with connect(url, origin=upstream, subprotocols=protocols, max_size=None, proxy=None) as remote:
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=5173)
    parser.add_argument('--upstream-port', type=int, default=5173)
    parser.add_argument('--tls-cert', type=Path)
    parser.add_argument('--tls-key', type=Path)
    parser.add_argument('--https-origin', '--public-origin', dest='public_origin',
                        help='Exact Tailscale HTTPS origin; requires --host 127.0.0.1')
    args = parser.parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error('--tls-cert and --tls-key must be supplied together')
    tls = args.tls_cert is not None
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
    password = os.environ.get('MANAGER_LAN_PASSWORD') or secrets.token_urlsafe(18)
    app = create_app(args.host, args.port, password, args.upstream_port, tls=tls, public_origin=args.public_origin)
    secure_url = args.public_origin or f'{"https" if tls else "http"}://{args.host}:{args.port}/'
    print(f'Web app: {secure_url}\nUsername: manager\nPassword: {password}\n'
          + ('Trust the local CA on your client computer before opening HTTPS.\n' if tls else
             'HTTPS is supplied by Tailscale. Serve/Funnel determines who can reach it.\n' if args.public_origin else
             'Trusted Wi-Fi only: HTTP is not encrypted.\n')
          + 'Stop this command to close gateway access.\n', flush=True)
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, access_log=False,
                ssl_certfile=str(args.tls_cert) if tls else None,
                ssl_keyfile=str(args.tls_key) if tls else None)


if __name__ == '__main__':
    main()
