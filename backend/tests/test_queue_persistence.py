"""Regression tests for queue persistence and dispatch reservations."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

from backend.services import automation_service, queue_service


def test_concurrent_queue_saves_never_corrupt_json(tmp_path, monkeypatch):
    queue_file = tmp_path / "posting_queue.json"
    monkeypatch.setattr(queue_service, "QUEUE_FILE", queue_file)

    payloads = [
        {
            "queue_version": "2.0",
            "daily_batches": [{"batch_id": f"batch-{index}", "blob": "x" * 20000}],
        }
        for index in range(20)
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(queue_service.save_posting_queue, payloads))

    assert all(results)
    persisted = json.loads(queue_file.read_text(encoding="utf-8"))
    assert persisted in payloads
    assert not list(tmp_path.glob("*.tmp"))


def test_load_queue_falls_back_to_last_known_good_backup(tmp_path, monkeypatch):
    queue_file = tmp_path / "posting_queue.json"
    backup_file = tmp_path / "posting_queue.json.bak"
    expected = {"queue_version": "2.0", "daily_batches": [{"batch_id": "safe"}]}
    queue_file.write_text('{"daily_batches": [broken', encoding="utf-8")
    backup_file.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(queue_service, "QUEUE_FILE", queue_file)

    assert queue_service.load_posting_queue() == expected


def test_dispatch_reservations_enforce_kind_total_and_profile_limits():
    config = {
        "max_publishers": 1,
        "max_preparers": 1,
        "max_total_automation_tasks": 2,
    }
    reservations = {"profiles": set(), "publishers": 0, "preparers": 0, "total": 0}

    assert automation_service._reserve_dispatch_slot(reservations, "publisher", "p1", config)
    assert not automation_service._reserve_dispatch_slot(reservations, "publisher", "p2", config)
    assert not automation_service._reserve_dispatch_slot(reservations, "preparer", "p1", config)
    assert automation_service._reserve_dispatch_slot(reservations, "preparer", "p2", config)
    assert not automation_service._reserve_dispatch_slot(reservations, "preparer", "p3", config)


def test_dispatched_claim_race_is_consumed_as_a_deferral(monkeypatch):
    async def reject_claim(_execution_id, _kind):
        raise RuntimeError("Scheduler could not claim execution: profile_busy")

    monkeypatch.setattr(automation_service, "execute_queue_item", reject_claim)

    result = asyncio.run(automation_service._run_dispatched_queue_task("exec-1", "publisher"))

    assert result == {
        "success": False,
        "status": "deferred",
        "error": "Scheduler could not claim execution: profile_busy",
    }
