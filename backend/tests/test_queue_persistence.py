"""Regression tests for queue persistence and dispatch reservations."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
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


def test_save_rotates_its_own_last_write_into_the_backup_without_reparsing(tmp_path, monkeypatch):
    queue_file = tmp_path / "posting_queue.json"
    backup_file = tmp_path / "posting_queue.json.bak"
    monkeypatch.setattr(queue_service, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(queue_service, "_last_written_signature", None)
    first = {"queue_version": "2.0", "daily_batches": [{"batch_id": "first"}]}
    second = {"queue_version": "2.0", "daily_batches": [{"batch_id": "second"}]}

    assert queue_service.save_posting_queue(first)
    reads = []
    original_read = queue_service._read_queue_file
    monkeypatch.setattr(queue_service, "_read_queue_file", lambda path: reads.append(path) or original_read(path))
    assert queue_service.save_posting_queue(second)

    assert reads == []
    assert json.loads(backup_file.read_text(encoding="utf-8")) == first
    assert json.loads(queue_file.read_text(encoding="utf-8")) == second
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))


def test_save_never_backs_up_a_primary_someone_else_corrupted(tmp_path, monkeypatch):
    queue_file = tmp_path / "posting_queue.json"
    backup_file = tmp_path / "posting_queue.json.bak"
    monkeypatch.setattr(queue_service, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(queue_service, "_last_written_signature", None)
    good = {"queue_version": "2.0", "daily_batches": [{"batch_id": "good"}]}
    assert queue_service.save_posting_queue(good)
    assert queue_service.save_posting_queue(good)  # backup now holds `good`
    queue_file.write_text('{"daily_batches": [broken', encoding="utf-8")

    newer = {"queue_version": "2.0", "daily_batches": [{"batch_id": "newer"}]}
    assert queue_service.save_posting_queue(newer)

    assert json.loads(backup_file.read_text(encoding="utf-8")) == good
    assert json.loads(queue_file.read_text(encoding="utf-8")) == newer


def _queue_with(execution):
    return {"queue_version": "2.0", "daily_batches": [{"batch_id": "b", "posts": [{"type": "post", "executions": [execution]}]}]}


def _details_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(queue_service, "QUEUE_FILE", tmp_path / "posting_queue.json")
    monkeypatch.setattr(queue_service, "_last_written_signature", None)


FULL_TELEMETRY = {"total_duration_ms": 1200, "outcome": "published", "ocr": {"samples": [{"duration_ms": 5}] * 50},
                  "locators": {"tier_counts": {"ocr": 2}, "fallback_count": 1, "events": [{"x": 1}] * 20},
                  "steps": [{"step": "x"}] * 30}


def test_finished_items_keep_logs_and_telemetry_beside_the_queue(tmp_path, monkeypatch):
    _details_setup(tmp_path, monkeypatch)
    execution = {"execution_id": "e1", "status": "published", "logs": ["a", "b"], "telemetry": FULL_TELEMETRY,
                 "preparation_telemetry": {"total_duration_ms": 10}}
    queue = _queue_with(execution)
    assert queue_service.save_posting_queue(queue)

    assert "logs" in execution  # the caller's object is not changed
    on_disk = json.loads((tmp_path / "posting_queue.json").read_text())["daily_batches"][0]["posts"][0]["executions"][0]
    assert not any(key in on_disk for key in queue_service.DETAIL_FIELDS)
    assert on_disk["details_stored"] is True and on_disk["log_count"] == 2
    assert on_disk["telemetry_digest"] == {"total_duration_ms": 1200, "outcome": "published",
                                           "locators": {"tier_counts": {"ocr": 2}, "fallback_count": 1}}
    loaded = queue_service.find_queue_execution(queue_service.load_posting_queue(), "e1")["execution"]
    assert queue_service.hydrate_execution(loaded)["telemetry"] == FULL_TELEMETRY
    assert queue_service.execution_logs(loaded) == ["a", "b"]


def test_running_items_keep_their_details_inline(tmp_path, monkeypatch):
    _details_setup(tmp_path, monkeypatch)
    for status in ("running", "uncertain", "needs_review", "ready"):
        execution = {"execution_id": f"e-{status}", "status": status, "logs": ["a"], "telemetry": FULL_TELEMETRY}
        assert queue_service.save_posting_queue(_queue_with(execution))
        assert queue_service.load_posting_queue()["daily_batches"][0]["posts"][0]["executions"][0]["logs"] == ["a"]
    assert not (tmp_path / "queue_details").exists()


def test_a_later_attempt_replaces_only_the_details_it_wrote(tmp_path, monkeypatch):
    _details_setup(tmp_path, monkeypatch)
    queue_service.save_posting_queue(_queue_with(
        {"execution_id": "e1", "status": "failed", "logs": ["first"], "telemetry": FULL_TELEMETRY}))
    loaded = queue_service.load_posting_queue()
    execution = loaded["daily_batches"][0]["posts"][0]["executions"][0]
    # A comment retry appends to the stored logs and brings no telemetry.
    execution.update(status="published", logs=[*queue_service.execution_logs(execution), "retry"])
    queue_service.save_posting_queue(loaded)

    stored = queue_service.load_posting_queue()["daily_batches"][0]["posts"][0]["executions"][0]
    assert queue_service.execution_logs(stored) == ["first", "retry"]
    assert queue_service.hydrate_execution(stored)["telemetry"] == FULL_TELEMETRY


def test_orphaned_details_are_removed(tmp_path, monkeypatch):
    _details_setup(tmp_path, monkeypatch)
    queue_service.save_posting_queue(_queue_with({"execution_id": "e1", "status": "published", "logs": ["a"]}))
    queue_service.save_posting_queue({"queue_version": "2.0", "daily_batches": [{"batch_id": "x", "posts": [{"executions": [
        {"execution_id": "e2", "status": "published", "logs": ["b"]}]}]}]})
    assert queue_service.remove_orphan_details(queue_service.load_posting_queue()) == 1
    assert [path.name for path in (tmp_path / "queue_details").iterdir()] == ["e2.json"]


def test_unsafe_execution_ids_stay_inline(tmp_path, monkeypatch):
    _details_setup(tmp_path, monkeypatch)
    queue_service.save_posting_queue(_queue_with({"execution_id": "../evil", "status": "published", "logs": ["a"]}))
    assert queue_service.load_posting_queue()["daily_batches"][0]["posts"][0]["executions"][0]["logs"] == ["a"]


def test_queue_list_leaves_details_out_and_the_drawer_reads_them(tmp_path, monkeypatch):
    from backend.routers import queue as queue_router
    _details_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(queue_router, "load_posting_queue", queue_service.load_posting_queue)
    queue_service.save_posting_queue({"queue_version": "2.0", "daily_batches": [{"batch_id": "b", "posts": [{"type": "post", "executions": [
        {"execution_id": "done", "status": "published", "scheduled_at": "2026-01-01T00:00:00+00:00",
         "logs": ["a", "b"], "telemetry": {"total_duration_ms": 7}},
        {"execution_id": "live", "status": "running", "scheduled_at": "2026-01-01T00:00:00+00:00", "logs": ["c"]},
    ]}]}]})

    body = queue_router.queue_snapshot()

    assert "executions" not in body["batches"][0]["posts"][0]
    for item in body["executions"]:
        assert not any(key in item for key in queue_service.DETAIL_FIELDS)
    counts = {item["execution_id"]: item["log_count"] for item in body["executions"]}
    assert counts == {"done": 2, "live": 1}
    assert body["telemetry_summary"]["median_execution_duration_ms"] == 7
    assert queue_router.get_execution_details("done")["logs"] == ["a", "b"]
    assert queue_router.get_execution_details("live")["logs"] == ["c"]


def test_unchanged_queue_list_answers_304_without_a_body(tmp_path, monkeypatch):
    from starlette.requests import Request
    from backend.routers import queue as queue_router
    _details_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(queue_router, "load_posting_queue", queue_service.load_posting_queue)
    queue_service.save_posting_queue({"queue_version": "2.0", "daily_batches": [{"batch_id": "b", "posts": [{"type": "post", "executions": [
        {"execution_id": "one", "status": "pending", "scheduled_at": "2026-01-01T00:00:00+00:00"},
    ]}]}]})

    monkeypatch.setattr(queue_router.time, "time", lambda: 1_800_000_000.0)

    def get(etag=None):
        headers = [(b"if-none-match", etag.encode())] if etag else []
        return queue_router.get_queue(Request({"type": "http", "method": "GET", "path": "/api/queue", "headers": headers}))

    first = get()
    etag = first.headers["etag"]
    assert first.status_code == 200 and json.loads(first.body)["executions"][0]["execution_id"] == "one"
    again = get(etag)
    assert again.status_code == 304 and again.body == b"" and again.headers["etag"] == etag

    queue = queue_service.load_posting_queue()
    queue["daily_batches"][0]["posts"][0]["executions"][0]["status"] = "published"
    queue_service.save_posting_queue(queue)
    changed = get(etag)
    assert changed.status_code == 200 and changed.headers["etag"] != etag


def test_queue_list_limits_finished_history_but_keeps_jobs_that_need_the_user(tmp_path, monkeypatch):
    from backend.routers import queue as queue_router
    _details_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(queue_router, "load_posting_queue", queue_service.load_posting_queue)
    old, recent = "2026-01-01T00:00:00+00:00", "2026-01-30T12:00:00+00:00"
    monkeypatch.setattr(queue_router.time, "time", lambda: datetime.fromisoformat("2026-01-31T00:00:00+00:00").timestamp())
    jobs = [
        {"execution_id": "old_posted", "status": "published", "published_at": old, "scheduled_at": old,
         "stage_history": [{"stage": "published"}], "container_owner_token": "secret"},
        {"execution_id": "recent_posted", "status": "published", "published_at": recent, "scheduled_at": recent},
        {"execution_id": "old_failed", "status": "failed", "scheduled_at": old},
        {"execution_id": "old_comment", "status": "published", "published_at": old, "scheduled_at": old,
         "first_comment_status": "failed_input_not_found"},
        {"execution_id": "old_recovering", "status": "stopped", "scheduled_at": old, "network_recovery": {"state": "waiting_network"}},
        {"execution_id": "waiting", "status": "pending", "scheduled_at": old},
    ]
    queue_service.save_posting_queue({"queue_version": "2.0", "daily_batches": [{"batch_id": "b", "posts": [{"type": "post", "executions": jobs}]}]})

    everything = queue_router.queue_snapshot()
    assert len(everything["executions"]) == 6 and everything["history"]["older_finished"] == 0
    assert not any(key in item for item in everything["executions"] for key in queue_router.LIST_OMITTED_FIELDS)

    recent_only = queue_router.queue_snapshot(finished_days=3)
    assert {item["execution_id"] for item in recent_only["executions"]} == {
        "recent_posted", "old_failed", "old_comment", "old_recovering", "waiting"}
    assert recent_only["history"] == {"finished_days": 3, "older_finished": 1}
    assert recent_only["batches"][0]["job_counts"] == {"total": 6, "done": 3}

    active_only = queue_router.queue_snapshot(finished_days=0)
    assert "recent_posted" not in {item["execution_id"] for item in active_only["executions"]}
    assert queue_router.get_execution_details("old_posted")["stage_history"] == [{"stage": "published"}]
