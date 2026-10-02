"""Regression tests for manual queue retries."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.routers import queue as queue_router


def test_failed_retry_queues_when_preparation_buffer_is_occupied(monkeypatch):
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

    async def dispatch_queue():
        return None

    monkeypatch.setattr(queue_router, "dispatch_pending_queue", dispatch_queue)

    result = asyncio.run(queue_router.run_execution_now("exec-1"))

    assert result["success"] is True
    assert result["queued"] is True
    assert execution["status"] == "pending"
    assert execution["stage"] == "pending"
    assert execution["preparation_status"] == "pending"
    assert execution["stage_history"][-1]["reason"] == "manual_retry"
    save_queue.assert_called_once_with(queue)


def test_failed_retry_reuses_ready_preparation(monkeypatch):
    execution = {
        "execution_id": "exec-ready",
        "profile_id": "profile-1",
        "status": "failed_before_publish",
        "stage": "failed_before_publish",
        "error": "file chooser failed",
        "preparation_mode": "brief",
        "preparation_status": "ready",
        "stage_history": [],
    }
    post = {"type": "photo", "executions": [execution]}
    batch = {"batch_id": "b-1", "posts": [post]}
    queue = {"daily_batches": [batch]}
    save_queue = Mock(return_value=True)

    monkeypatch.setattr(queue_router, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(
        queue_router,
        "find_queue_execution",
        lambda _queue, _execution_id: {"execution": execution, "post": post, "batch": batch},
    )
    monkeypatch.setattr(queue_router, "batch_iteration_availability", lambda *_args, **_kwargs: {"allowed": True})
    monkeypatch.setattr(queue_router, "get_scheduler_config", lambda: {"max_preparers": 1, "max_active_profile_containers": 2})
    monkeypatch.setattr(queue_router, "count_buffered_preparations", lambda *_args: 99)
    monkeypatch.setattr(queue_router, "get_container_status", lambda *_args: "running")
    monkeypatch.setattr(queue_router, "save_posting_queue", save_queue)

    started = []

    async def execute_now(execution_id, kind):
        started.append((execution_id, kind))

    monkeypatch.setattr(queue_router, "execute_queue_item", execute_now)

    result = asyncio.run(queue_router.run_execution_now("exec-ready"))

    assert result["success"] is True
    assert execution["preparation_status"] == "ready"
    assert started == [("exec-ready", "publisher")]
    save_queue.assert_called_once_with(queue)


@pytest.mark.parametrize("status", ["uncertain", "needs_review"])
def test_unverified_outcome_can_be_retried_directly(monkeypatch, status):
    execution = {
        "execution_id": f"exec-{status}",
        "profile_id": "profile-1",
        "status": status,
        "stage": status,
        "error": "publication outcome was not verified",
        "preparation_mode": "brief",
        "preparation_status": "ready",
        "stage_history": [],
    }
    post = {"type": "photo", "executions": [execution]}
    batch = {"batch_id": "b-1", "posts": [post]}
    queue = {"daily_batches": [batch]}
    save_queue = Mock(return_value=True)

    monkeypatch.setattr(queue_router, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(
        queue_router,
        "find_queue_execution",
        lambda _queue, _execution_id: {"execution": execution, "post": post, "batch": batch},
    )
    monkeypatch.setattr(queue_router, "batch_iteration_availability", lambda *_args, **_kwargs: {"allowed": True})
    monkeypatch.setattr(queue_router, "get_scheduler_config", lambda: {"max_preparers": 1, "max_active_profile_containers": 2})
    monkeypatch.setattr(queue_router, "count_buffered_preparations", lambda *_args: 0)
    monkeypatch.setattr(queue_router, "get_container_status", lambda *_args: "running")
    monkeypatch.setattr(queue_router, "save_posting_queue", save_queue)

    started = []

    async def execute_now(execution_id, kind):
        started.append((execution_id, kind))

    monkeypatch.setattr(queue_router, "execute_queue_item", execute_now)

    result = asyncio.run(queue_router.run_execution_now(execution["execution_id"]))

    assert result["success"] is True
    assert execution["status"] == "pending"
    assert execution["stage"] == "pending"
    assert execution["error"] is None
    assert execution["stage_history"][-1]["reason"] == "manual_retry"
    assert started == [(execution["execution_id"], "publisher")]
    save_queue.assert_called_once_with(queue)
