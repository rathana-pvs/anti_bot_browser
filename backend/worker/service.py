"""Backend lifecycle integration; disabled unless a worker config is supplied."""
import asyncio
import os
from pathlib import Path

_agent = None
_task = None


def cloud_execution_allowed(execution):
    if not execution.get('cloud_attempt_id'):
        return True
    return bool(_agent and _agent.cloud_execution_allowed(execution))


def status():
    return _agent.status() if _agent else {'enabled': False, 'connected': False, 'reconciled': False}


async def start():
    global _agent, _task
    path = os.environ.get('WORKER_CONFIG_FILE')
    if not path:
        return
    from backend.config import DATA_DIR, SHARED_MEDIA_DIR
    from .adapter import QueueAdapter
    from .agent import WorkerAgent
    from .config import WorkerConfig
    from .journal import Journal
    from .transfers import Transfers
    config = WorkerConfig.load(Path(path))
    journal = Journal(DATA_DIR / 'worker' / 'journal.sqlite3')
    transfers = Transfers(config, SHARED_MEDIA_DIR / 'worker')
    try:
        _agent = WorkerAgent(config, journal, QueueAdapter(config), transfers)
    except Exception:
        journal.close()
        await transfers.close()
        raise
    _task = asyncio.create_task(_agent.run(), name='cloud-worker')


async def stop():
    global _agent, _task
    if _agent:
        await _agent.stop()
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        await _agent.transfers.close()
        _agent.journal.close()
    _agent = _task = None
