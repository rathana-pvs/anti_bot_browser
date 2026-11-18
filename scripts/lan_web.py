"""Password-protected LAN gateway to the existing loopback web development UI."""
import argparse
import os
from pathlib import Path
import secrets
import ssl
import sys

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.web_gateway import create_app, private_address  # noqa: E402,F401


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
                ws_per_message_deflate=False,
                ssl_certfile=str(args.tls_cert) if tls else None,
                ssl_keyfile=str(args.tls_key) if tls else None)


if __name__ == '__main__':
    main()
