"""Regression tests for manual queue retries."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.routers import queue as queue_router


def test_rejected_failed_retry_is_not_persisted_as_pending(monkeypatch):
    execution = {
        "execution_id": "exec-1",
        "profile_id": "profile-1",
        "status": "failed_before_publish",
        "stage": "failed_before_publish",
        "error": "previous failure",
        "preparation_mode": "brief",
        "preparation_status": "failed",
        "stage_history": [],
    }
    post = {"type": "post", "executions": [execution]}
    queue = {"daily_batches": [{"posts": [post]}]}
    save_queue = Mock()

    monkeypatch.setattr(queue_router, "load_posting_queue", lambda: queue)
    batch = {"batch_id": "b-1", "posts": [post]}
    monkeypatch.setattr(
        queue_router,
        "find_queue_execution",
        lambda _queue, _execution_id: {"execution": execution, "post": post, "batch": batch},
    )
    monkeypatch.setattr(queue_router, "get_scheduler_config", lambda: {"max_preparers": 1, "max_active_profile_containers": 2})
    monkeypatch.setattr(queue_router, "count_buffered_preparations", lambda *_args: 1)
    monkeypatch.setattr(queue_router, "save_posting_queue", save_queue)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(queue_router.run_execution_now("exec-1"))

    assert exc_info.value.status_code == 409
    assert execution["status"] == "failed_before_publish"
    assert execution["stage"] == "failed_before_publish"
    assert execution["preparation_status"] == "failed"
    assert execution["stage_history"] == []
    save_queue.assert_not_called()
