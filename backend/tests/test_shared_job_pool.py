"""Preparing and posting draw from one pool of job slots."""

from backend.services.queue_scheduler import (
    DEFAULT_SCHEDULER_CONFIG,
    can_acquire_scheduler_slot,
    preparation_has_room,
)

NOW = 1_000_000.0


def lease(profile_id, kind, workload="post"):
    return {"lease_id": f"{profile_id}-{kind}", "profile_id": profile_id, "kind": kind,
            "workload": workload, "expires_at": "2999-01-01T00:00:00+00:00"}


def config(limit, **extra):
    return {**DEFAULT_SCHEDULER_CONFIG, "max_publishers": limit, "max_post_publishers": limit,
            "max_live_publishers": limit, "max_preparers": limit,
            "max_total_automation_tasks": limit, "max_active_profile_containers": limit, **extra}


def test_prepared_account_posts_while_another_account_posts():
    # Limit 2: A is posting. B finished preparing and holds no lease, so it
    # takes the second slot right away instead of waiting for A.
    result = can_acquire_scheduler_slot({}, [lease("A", "publisher")], "publisher", "B", config(2), NOW)
    assert result["allowed"] is True


def test_two_accounts_may_post_at_once_with_limit_two():
    leases = [lease("A", "publisher"), lease("B", "publisher")]
    assert can_acquire_scheduler_slot({}, leases, "preparer", "C", config(2), NOW)["reason"] == "total_capacity_reached"
    assert can_acquire_scheduler_slot({}, leases[:1], "preparer", "C", config(2), NOW)["allowed"] is True


def test_custom_post_cap_still_limits_the_posting_step():
    cfg = config(4, max_post_publishers=1)
    leases = [lease("A", "publisher")]
    assert can_acquire_scheduler_slot({}, leases, "publisher", "B", cfg, NOW)["reason"] == "posts_capacity_reached"
    assert can_acquire_scheduler_slot({}, leases, "publisher", "B", cfg, NOW, workload="live")["allowed"] is True


def test_preparation_waits_when_it_would_have_no_posting_slot():
    cfg = config(2)
    assert preparation_has_room(0, 1, cfg) is True    # one posting, one free slot
    assert preparation_has_room(1, 1, cfg) is False   # a prepared account already claims that slot
    assert preparation_has_room(0, 0, config(4, max_post_publishers=1)) is True
    assert preparation_has_room(1, 0, config(4, max_post_publishers=1)) is False
