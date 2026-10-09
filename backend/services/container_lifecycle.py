"""Persisted, exclusive ownership of automation profile containers."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import tempfile
import threading
import time
import uuid
import psutil

from backend.config import DATA_DIR, MANAGER_INSTANCE_ID

OWNERS_FILE = DATA_DIR / 'container_owners.json'
LOCK_FILE = DATA_DIR / 'container_owners.lock'
OWNER_TOKEN = ContextVar('automation_container_owner', default=None)
_LOCAL_LOCK = threading.RLock()
HEARTBEAT_SECONDS = 90
HARD_DEADLINE_SECONDS = 30 * 60
HANDOFF_SECONDS = 120
SHUTTING_DOWN = False

@contextmanager
def _locked():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOCK, LOCK_FILE.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

def _read():
    if not OWNERS_FILE.exists():
        return {}
    with OWNERS_FILE.open() as stream:
        return json.load(stream)  # Fail closed on corrupt ownership data.

def _write(owners):
    OWNERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=OWNERS_FILE.parent, prefix='.container-owners-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(owners, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, OWNERS_FILE)
    finally:
        if os.path.exists(path):
            os.unlink(path)

def acquire(profile_id, execution_id, token=None, deadline_seconds=None):
    if SHUTTING_DOWN:
        raise RuntimeError("Manager is shutting down")
    token = token or uuid.uuid4().hex
    now = time.time()
    with _locked():
        owners = _read()
        old = owners.get(profile_id)
        if old and not (old['state'] == 'handoff' and old['execution_id'] == execution_id):
            raise RuntimeError('Scheduler could not claim execution: container_owned')
        owners[profile_id] = dict(token=token, execution_id=execution_id,
            manager_id=MANAGER_INSTANCE_ID, backend_pid=os.getpid(),
            backend_start_time=psutil.Process().create_time(),
            state='active', used=bool(old and old.get('used')),
            heartbeat_expires_at=now + HEARTBEAT_SECONDS,
            hard_deadline_at=now + (deadline_seconds or HARD_DEADLINE_SECONDS))
        _write(owners)
    return token

def mark_used(profile_id, token):
    with _locked():
        owners = _read()
        owner = owners.get(profile_id)
        if not owner or owner['token'] != token or owner['state'] != 'active':
            raise RuntimeError('Container ownership lost before use')
        owner['used'] = True
        _write(owners)

def heartbeat(profile_id, token):
    with _locked():
        owners = _read()
        owner = owners.get(profile_id)
        if not owner or owner['token'] != token or owner['state'] != 'active':
            return False
        now = time.time()
        if now >= owner['hard_deadline_at']:
            return False
        owner['heartbeat_expires_at'] = now + HEARTBEAT_SECONDS
        _write(owners)
        return True

def handoff(profile_id, token):
    with _locked():
        owners = _read()
        owner = owners.get(profile_id)
        if not owner or owner['token'] != token or owner['state'] != 'active':
            return False
        owner['state'] = 'handoff'
        owner['heartbeat_expires_at'] = time.time() + HANDOFF_SECONDS
        _write(owners)
        return True

def snapshot():
    with _locked():
        return _read()

async def release(profile_id, token, stop):
    if not token:
        return {'stopped': False, 'reason': 'no_ownership'}
    with _locked():
        owners = _read()
        owner = owners.get(profile_id)
        if not owner or owner['token'] != token:
            return {'stopped': False, 'reason': 'ownership_changed'}
        if owner['state'] == 'stopping':
            return {'stopped': False, 'reason': 'cleanup_in_progress'}
        if not owner.get('used'):
            del owners[profile_id]
            _write(owners)
            return {'stopped': False, 'reason': 'container_not_used'}
        owner['state'] = 'stopping'
        owner['cleanup_started_at'] = time.time()
        _write(owners)
    try:
        result = await stop(profile_id)
    except BaseException as error:
        with _locked():
            owners = _read()
            owner = owners.get(profile_id)
            if owner and owner['token'] == token:
                owner['state'] = 'cleanup_failed'
                owner['cleanup_error'] = str(error)
                _write(owners)
        raise
    with _locked():
        owners = _read()
        if owners.get(profile_id, {}).get('token') == token:
            del owners[profile_id]
            _write(owners)
    return result

async def recover(stop, terminate, force=False):
    now = time.time()
    for profile_id, owner in snapshot().items():
        orphan = owner['manager_id'] != MANAGER_INSTANCE_ID
        if orphan and owner.get('backend_pid') and not force:
            try:
                process = psutil.Process(owner['backend_pid'])
                if process.is_running() and process.create_time() == owner.get('backend_start_time'):
                    continue  # Another live manager owns this profile.
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                continue  # Ownership cannot safely be disproved.
        expired = now >= min(owner['heartbeat_expires_at'], owner['hard_deadline_at'])
        if not (force or orphan or expired or owner['state'] == 'cleanup_failed'):
            continue
        # A prior backend may have died during Stop; resume its persisted cleanup.
        if owner['state'] == 'stopping':
            if not orphan and not force and now - owner.get('cleanup_started_at', now) < 300:
                continue
            with _locked():
                owners = _read()
                current = owners.get(profile_id)
                if current and current['token'] == owner['token']:
                    current['state'] = 'cleanup_failed'
                    _write(owners)
        try:
            await terminate(profile_id, owner['token'])
        except Exception:
            pass  # Container stop still proceeds when worker termination fails.
        try:
            await release(profile_id, owner['token'], stop)
        except Exception:
            pass  # Persisted failure is retried on the next watchdog sweep.


def import_legacy_owners(queue):
    """Migrate queue-owned containers created before the central controller."""
    with _locked():
        owners = _read()
        for batch in queue.get('daily_batches', []):
            for post in batch.get('posts', []):
                for execution in post.get('executions', []):
                    profile_id = execution.get('profile_id')
                    if not profile_id or profile_id in owners or execution.get('container_owner_schema') == 1 or not execution.get('automation_container_owned'):
                        continue
                    execution['container_owner_schema'] = 1
                    owners[profile_id] = dict(token=uuid.uuid4().hex,
                        execution_id=execution.get('execution_id'), manager_id='legacy',
                        state='cleanup_failed', used=True, heartbeat_expires_at=0,
                        hard_deadline_at=0)
        _write(owners)
