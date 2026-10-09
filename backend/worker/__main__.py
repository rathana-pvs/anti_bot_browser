"""Run the existing authenticated backend and cloud agent independently of the UI."""
import argparse
import os
import secrets
import socket
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int, default=0, help='Loopback port; default selects an available port')
    args = parser.parse_args()
    from .config import WorkerConfig
    WorkerConfig.load(args.config)  # Fail before launching the backend on invalid configuration.
    listener = socket.socket()
    listener.bind(('127.0.0.1', args.port))
    listener.listen(128)
    os.environ['WORKER_CONFIG_FILE'] = str(args.config.resolve())
    os.environ['WORKER_SERVICE_MODE'] = '1'
    os.environ['MANAGER_API_TOKEN'] = secrets.token_hex(32)
    os.environ['PORT'] = str(listener.getsockname()[1])
    import uvicorn
    # A prebound socket prevents the free-port discovery race.
    import asyncio
    try:
        asyncio.run(uvicorn.Server(uvicorn.Config('backend.main:app', host='127.0.0.1',
                                                 port=int(os.environ['PORT']), log_level='info')).serve(sockets=[listener]))
    finally:
        listener.close()


if __name__ == '__main__':
    main()
