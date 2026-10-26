"""Bulk cancellation must stop execution without losing publication uncertainty."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.services import automation_service as svc


def setup_queue(monkeypatch, posts):
    queue = {"daily_batches": [{"posts": posts}]}
    monkeypatch.setattr(svc, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(svc, "save_posting_queue", Mock(return_value=True))
    monkeypatch.setattr(svc, "with_queue_claim_lock", lambda callback: callback())
    monkeypatch.setattr(svc, "_queue_execution_tasks", {})
    monkeypatch.setattr(svc, "active_automation_tasks", {})
    monkeypatch.setattr(svc.lifecycle, "release", AsyncMock(return_value={"stopped": True}))
    monkeypatch.setattr(svc, "_terminate_owned_worker", AsyncMock())
    return queue


def test_cancels_every_batch_pending_and_ready_but_preserves_other_work(monkeypatch):
    pending = {"execution_id": "pending", "status": "pending"}
    ready = {"execution_id": "ready", "status": "ready", "profile_id": "p", "container_owner_token": "owner"}
    published = {"execution_id": "published", "status": "published"}
    uncertain = {"execution_id": "uncertain", "status": "uncertain"}
    cloud = {"execution_id": "cloud", "status": "pending", "cloud_attempt_id": "attempt"}
    live = {"execution_id": "live", "status": "running"}
    queue = setup_queue(monkeypatch, [{"type": "photo", "executions": [pending, published, uncertain, cloud]}, {"type": "live", "executions": [live]}])
    queue["daily_batches"].append({"posts": [{"type": "reel", "executions": [ready]}]})
    result = asyncio.run(svc.cancel_all_local_posts())
    assert result["cancelled"] == 2
    assert pending["status"] == ready["status"] == "cancelled"
    assert published["status"] == "published"
    assert uncertain["status"] == "uncertain"
    assert cloud["status"] == "pending"
    assert live["status"] == "running"
    svc.lifecycle.release.assert_awaited_once()
    assert asyncio.run(svc.cancel_all_local_posts())["cancelled"] == 0


@pytest.mark.parametrize("stage,expected", [("preparing", "cancelled"), ("composing", "cancelled"), ("publish_clicked", "uncertain"), ("verifying", "uncertain"), ("unknown", "uncertain")])
@pytest.mark.parametrize("batch_id", [None, "selected-batch"])
def test_stops_owned_task_and_preserves_uncertainty(monkeypatch, stage, expected, batch_id):
    execution = {"execution_id": "e", "profile_id": "p", "status": "pending", "stage": stage}
    queue = setup_queue(monkeypatch, [{"type": "photo", "executions": [execution]}])
    queue["daily_batches"][0]["batch_id"] = "selected-batch"

    async def run():
        started = asyncio.Event()

        async def running(execution_id):
            execution.update(status="running", container_owner_token=svc.lifecycle.OWNER_TOKEN.get(), scheduler_lease={"lease_id": "lease"})
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(svc._run_owned_queue("e", running))
        await started.wait()
        result = await svc.cancel_all_local_posts(batch_id=batch_id)
        assert result["cancelled"] == 1
        assert task.cancelled()
        assert not svc._queue_execution_tasks

    asyncio.run(run())
    assert execution["status"] == expected
    assert execution["scheduler_lease"] is None
    assert execution["stage_history"][-1]["reason"] == "user_cancel_all"
    svc._terminate_owned_worker.assert_awaited_once()
    svc.lifecycle.release.assert_awaited_once()


def test_persistence_failure_does_not_signal_running_task(monkeypatch):
    pending = {"execution_id": "pending", "status": "pending"}
    setup_queue(monkeypatch, [{"type": "photo", "executions": [pending]}])
    svc.save_posting_queue.return_value = False
    with pytest.raises(OSError, match="persist"):
        asyncio.run(svc.cancel_all_local_posts())
    svc._terminate_owned_worker.assert_not_awaited()


def test_cancelled_execution_cannot_be_claimed_by_already_dispatched_task(monkeypatch):
    execution = {"execution_id": "e", "profile_id": "p", "status": "cancelled"}
    setup_queue(monkeypatch, [{"type": "photo", "executions": [execution]}])
    with pytest.raises(RuntimeError, match="cancelled"):
        svc.claim_queue_execution("e")


def test_batch_cancellation_leaves_other_batches_untouched(monkeypatch):
    selected = {"execution_id": "selected", "status": "pending"}
    other = {"execution_id": "other", "status": "running"}
    queue = setup_queue(monkeypatch, [{"type": "photo", "executions": [selected]}])
    queue["daily_batches"][0]["batch_id"] = "selected-batch"
    queue["daily_batches"].append({"batch_id": "other-batch", "posts": [{"type": "photo", "executions": [other]}]})
    result = asyncio.run(svc.cancel_all_local_posts(batch_id="selected-batch"))
    assert result["cancelled"] == 1
    assert selected["status"] == "cancelled"
    assert other == {"execution_id": "other", "status": "running"}


def test_missing_batch_does_not_cancel_anything(monkeypatch):
    execution = {"execution_id": "e", "status": "pending"}
    setup_queue(monkeypatch, [{"type": "photo", "executions": [execution]}])
    with pytest.raises(KeyError, match="Batch not found"):
        asyncio.run(svc.cancel_all_local_posts(batch_id="missing"))
    assert execution["status"] == "pending"
    svc.save_posting_queue.assert_not_called()
