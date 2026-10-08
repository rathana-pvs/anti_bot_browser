"""Batch workers use mode capacity and each account's iteration delay."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.services import automation_service as automation
from backend.services.queue_scheduler import claim_execution_lease
from backend.services.resource_service import RESOURCE_MODE_LIMITS


def setup_dispatch(monkeypatch, mode, container_status="running", admission=None):
    config = {**RESOURCE_MODE_LIMITS[mode], "lease_ttl_ms": 60000}
    executions = [
        {"execution_id": f"exec-{i}", "profile_id": f"profile-{i}",
         "status": "ready", "preparation_mode": "off"}
        for i in range(4)
    ]
    # Duplicate profile must never consume a second slot.
    duplicate = {**executions[0], "execution_id": "duplicate-profile"}
    later = {"execution_id": "next-post", "profile_id": "profile-0",
             "status": "ready", "preparation_mode": "off"}
    queue = {"daily_batches": [{
        "batch_id": "batch", "schedule_window": {"batch_iteration_delay_seconds": 0},
        "posts": [{"type": "reel", "executions": [executions[0], duplicate, *executions[1:]]},
                  {"type": "reel", "executions": [later]}],
    }]}
    monkeypatch.setattr(automation, "recover_stale_queue_executions", lambda *_: None)
    monkeypatch.setattr(automation, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(automation, "get_scheduler_config", lambda: config)
    monkeypatch.setattr(automation, "active_in_memory_scheduler_leases", lambda: [])
    monkeypatch.setattr(automation, "get_container_status", lambda _: container_status)
    monkeypatch.setattr(automation, "count_running_profile_containers", lambda: 0)
    monkeypatch.setattr(automation, "claim_container_start_admission",
                        admission or (lambda: {"allowed": True}))
    dispatched = []

    async def run(execution_id, kind):
        match = automation.find_queue_execution(queue, execution_id)
        execution = match["execution"]
        assert automation.batch_iteration_availability(
            match["batch"], target_execution=execution)["allowed"]
        claim = claim_execution_lease(queue, execution, execution_id, "test-owner", kind, config)
        assert claim["claimed"]
        execution["status"] = "running"
        dispatched.append(execution_id)

    monkeypatch.setattr(automation, "_run_dispatched_queue_task", run)

    async def dispatch():
        await automation.dispatch_pending_queue()
        await asyncio.sleep(0)  # Run the tasks reserved in this dispatch pass.

    return executions, dispatched, dispatch


@pytest.mark.parametrize("mode,expected", [("low", 1), ("medium", 2), ("high", 3)])
def test_same_batch_uses_mode_publisher_capacity(monkeypatch, mode, expected):
    _, dispatched, dispatch = setup_dispatch(monkeypatch, mode)
    asyncio.run(dispatch())
    assert dispatched == [f"exec-{i}" for i in range(expected)]


def test_existing_publisher_uses_one_medium_slot(monkeypatch):
    executions, dispatched, dispatch = setup_dispatch(monkeypatch, "medium")
    claim = claim_execution_lease(
        {"daily_batches": []}, executions[0], "active", "test-owner",
        config={**RESOURCE_MODE_LIMITS["medium"], "lease_ttl_ms": 60000})
    assert claim["claimed"]
    executions[0]["status"] = "running"
    asyncio.run(dispatch())
    assert dispatched == ["exec-1"]


@pytest.mark.parametrize("reason", ["memory_pressure", "cpu_pressure"])
def test_hardware_pressure_defers_new_containers(monkeypatch, reason):
    admission = Mock(return_value={"allowed": False, "reason": reason})
    _, dispatched, dispatch = setup_dispatch(monkeypatch, "medium", "stopped", admission)
    asyncio.run(dispatch())
    assert dispatched == []
    assert admission.called


@pytest.mark.parametrize("kind", ["preparer", "publisher"])
def test_iteration_delay_gates_dispatch_and_claim_for_each_profile(monkeypatch, kind):
    now = 1_800_000_000.0
    from datetime import datetime, timezone
    def iso(offset):
        return datetime.fromtimestamp(now + offset, timezone.utc).isoformat()

    def next_execution(profile):
        return {
            "execution_id": f"next-{profile}", "profile_id": profile,
            "status": "pending" if kind == "preparer" else "ready",
            "preparation_mode": "brief",
            "preparation_status": "pending" if kind == "preparer" else "ready",
        }
    waiting = next_execution("waiting")
    eligible = next_execution("eligible")
    previous = [
        {"execution_id": "previous-waiting", "profile_id": "waiting",
         "status": "published", "ended_at": iso(-30)},
        {"execution_id": "previous-eligible", "profile_id": "eligible",
         "status": "published", "ended_at": iso(-90)},
        {"execution_id": "previous-slow", "profile_id": "slow", "status": "running"},
    ]
    queue = {"daily_batches": [{
        "schedule_window": {"batch_iteration_delay_seconds": 60},
        "posts": [{"type": "reel", "executions": previous},
                  {"type": "reel", "executions": [waiting, eligible]}],
    }]}
    config = {**RESOURCE_MODE_LIMITS["medium"], "lease_ttl_ms": 60000}
    monkeypatch.setattr(automation.time, "time", lambda: now)
    monkeypatch.setattr(automation, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(automation, "recover_stale_queue_executions", lambda *_: None)
    monkeypatch.setattr(automation, "get_scheduler_config", lambda: config)
    monkeypatch.setattr(automation, "active_in_memory_scheduler_leases", lambda: [])
    monkeypatch.setattr(automation, "get_container_status", lambda _: "running")
    monkeypatch.setattr(automation, "with_queue_claim_lock", lambda fn: fn())
    monkeypatch.setattr(automation, "_delayed_dispatch", AsyncMock())
    dispatched = []
    async def run(execution_id, slot):
        dispatched.append((execution_id, slot))
    monkeypatch.setattr(automation, "_run_dispatched_queue_task", run)
    async def dispatch():
        await automation.dispatch_pending_queue()
        await asyncio.sleep(0)
    asyncio.run(dispatch())
    assert dispatched == [("next-eligible", kind)]
    automation._delayed_dispatch.assert_awaited_once_with(30_100)
    with pytest.raises(RuntimeError, match="batch_iteration_delay"):
        automation.claim_queue_execution("next-waiting", kind)
    assert waiting.get("scheduler_lease") is None
