"""Batch retries never turn an ambiguous publication into another publish attempt."""
import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from backend.routers import queue as router
from backend.services import container_lifecycle


def configure(monkeypatch, executions, save=True):
    monkeypatch.setattr(container_lifecycle, "snapshot", lambda: {})
    queue = {"daily_batches": [{"batch_id": "batch", "posts": [
        {"type": "reel", "executions": executions},
        {"type": "live", "executions": [{"status": "failed_before_publish"}]},
    ]}]}
    monkeypatch.setattr(router, "load_posting_queue", lambda: deepcopy(queue))
    monkeypatch.setattr(router, "with_queue_claim_lock", lambda callback: callback())
    saved = Mock(return_value=save)
    monkeypatch.setattr(router, "save_posting_queue", saved)
    dispatched = Mock()

    async def dispatch():
        dispatched()

    monkeypatch.setattr(router, "dispatch_pending_queue", dispatch)
    return saved, dispatched


def test_rerun_queues_safe_posts_but_preserves_review_active_and_cloud(monkeypatch):
    executions = [
        {"status": "failed_before_publish", "preparation_mode": "brief", "preparation_status": "ready",
         "cancel_requested_at": "old", "network_recovery": {"state": "waiting_network"}},
        {"status": "skipped_unauthenticated"},
        {"status": "pending"},
        {"status": "cancelled"},
        {"status": "uncertain"},
        {"status": "needs_review"},
        {"status": "failed_after_publish"},
        {"status": "running"},
        {"status": "preparing"},
        {"status": "published"},
        {"status": "completed"},
        {"status": "failed_before_publish", "cloud_attempt_id": "cloud"},
        {"status": "failed_before_publish", "container_cleanup_in_progress": True},
        {"status": "failed_before_publish", "stage_history": [{"stage": "publish_clicked"}]},
    ]
    saved, dispatched = configure(monkeypatch, executions)
    result = asyncio.run(router.rerun_batch("batch"))
    assert result["queued"] == 4
    assert result["needs_review"] == 4
    dispatched.assert_called_once()
    items = saved.call_args.args[0]["daily_batches"][0]["posts"][0]["executions"]
    assert items[0]["network_recovery"]["state"] == "cancelled"
    assert items[0]["preparation_status"] == "pending"
    assert "cancel_requested_at" not in items[0]
    assert items[4:] == executions[4:]


def test_failed_save_does_not_dispatch(monkeypatch):
    _, dispatched = configure(monkeypatch, [{"status": "failed_before_publish"}], save=False)
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.rerun_batch("batch"))
    assert error.value.status_code == 500
    dispatched.assert_not_called()


def test_review_only_batch_is_unchanged(monkeypatch):
    saved, dispatched = configure(monkeypatch, [{"status": "uncertain"}])
    result = asyncio.run(router.rerun_batch("batch"))
    assert result["queued"] == 0
    assert result["needs_review"] == 1
    saved.assert_not_called()
    dispatched.assert_not_called()


def test_missing_batch_returns_404(monkeypatch):
    configure(monkeypatch, [])
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.rerun_batch("missing"))
    assert error.value.status_code == 404


def test_rerun_recovers_stale_migrated_container_flag(monkeypatch):
    saved, dispatched = configure(monkeypatch, [{
        "profile_id": "profile_014", "status": "skipped_session_unverified",
        "automation_container_owned": True, "container_owner_schema": 1,
    }])
    result = asyncio.run(router.rerun_batch("batch"))
    assert result["queued"] == 1
    item = saved.call_args.args[0]["daily_batches"][0]["posts"][0]["executions"][0]
    assert item["status"] == "pending"
    assert item["automation_container_owned"] is False
    dispatched.assert_called_once()


@pytest.mark.parametrize("state", ["active", "handoff", "stopping", "cleanup_failed"])
def test_rerun_preserves_registered_owner(monkeypatch, state):
    saved, dispatched = configure(monkeypatch, [{
        "profile_id": "profile_014", "status": "skipped_session_unverified",
        "automation_container_owned": True, "container_owner_schema": 1,
    }])
    monkeypatch.setattr(container_lifecycle, "snapshot", lambda: {"profile_014": {"state": state}})
    result = asyncio.run(router.rerun_batch("batch"))
    assert result["queued"] == 0
    assert result["blocked"] == 1
    saved.assert_not_called()
    dispatched.assert_not_called()


def test_rerun_does_not_clear_unmigrated_ownership(monkeypatch):
    saved, dispatched = configure(monkeypatch, [{
        "status": "skipped_session_unverified", "automation_container_owned": True,
    }])
    result = asyncio.run(router.rerun_batch("batch"))
    assert result["blocked"] == 1
    saved.assert_not_called()
    dispatched.assert_not_called()
