"""Durable local preparation results, fenced by the queue's scheduler lease."""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def result_path(profiles_root, profile_id, lease_id):
    digest = hashlib.sha256(lease_id.encode()).hexdigest()
    return Path(profiles_root) / profile_id / 'preparation_results' / f'{digest}.json'


def write_result(profiles_root, context, result):
    path = result_path(profiles_root, context['profile_id'], context['lease_id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.preparation-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'context': context, 'result': result}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_result(profiles_root, context):
    try:
        payload = json.loads(result_path(profiles_root, context['profile_id'], context['lease_id']).read_text())
        if isinstance(payload, dict) and payload.get('context') == context and isinstance(payload.get('result'), dict):
            return payload['result']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None
