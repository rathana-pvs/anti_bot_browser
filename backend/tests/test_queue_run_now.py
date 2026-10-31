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


@pytest.mark.parametrize("status", ["uncertain", "needs_review", "failed_after_publish"])
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



def test_run_now_cannot_prepare_next_iteration_during_profile_delay(monkeypatch):
    execution = {
        "execution_id": "next", "profile_id": "p1", "status": "pending",
        "preparation_mode": "brief", "preparation_status": "pending",
    }
    from datetime import datetime, timezone
    previous = {
        "execution_id": "previous", "profile_id": "p1", "status": "published",
        "ended_at": datetime.now(timezone.utc).isoformat(),
    }
    batch = {
        "schedule_window": {"batch_iteration_delay_seconds": 1600},
        "posts": [{"type": "reel", "executions": [previous]},
                  {"type": "reel", "executions": [execution]}],
    }
    queue = {"daily_batches": [batch]}
    monkeypatch.setattr(queue_router, "load_posting_queue", lambda: queue)
    with pytest.raises(HTTPException) as error:
        asyncio.run(queue_router.run_execution_now("next"))
    assert error.value.status_code == 409
    assert "this profile" in error.value.detail.lower()
    assert execution["status"] == "pending"


@pytest.fixture
def interrupted_retry(monkeypatch):
    from copy import deepcopy
    from backend.services import container_lifecycle, live_service

    execution = {
        "execution_id": "selected", "profile_id": "p1", "status": "cancelled",
        "stage": "cancelled", "cancel_requested_at": "old", "error": "cancelled",
        "scheduled_at": "2099-01-01T00:00:00+00:00",
        "preparation_mode": "brief", "preparation_status": "ready",
        "network_recovery": {"state": "waiting_network"},
    }
    other = {"execution_id": "other", "profile_id": "p2", "status": "cancelled"}
    queue = {"daily_batches": [{"posts": [{"type": "reel", "executions": [execution, other]}]}]}
    saved = Mock(return_value=True)
    dispatched = Mock()
    async def dispatch():
        dispatched()
    monkeypatch.setattr(queue_router, "load_posting_queue", lambda: deepcopy(queue))
    monkeypatch.setattr(queue_router, "with_queue_claim_lock", lambda callback: callback())
    monkeypatch.setattr(queue_router, "save_posting_queue", saved)
    monkeypatch.setattr(queue_router, "dispatch_pending_queue", dispatch)
    monkeypatch.setattr(container_lifecycle, "snapshot", lambda: {})
    monkeypatch.setattr(live_service, "get_status", lambda _: {})
    return execution, queue, saved, dispatched


@pytest.mark.parametrize("status", ["cancelled", "stopped"])
def test_interrupted_retry_queues_only_selected_item(interrupted_retry, status):
    execution, queue, saved, dispatched = interrupted_retry
    execution["status"] = status
    result = asyncio.run(queue_router.run_execution_now("selected"))
    assert result["queued"] is True
    posts = saved.call_args.args[0]["daily_batches"][0]["posts"]
    selected, other = posts[0]["executions"]
    assert selected["status"] == selected["stage"] == "pending"
    assert selected["scheduled_at"] != execution["scheduled_at"]
    assert selected["error"] is None
    assert "cancel_requested_at" not in selected
    assert selected["preparation_status"] == "pending"
    assert selected["network_recovery"]["state"] == "cancelled"
    assert other == queue["daily_batches"][0]["posts"][0]["executions"][1]
    dispatched.assert_called_once()


@pytest.mark.parametrize("patch", [
    {"stage_history": [{"stage": "publish_clicked"}]},
    {"cloud_attempt_id": "remote"},
    {"container_cleanup_in_progress": True},
    {"automation_container_owned": True},
])
def test_interrupted_retry_rejects_unsafe_items(interrupted_retry, patch):
    execution, _, saved, dispatched = interrupted_retry
    execution.update(patch)
    with pytest.raises(HTTPException) as error:
        asyncio.run(queue_router.run_execution_now("selected"))
    assert error.value.status_code == 409
    saved.assert_not_called()
    dispatched.assert_not_called()


def test_interrupted_retry_waits_for_profile_owner(interrupted_retry, monkeypatch):
    from backend.services import container_lifecycle
    _, _, saved, dispatched = interrupted_retry
    monkeypatch.setattr(container_lifecycle, "snapshot", lambda: {"p1": {"state": "stopping"}})
    with pytest.raises(HTTPException) as error:
        asyncio.run(queue_router.run_execution_now("selected"))
    assert error.value.status_code == 409
    saved.assert_not_called()
    dispatched.assert_not_called()


@pytest.mark.parametrize("status,confirmed", [("stopped", False), ("cancelled", True)])
def test_interrupted_retry_preserves_live_review(interrupted_retry, status, confirmed):
    execution, queue, saved, dispatched = interrupted_retry
    execution.update(status=status, broadcast_confirmed=confirmed)
    queue["daily_batches"][0]["posts"][0]["type"] = "live"
    with pytest.raises(HTTPException) as error:
        asyncio.run(queue_router.run_execution_now("selected"))
    assert error.value.status_code == 409
    saved.assert_not_called()
    dispatched.assert_not_called()


def test_interrupted_retry_does_not_dispatch_after_save_failure(interrupted_retry):
    _, _, saved, dispatched = interrupted_retry
    saved.return_value = False
    with pytest.raises(HTTPException) as error:
        asyncio.run(queue_router.run_execution_now("selected"))
    assert error.value.status_code == 500
    dispatched.assert_not_called()
