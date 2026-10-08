from datetime import datetime, timezone

import pytest

from backend.services.profile_pipeline import active_batch_id, batch_iteration_availability, ordered_due_executions
from backend.services.queue_scheduler import compute_execution_schedule


def _timestamp_ms(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000


def _execution(execution_id, status="pending", ended_at=None, profile_id="profile-1"):
    execution = {
        "execution_id": execution_id,
        "status": status,
        "profile_id": profile_id,
    }
    if ended_at:
        execution["ended_at"] = ended_at
        execution["last_scheduler_lease"] = {"kind": "publisher"}
    return execution


def _batch(delay_seconds=60, posts=None, executions=None):
    return {
        "schedule_window": {"batch_iteration_delay_seconds": delay_seconds},
        "posts": posts or [{"executions": executions or []}],
    }


def test_batch_iteration_delay_waits_from_publisher_completion():
    target = _execution("post-2-profile-1")
    batch = _batch(posts=[
        {"executions": [
            _execution("post-1-profile-1", "published", "2026-09-28T11:59:50+00:00"),
            _execution("post-1-profile-2", "published", "2026-09-28T12:00:00+00:00", "profile-2"),
        ]},
        {"executions": [target]},
    ])

    waiting = batch_iteration_availability(
        batch,
        _timestamp_ms("2026-09-28T12:00:30"),
        target_execution=target,
    )
    ready = batch_iteration_availability(
        batch,
        _timestamp_ms("2026-09-28T12:01:01"),
        target_execution=target,
    )

    assert waiting["allowed"] is False
    assert waiting["reason"] == "batch_iteration_delay"
    assert waiting["retry_after_ms"] == 20_000
    assert ready["allowed"] is True


def test_profiles_within_same_post_handoff_without_iteration_delay():
    target = _execution("post-1-profile-2")
    batch = _batch(posts=[{"executions": [
        _execution("post-1-profile-1", "published", "2026-09-28T12:00:00+00:00"),
        target,
    ]}])

    result = batch_iteration_availability(
        batch,
        _timestamp_ms("2026-09-28T12:00:01"),
        target_execution=target,
    )

    assert result == {
        "allowed": True,
        "reason": "batch_iteration_ready",
        "retry_after_ms": 0,
    }


def test_next_post_does_not_wait_for_other_profiles():
    target = _execution("post-2-profile-1")
    batch = _batch(posts=[
        {"executions": [
            _execution("post-1-profile-1", "published", "2026-09-28T12:00:00+00:00"),
            _execution("post-1-profile-2", "ready", profile_id="profile-2"),
        ]},
        {"executions": [target]},
    ])

    result = batch_iteration_availability(
        batch,
        _timestamp_ms("2026-09-28T12:02:00"),
        target_execution=target,
    )

    assert result == {
        "allowed": True,
        "reason": "batch_iteration_ready",
        "retry_after_ms": 0,
    }


@pytest.mark.parametrize("status", ["running", "uncertain", "needs_review"])
def test_other_profiles_do_not_hold_this_profiles_next_iteration(status):
    target = _execution("next")
    batch = _batch(posts=[{"executions": [
        _execution("previous", "published", "2026-09-28T12:00:00+00:00"),
        _execution("other", status, profile_id="profile-2"),
    ]}, {"executions": [target]}])
    assert batch_iteration_availability(
        batch, _timestamp_ms("2026-09-28T12:01:00"), target)["allowed"]


def test_profile_delay_uses_its_last_execution_when_absent_from_previous_post():
    target = _execution("third")
    batch = _batch(posts=[
        {"executions": [_execution("first", "published", "2026-09-28T12:00:00+00:00")]},
        {"executions": [_execution("other", "running", profile_id="profile-2")]},
        {"executions": [target]},
    ])
    result = batch_iteration_availability(batch, _timestamp_ms("2026-09-28T12:00:30"), target)
    assert result["reason"] == "batch_iteration_delay"
    assert result["retry_after_ms"] == 30_000


def test_terminal_preparation_failure_still_starts_profile_delay():
    previous = _execution("previous", "failed_before_publish", "2026-09-28T12:00:00+00:00")
    previous["last_scheduler_lease"] = {"kind": "preparer"}
    target = _execution("next")
    batch = _batch(posts=[{"executions": [previous]}, {"executions": [target]}])
    result = batch_iteration_availability(batch, _timestamp_ms("2026-09-28T12:00:30"), target)
    assert result["reason"] == "batch_iteration_delay"
    assert result["retry_after_ms"] == 30_000


def test_same_post_allows_another_profile_while_publisher_is_running():
    target = _execution("second-profile")
    batch = _batch(executions=[{
        "status": "running",
        "scheduler_lease": {"kind": "publisher"},
    }, target])

    result = batch_iteration_availability(
        batch,
        _timestamp_ms("2026-09-28T12:00:00"),
        target_execution=target,
    )

    assert result == {
        "allowed": True,
        "reason": "batch_iteration_ready",
        "retry_after_ms": 0,
    }


@pytest.mark.parametrize("status", ["pending", "ready", "running", "preparing", "uncertain", "needs_review"])
def test_later_posts_wait_for_unfinished_or_unverified_earlier_posts(status):
    target = _execution("post-3")
    batch = _batch(delay_seconds=0, posts=[
        {"executions": [_execution("post-1", status)]},
        {"executions": [_execution("post-2", "published")]},
        {"executions": [target]},
    ])
    result = batch_iteration_availability(batch, target_execution=target)
    assert result["allowed"] is False
    assert result["reason"] == "batch_iteration_waiting_for_profiles"


def test_legacy_batch_without_iteration_delay_remains_ready():
    batch = _batch(delay_seconds=0, executions=[{
        "status": "published",
        "ended_at": "2026-09-28T12:00:00+00:00",
        "last_scheduler_lease": {"kind": "publisher"},
    }])

    assert batch_iteration_availability(
        batch,
        _timestamp_ms("2026-09-28T12:00:00"),
    )["allowed"] is True


def test_start_now_uses_completion_pacing_instead_of_post_window_slots():
    now_ms = _timestamp_ms("2026-09-28T12:00:00")
    first = compute_execution_schedule(
        now_ms=now_ms,
        is_start_now=True,
        slot_index=0,
        profile_index=0,
        stagger_ms=60_000,
        post_slot_ms=12 * 60 * 60_000,
        window_start_ms=now_ms,
    )
    second = compute_execution_schedule(
        now_ms=now_ms,
        is_start_now=True,
        slot_index=1,
        profile_index=0,
        stagger_ms=60_000,
        post_slot_ms=12 * 60 * 60_000,
        window_start_ms=now_ms,
    )

    assert first == now_ms
    assert second == now_ms


def test_scheduled_batch_releases_all_iterations_at_its_start_gate():
    queue = {"daily_batches": [{
        "schedule_window": {"profile_stagger_seconds": 0},
        "posts": [
            {"executions": [{
                "execution_id": "first", "profile_id": "p1", "status": "pending",
                "scheduled_at": "2026-09-28T12:00:00+00:00", "batch_iteration_index": 0,
            }]},
            {"executions": [{
                "execution_id": "second", "profile_id": "p1", "status": "pending",
                "scheduled_at": "2026-09-28T20:00:00+00:00", "batch_iteration_index": 1,
            }]},
        ],
    }]}

    due = ordered_due_executions(queue, _timestamp_ms("2026-09-28T12:01:00"))
    assert [item["execution_id"] for item in due] == ["first", "second"]


def test_uncertain_execution_does_not_block_later_pending_work():
    queue = {"daily_batches": [
        {
            "batch_id": "older-review",
            "created_at": "2026-09-28T12:00:00+00:00",
            "posts": [{"executions": [{
                "execution_id": "uncertain-first",
                "profile_id": "p1",
                "status": "uncertain",
                "started_at": "2026-09-28T12:01:00+00:00",
                "scheduled_at": "2026-09-28T12:00:00+00:00",
            }]}],
        },
        {
            "batch_id": "next-batch",
            "created_at": "2026-09-28T12:05:00+00:00",
            "posts": [{"executions": [{
                "execution_id": "next-post",
                "profile_id": "p1",
                "status": "pending",
                "scheduled_at": "2026-09-28T12:05:00+00:00",
            }]}],
        },
    ]}

    due = ordered_due_executions(queue, _timestamp_ms("2026-09-28T12:06:00"))
    assert [item["execution_id"] for item in due] == ["next-post"]


def test_started_batch_keeps_queue_ownership_until_its_pending_work_finishes():
    queue = {"daily_batches": [
        {
            "batch_id": "later",
            "created_at": "2026-09-28T12:05:00+00:00",
            "posts": [{"executions": [{"status": "pending"}]}],
        },
        {
            "batch_id": "active",
            "created_at": "2026-09-28T12:00:00+00:00",
            "posts": [{"executions": [
                {"status": "published", "started_at": "2026-09-28T12:01:00+00:00"},
                {"status": "pending"},
            ]}],
        },
    ]}

    assert active_batch_id(queue) == "active"

    queue["daily_batches"][1]["posts"][0]["executions"][1]["status"] = "published"
    assert active_batch_id(queue) is None


if __name__ == "__main__":
    test_batch_iteration_delay_waits_from_publisher_completion()
    test_same_post_allows_another_profile_while_publisher_is_running()
    test_legacy_batch_without_iteration_delay_remains_ready()
    test_start_now_uses_completion_pacing_instead_of_post_window_slots()
    test_scheduled_batch_releases_all_iterations_at_its_start_gate()
    test_started_batch_keeps_queue_ownership_until_its_pending_work_finishes()
    print("batch iteration checks passed")
