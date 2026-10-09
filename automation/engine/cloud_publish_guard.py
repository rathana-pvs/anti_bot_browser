"""Durable cloud publish intent before any irreversible browser action."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .runtime_paths import runtime_root


def record_cloud_publish_intent():
    execution_id = os.environ.get('CLOUD_EXECUTION_ID')
    if not execution_id:
        return
    if not re.fullmatch(r'cloud_[a-f0-9]{64}', execution_id):
        raise ValueError('Invalid cloud execution identity')
    directory = runtime_root() / 'data' / 'worker' / 'publish-intents'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / (execution_id + '.json')
    # Never overwrite an earlier intent; one local execution may publish only once.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'execution_id': execution_id, 'stage': 'publish_clicked',
                   'timestamp': datetime.now(timezone.utc).isoformat()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
