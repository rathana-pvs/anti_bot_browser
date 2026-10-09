"""One backend per data root; protected discovery for the independent worker service."""
import json
import os
import tempfile
from contextlib import contextmanager

from backend.config import DATA_DIR


@contextmanager
def exclusive_backend():
    path = DATA_DIR / 'backend-service.lock'
    with path.open('a+b') as stream:
        os.chmod(path, 0o600)
        if os.name == 'nt':
            import msvcrt
            stream.write(b'\0')
            stream.flush()
            stream.seek(0)
            lock, unlock = msvcrt.LK_NBLCK, msvcrt.LK_UNLCK
            try:
                msvcrt.locking(stream.fileno(), lock, 1)
            except OSError as error:
                raise RuntimeError('A backend already owns this data root') from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError('A backend already owns this data root; attach to the worker service') from error
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), unlock, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_session():
    if os.environ.get('WORKER_SERVICE_MODE') != '1':
        return None
    path = DATA_DIR / 'worker-service-session.json'
    token = os.environ.get('MANAGER_API_TOKEN')
    if not token:
        raise RuntimeError('Worker service requires local API authentication')
    fd, temporary = tempfile.mkstemp(dir=DATA_DIR, prefix='.worker-session-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'port': int(os.environ['PORT']), 'token': token, 'pid': os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
