"""Standalone warming shares preparer capacity but runs the warming task."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.services import automation_service as automation


@pytest.mark.parametrize("post_type,kind,preparation", [
    ("warming", "preparer", False),
    ("photo", "preparer", True),
    ("photo", "publisher", False),
    ("reel", "publisher", False),
])
def test_dispatch_uses_correct_runner(monkeypatch, post_type, kind, preparation):
    execution = {"execution_id": "exec-test", "profile_id": "profile-test"}
    queue = {"daily_batches": [{"posts": [{
        "type": post_type, "executions": [execution],
    }]}]}
    monkeypatch.setattr(automation, "load_posting_queue", lambda: queue)
    prepare = AsyncMock(return_value={"success": True})
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(automation, "execute_queue_preparation", prepare)
    monkeypatch.setattr(automation, "execute_queue_item", execute)

    result = asyncio.run(automation._run_dispatched_queue_task("exec-test", kind))

    assert result["success"] is True
    if preparation:
        prepare.assert_awaited_once_with("exec-test")
        execute.assert_not_awaited()
    else:
        execute.assert_awaited_once_with("exec-test", kind)
        prepare.assert_not_awaited()


def test_deleted_warming_execution_is_deferred(monkeypatch):
    monkeypatch.setattr(automation, "load_posting_queue", lambda: {"daily_batches": []})
    execute = AsyncMock()
    monkeypatch.setattr(automation, "execute_queue_item", execute)

    result = asyncio.run(automation._run_dispatched_queue_task("deleted", "preparer"))

    assert result["status"] == "deferred"
    execute.assert_not_awaited()
