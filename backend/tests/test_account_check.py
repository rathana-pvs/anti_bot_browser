"""Check now adds one read-only check that starts at once, never twice, never on hold."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.routers import profiles
from backend.services import account_check, account_hold
from backend.services.account_health import compute
from automation.warming_options import normalize_warming_options

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)


def test_a_check_is_a_short_profile_visit_in_its_own_batch():
    queue = {"daily_batches": [{"batch_id": "b1", "posts": []}]}
    execution = account_check.add_check(queue, "profile_017", NOW)
    batch = queue["daily_batches"][-1]
    assert account_check.is_check_batch(batch) and batch["schedule_window"]["start_now"] is True
    post = batch["posts"][0]
    assert post["type"] == "warming" and normalize_warming_options(post["warming_options"])["check"] is True
    assert execution["account_check"] is True and execution["preparation_mode"] == "off"
    assert account_check.open_check(queue, "profile_017") is execution
    execution["status"] = "completed"
    assert account_check.open_check(queue, "profile_017") is None


def test_a_passed_check_moves_the_account_out_of_attention():
    config = {"id": "p", "created_at": "2026-09-01T00:00:00+00:00", "account": {}}
    checkpoint = {"at": "2026-10-30T08:00:00+00:00", "kind": "reel", "status": "skipped_auth_required", "error": ""}
    assert compute(config, [checkpoint], NOW)["stage"] == "attention"
    passed = {"at": "2026-10-31T14:00:00+00:00", "kind": "warming", "status": "completed", "error": "", "identity": "page"}
    assert compute(config, [checkpoint, passed], NOW)["stage"] != "attention"
    personal = dict(checkpoint, status="skipped_personal_profile", identity="personal")
    assert compute(config, [personal], NOW)["stage"] == "attention"
    assert compute(config, [personal, passed], NOW)["stage"] != "attention"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    directory = tmp_path / "profiles" / "profile_017"
    directory.mkdir(parents=True)
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles")
    path = directory / "config.json"
    path.write_text(json.dumps({"id": "profile_017", "name": "A", "account": {}}))
    queue = {"daily_batches": []}
    from backend.services import automation_service, queue_service
    monkeypatch.setattr(profiles, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(queue_service, "save_posting_queue", lambda value: True)
    monkeypatch.setattr(queue_service, "with_queue_claim_lock", lambda callback: callback())
    dispatched = []
    async def dispatch():
        dispatched.append(1)
    monkeypatch.setattr(automation_service, "dispatch_pending_queue", dispatch)
    return path, queue, dispatched


def test_check_now_queues_once(setup):
    path, queue, dispatched = setup
    async def run_twice():
        first = await profiles.check_profile("profile_017")
        second = await profiles.check_profile("profile_017")
        await asyncio.sleep(0)
        return first, second
    first, second = asyncio.run(run_twice())
    assert first["created"] and not second["created"]
    assert first["execution_id"] == second["execution_id"]
    assert len(queue["daily_batches"][0]["posts"]) == 1 and dispatched == [1]


def test_check_now_refuses_an_account_on_hold(setup):
    path, queue, _ = setup
    config = json.loads(path.read_text())
    account_hold.hold(config, "", NOW)
    path.write_text(json.dumps(config))
    with pytest.raises(HTTPException) as error:
        asyncio.run(profiles.check_profile("profile_017"))
    assert error.value.status_code == 409 and queue["daily_batches"] == []
