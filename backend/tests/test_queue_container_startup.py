"""Run queue workers through startup so ownership and readiness cannot conflict."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.services import automation_service as svc
from backend.services import queue_service


@pytest.mark.parametrize("worker,post_type,kind", [
    ("preparation", "photo", "preparer"),
    ("item", "warming", "preparer"),
    ("item", "photo", "publisher"),
])
@pytest.mark.parametrize("startup_fails", [False, True])
def test_queue_worker_reaches_container_startup(monkeypatch, worker, post_type, kind, startup_fails):
    lease = {"lease_id": "lease-test", "kind": kind}
    execution = {
        "execution_id": "exec-test", "profile_id": "profile-test",
        "status": "running", "scheduler_lease": lease,
        "preparation_mode": "brief",
    }
    post = {"type": post_type, "executions": [execution], "scrolls": 4}
    queue = {"daily_batches": [{"posts": [post]}]}
    claim = Mock(return_value={
        "targetExec": execution, "targetPost": post,
        "leaseId": "lease-test", "lease": lease,
    })
    monkeypatch.setattr(svc, "claim_queue_execution", claim)
    monkeypatch.setattr(svc, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(svc, "save_posting_queue", Mock(return_value=True))
    monkeypatch.setattr(queue_service, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(queue_service, "save_posting_queue", Mock(return_value=True))
    monkeypatch.setattr(svc, "active_automation_tasks", {})
    monkeypatch.setattr(svc, "resolve_brain_pin", lambda *_: None)
    monkeypatch.setattr(svc, "automation_worker_env", lambda: {})
    monkeypatch.setattr(svc, "_delayed_dispatch", AsyncMock())
    monkeypatch.setattr(svc, "stop_profile_container_after_report", AsyncMock(return_value={"stopped": True}))
    ready = AsyncMock(
        side_effect=RuntimeError("Docker startup failed") if startup_fails else None,
        return_value={"started": True, "status": "running"},
    )
    monkeypatch.setattr(svc, "ensure_profile_container_ready", ready)
    status = "published" if post_type == "photo" and worker == "item" else "completed"
    proc = SimpleNamespace(
        stdout=SimpleNamespace(read=AsyncMock(side_effect=[
            ('{"status": "' + status + '", "success": true}\n').encode(), b"",
        ])),
        stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        wait=AsyncMock(return_value=0),
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(svc.asyncio, "create_subprocess_exec", spawn)

    async def run():
        token = svc.lifecycle.OWNER_TOKEN.set("owner-test")
        try:
            if worker == "preparation":
                return await svc._execute_queue_preparation_inner("exec-test")
            return await svc._execute_queue_item_inner("exec-test", kind)
        finally:
            svc.lifecycle.OWNER_TOKEN.reset(token)

    result = asyncio.run(run())

    ready.assert_awaited_once_with("profile-test")
    claim.assert_called_once_with("exec-test", kind)
    record = svc.active_automation_tasks["profile-test"]
    assert record["container_owner_token"] == "owner-test"
    assert record["lifecycle_active"] is False
    if startup_fails:
        spawn.assert_not_awaited()
        assert result["success"] is False
        assert execution["status"] == "failed_before_publish"
        assert "Docker startup failed" in execution["error"]
        assert record["status"] == "failed"
    else:
        spawn.assert_awaited_once()
        args = spawn.await_args.args
        assert args[args.index("--task") + 1] == ("preparation" if worker == "preparation" else ("warming" if post_type == "warming" else "post"))
        assert record["container_started_by_queue"] is True
        assert result["success"] is True
        assert execution["status"] == ("ready" if worker == "preparation" else status)


@pytest.mark.parametrize("before, records", [("stopped", True), ("running", False), ("paused", False)])
def test_automation_start_clears_restart_needed(monkeypatch, before, records):
    """A container that automation starts fresh runs the saved settings."""
    import backend.services.automation_service as svc

    statuses = iter([before, "running", "running"])
    monkeypatch.setattr(svc, "get_container_status", lambda *_a, **_k: next(statuses, "running"))
    monkeypatch.setattr(svc, "run_profile_lifecycle_action", AsyncMock(return_value=""))
    monkeypatch.setattr(svc.lifecycle, "mark_used", lambda *_a: None)
    chk = SimpleNamespace(communicate=AsyncMock(return_value=(b"12345\n", b"")), returncode=0)
    monkeypatch.setattr(svc.asyncio, "create_subprocess_exec", AsyncMock(return_value=chk))
    recorded = []
    monkeypatch.setattr(svc, "record_effective_profile_environment", recorded.append)

    asyncio.run(svc.ensure_profile_container_ready("profile-test"))

    assert recorded == (["profile-test"] if records else [])
