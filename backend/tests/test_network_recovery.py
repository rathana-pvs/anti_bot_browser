"""No browser or external network: exercise persisted recovery and queue safety."""
import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest

from backend.services import network_recovery as policy
from backend.services import queue_network_recovery as service
from backend.services.profile_pipeline import ordered_due_executions, batch_iteration_availability

ORIGIN = policy.timestamp("2026-10-25T22:00:00+07:00")


def execution(status="failed_before_publish", **extra):
    return dict(execution_id="recovery", profile_id="profile_1", status=status,
                stage="failed_before_publish", error="reel_publish_timeout",
                ended_at=policy.iso(ORIGIN), **extra)


def enroll(item=None):
    item = item if item is not None else execution()
    policy.refresh_recovery(item, "reel", ORIGIN)
    return item


def sample(upload=2, download=2, facebook=True):
    return {"upload": {"success": True, "mbps": upload},
            "download": {"success": True, "mbps": download},
            "facebook": {"success": facebook}}


def recovered(item, now=ORIGIN + 330, fingerprint="proxy"):
    policy.apply_measurement(item, sample(), fingerprint, now - 30)
    policy.apply_measurement(item, sample(), fingerprint, now)


def test_rolling_deadline_and_restart_do_not_reset_window():
    item = enroll()
    recovery = item["network_recovery"]
    assert recovery["deadline_at"] == "2026-10-26T15:00:00+00:00"
    for offset in (300, 900, 1800, 3600, 7200, 23 * 3600):
        assert policy.timestamp(policy.next_check(recovery, ORIGIN + offset - 1)) == ORIGIN + offset
    restarted = deepcopy(item)
    policy.refresh_recovery(restarted, "reel", ORIGIN + 12 * 3600)
    assert restarted["network_recovery"]["deadline_at"] == recovery["deadline_at"]
    policy.refresh_recovery(restarted, "reel", ORIGIN + 24 * 3600)
    assert restarted["network_recovery"]["state"] == "expired"


@pytest.mark.parametrize("extra,post_type", [
    ({"cloud_attempt_id": "cloud"}, "reel"), ({}, "live"), ({}, "photo"),
    ({"cancel_requested_at": policy.iso(ORIGIN)}, "reel"),
])
def test_cloud_and_non_reel_work_never_enroll(extra, post_type):
    item = execution(**extra)
    policy.refresh_recovery(item, post_type, ORIGIN)
    assert "network_recovery" not in item


@pytest.mark.parametrize("upload,download,facebook", [
    (1, 2, True), (2, 1, True), (0.5, 2, True), (2, 2, False),
    (float("nan"), 2, True), (float("inf"), 2, True), (None, 2, True),
])
def test_threshold_is_above_one_mbps_in_both_directions(upload, download, facebook):
    item = enroll()
    policy.apply_measurement(item, sample(upload, download, facebook), "proxy", ORIGIN + 300)
    assert item["network_recovery"]["pass_count"] == 0
    assert item["network_recovery"]["state"] == "waiting_network"
    assert policy.timestamp(item["network_recovery"]["next_check_at"]) == ORIGIN + 900


def test_two_samples_need_same_proxy_and_thirty_second_separation():
    item = enroll()
    policy.apply_measurement(item, sample(), "one", ORIGIN + 300)
    assert not policy.retry_admitted(item, ORIGIN + 300)
    policy.apply_measurement(item, sample(), "two", ORIGIN + 330)
    assert item["network_recovery"]["pass_count"] == 1
    policy.apply_measurement(item, sample(), "two", ORIGIN + 360)
    assert policy.queue_retry(item, ORIGIN + 360)
    assert policy.retry_admitted(item, ORIGIN + 360)
    assert not policy.retry_admitted(item, ORIGIN + 661)


@pytest.mark.parametrize("status", ["uncertain", "needs_review", "failed_after_publish", "failed"])
def test_fast_proxy_cannot_retry_unknown_or_post_click_outcome(status):
    item = enroll(execution(status))
    item["stage"] = "verifying" if status != "failed" else "unknown"
    recovered(item)
    assert item["network_recovery"]["state"] == "waiting_review"
    assert not policy.queue_retry(item, ORIGIN + 330)
    assert not policy.retry_admitted(item, ORIGIN + 330)


def test_operator_resolution_allows_retry_and_archives_original_click_evidence():
    item = enroll(execution("uncertain"))
    item["stage_history"] = [{"stage": "publish_clicked", "timestamp": policy.iso(ORIGIN)}]
    item["evidence_dir"] = "original/evidence"
    item["last_scheduler_lease"] = {"lease_id": "original"}
    recovered(item)
    item.update(status="failed_before_publish", stage="failed_before_publish", review_status="resolved_not_published")
    item["stage_history"].append({"stage": "failed_before_publish", "reason": "operator_resolved_not_published"})
    assert policy.queue_retry(item, ORIGIN + 340)
    saved = item["network_recovery"]["attempts"][0]
    assert saved["evidence_dir"] == "original/evidence"
    assert saved["last_scheduler_lease"]["lease_id"] == "original"
    assert saved["stage_history"][0]["stage"] == "publish_clicked"
    assert item["stage_history"][0]["reason"] == "network_recovery_queued"
    assert len(item["network_recovery"]["attempts"]) == 1
    policy.queue_retry(item, ORIGIN + 345)
    assert len(item["network_recovery"]["attempts"]) == 1


def test_retry_limit_and_new_failure_preserve_original_deadline():
    for count, error, expected in [(2, "reel_publish_timeout", "retry_limit"),
                                    (1, "login_required", "needs_review")]:
        item = enroll()
        recovery = item["network_recovery"]
        recovery.update(state="retrying", retry_count=count)
        item["error"] = error
        policy.refresh_recovery(item, "reel", ORIGIN + 3600)
        assert recovery["state"] == expected
        assert policy.timestamp(recovery["deadline_at"]) == ORIGIN + 24 * 3600
        recovered(item, ORIGIN + 3630)
        if count == 2:
            assert not policy.queue_retry(item, ORIGIN + 3630)


def test_deadline_blocks_waiting_retry_but_does_not_interrupt_publisher():
    item = enroll()
    recovered(item, ORIGIN + 24 * 3600 - 60)
    assert policy.queue_retry(item, ORIGIN + 24 * 3600 - 60)
    assert not policy.retry_admitted(item, ORIGIN + 24 * 3600)
    policy.refresh_recovery(item, "reel", ORIGIN + 24 * 3600)
    assert item["status"] == "failed_before_publish"
    assert item["network_recovery"]["state"] == "expired"
    item["network_recovery"]["state"] = "retrying"
    item["status"] = "running"
    policy.refresh_recovery(item, "reel", ORIGIN + 24 * 3600 + 20)
    assert item["status"] == "running"
    assert item["network_recovery"]["state"] == "retrying"


def test_recovery_waits_behind_normal_queue_and_holds_its_account():
    item = enroll()
    recovered(item)
    policy.queue_retry(item, ORIGIN + 330)
    normal = dict(execution_id="normal", profile_id="profile_2", status="pending")
    later = dict(execution_id="later", profile_id="profile_1", status="pending")
    batch = {"posts": [{"executions": [item]}, {"executions": [later]}]}
    queue = {"daily_batches": [batch, {"posts": [{"executions": [normal]}]}]}
    due = ordered_due_executions(queue, (ORIGIN + 330) * 1000)
    assert [ex["execution_id"] for ex in due] == ["normal", "recovery"]
    assert not batch_iteration_availability(batch, (ORIGIN + 330) * 1000, later)["allowed"]
    item["network_recovery"]["state"] = "expired"
    item["status"] = "failed_before_publish"
    assert not batch_iteration_availability(batch, (ORIGIN + 330) * 1000, later)["allowed"]


def setup_service(monkeypatch, item):
    queue = {"daily_batches": [{"batch_id": "batch", "posts": [{"type": "reel", "executions": [item]}]}]}
    monkeypatch.setattr(service, "load_posting_queue", lambda: deepcopy(queue))
    def save(value):
        queue.clear()
        queue.update(deepcopy(value))
        return True
    monkeypatch.setattr(service, "save_posting_queue", Mock(side_effect=save))
    monkeypatch.setattr(service, "with_queue_claim_lock", lambda callback: callback())
    monkeypatch.setattr(service.time, "time", lambda: ORIGIN + 300)
    monkeypatch.setattr(service, "profile_network", lambda pid: {"proxy_host": "example.invalid", "proxy_port": 1080})
    monkeypatch.setattr(service, "proxy_is_busy", lambda *args: False)
    monkeypatch.setattr(service.lifecycle, "UPDATE_MAINTENANCE", False)
    return queue


def test_background_checks_persist_confirmation_and_queue_safe_retry(monkeypatch):
    queue = setup_service(monkeypatch, enroll())
    measure = Mock(return_value=sample())
    monkeypatch.setattr(service, "_measure", measure)
    asyncio.run(service.check_network_recovery())
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    assert item["network_recovery"]["state"] == "confirming_network"
    assert item["status"] == "failed_before_publish"
    # Simulated backend restart: no process-local confirmation state is needed.
    monkeypatch.setattr(service.time, "time", lambda: ORIGIN + 330)
    asyncio.run(service.check_network_recovery())
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    assert item["status"] == "pending"
    assert item["network_recovery"]["state"] == "ready"
    assert item["network_recovery"]["retry_count"] == 0
    assert measure.call_count == 2


def test_background_probe_reservation_expires_after_restart(monkeypatch):
    item = enroll()
    queue = setup_service(monkeypatch, item)
    first = service.maintain_and_claim_check()
    assert first
    assert service.maintain_and_claim_check() is None
    monkeypatch.setattr(service.time, "time", lambda: ORIGIN + 421)
    second = service.maintain_and_claim_check()
    assert second and second[1] != first[1]
    assert queue["daily_batches"][0]["posts"][0]["executions"][0]["network_recovery"]["probe_token"] == second[1]


def test_cancel_during_probe_cannot_resurrect_retry(monkeypatch):
    queue = setup_service(monkeypatch, enroll())
    def cancel_while_measuring(_network):
        item = queue["daily_batches"][0]["posts"][0]["executions"][0]
        policy.stop_recovery(item, "cancelled")
        item["cancel_requested_at"] = policy.iso(ORIGIN + 301)
        return sample()
    monkeypatch.setattr(service, "_measure", cancel_while_measuring)
    asyncio.run(service.check_network_recovery())
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    assert item["network_recovery"]["state"] == "cancelled"
    assert item["status"] == "failed_before_publish"


def test_busy_proxy_defers_without_measuring_and_does_not_block_other_accounts(monkeypatch):
    queue = setup_service(monkeypatch, enroll())
    monkeypatch.setattr(service, "proxy_is_busy", lambda *args: True)
    measure = Mock()
    monkeypatch.setattr(service, "_measure", measure)
    asyncio.run(service.check_network_recovery())
    measure.assert_not_called()
    assert queue["daily_batches"][0]["posts"][0]["executions"][0]["network_recovery"]["check_count"] == 0


def test_failed_persistence_cannot_start_measurement(monkeypatch):
    setup_service(monkeypatch, enroll())
    monkeypatch.setattr(service, "save_posting_queue", lambda queue: False)
    measure = Mock()
    monkeypatch.setattr(service, "_measure", measure)
    with pytest.raises(OSError, match="persist"):
        asyncio.run(service.check_network_recovery())
    measure.assert_not_called()


def test_real_claim_counts_publishing_retries_and_stops_at_two(monkeypatch):
    from backend.services import automation_service as automation
    from backend.services.queue_scheduler import DEFAULT_SCHEDULER_CONFIG
    queue = setup_service(monkeypatch, enroll())
    monkeypatch.setattr(automation, "load_posting_queue", service.load_posting_queue)
    monkeypatch.setattr(automation, "save_posting_queue", service.save_posting_queue)
    monkeypatch.setattr(automation, "with_queue_claim_lock", lambda callback: callback())
    monkeypatch.setattr(automation, "get_scheduler_config", lambda: DEFAULT_SCHEDULER_CONFIG)
    monkeypatch.setattr(automation, "active_in_memory_scheduler_leases", lambda: [])
    monkeypatch.setattr(automation.lifecycle, "acquire", Mock())
    context = automation.lifecycle.OWNER_TOKEN.set("owner")
    try:
        for count in (1, 2):
            now = ORIGIN + count * 900
            monkeypatch.setattr(service.time, "time", lambda: now)
            item = queue["daily_batches"][0]["posts"][0]["executions"][0]
            recovered(item, now, service.current_fingerprint(item))
            assert policy.queue_retry(item, now)
            result = automation.claim_queue_execution("recovery", "publisher")
            assert result["targetExec"]["network_recovery"]["retry_count"] == count
            item = queue["daily_batches"][0]["posts"][0]["executions"][0]
            item.update(status="failed_before_publish", stage="failed_before_publish",
                        scheduler_lease=None, error="reel_publish_timeout")
            policy.refresh_recovery(item, "reel", now + 1)
        assert item["network_recovery"]["state"] == "retry_limit"
        with pytest.raises(RuntimeError, match="Network recovery"):
            automation.claim_queue_execution("recovery", "publisher")
        assert len(item["network_recovery"]["attempts"]) == 2
    finally:
        automation.lifecycle.OWNER_TOKEN.reset(context)


def test_claim_rechecks_age_and_proxy_before_acquiring_a_lease(monkeypatch):
    from backend.services import automation_service as automation
    item = enroll()
    queue = setup_service(monkeypatch, item)
    monkeypatch.setattr(automation, "load_posting_queue", service.load_posting_queue)
    monkeypatch.setattr(automation, "with_queue_claim_lock", lambda callback: callback())
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    recovered(item, ORIGIN + 300, service.current_fingerprint(item))
    policy.queue_retry(item, ORIGIN + 300)
    monkeypatch.setattr(service.time, "time", lambda: ORIGIN + 601)
    with pytest.raises(RuntimeError, match="Network recovery"):
        automation.claim_queue_execution("recovery")
    monkeypatch.setattr(service.time, "time", lambda: ORIGIN + 301)
    monkeypatch.setattr(service, "profile_network", lambda pid: {"proxy_host": "changed.invalid", "proxy_port": 1080})
    with pytest.raises(RuntimeError, match="Network recovery"):
        automation.claim_queue_execution("recovery")
    assert not item.get("scheduler_lease")


def test_cancel_stops_recovery_without_erasing_unknown_outcome(monkeypatch):
    item = enroll(execution("uncertain"))
    queue = setup_service(monkeypatch, item)
    result = asyncio.run(service.cancel_network_recovery("recovery"))
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    assert result["success"]
    assert item["network_recovery"]["state"] == "cancelled"
    assert item["status"] == "uncertain"
    assert item["error"] == "reel_publish_timeout"
    assert service.maintain_and_claim_check() is None


@pytest.mark.parametrize("resolution", ["published", "not_published"])
def test_legacy_failed_timeout_can_be_reviewed(monkeypatch, resolution):
    from backend.routers import queue as router
    queue = setup_service(monkeypatch, enroll(execution("failed_after_publish")))
    monkeypatch.setattr(router, "load_posting_queue", service.load_posting_queue)
    monkeypatch.setattr(router, "save_posting_queue", service.save_posting_queue)
    router.resolve_uncertain("recovery", {"resolution": resolution})
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    assert item["review_status"] == f"resolved_{resolution}"


def test_new_upload_waits_while_its_proxy_is_being_measured(monkeypatch):
    item = enroll()
    queue = setup_service(monkeypatch, item)
    assert service.maintain_and_claim_check()
    other = {"profile_id": "profile_2"}
    assert service.proxy_measurement_active(queue, other, ORIGIN + 300)
    assert not service.proxy_measurement_active(queue, other, ORIGIN + 421)


def test_old_timeout_is_not_retroactively_enrolled():
    item = execution()
    policy.refresh_recovery(item, "reel", ORIGIN + policy.WINDOW_SECONDS)
    assert "network_recovery" not in item


def test_cancel_queued_retry_makes_an_already_dispatched_claim_fail(monkeypatch):
    from backend.services import automation_service as automation
    item = enroll()
    recovered(item)
    policy.queue_retry(item, ORIGIN + 330)
    queue = setup_service(monkeypatch, item)
    monkeypatch.setattr(automation, "load_posting_queue", service.load_posting_queue)
    monkeypatch.setattr(automation, "with_queue_claim_lock", lambda callback: callback())
    asyncio.run(service.cancel_network_recovery("recovery"))
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    assert item["status"] == "cancelled"
    with pytest.raises(RuntimeError, match="Execution was cancelled"):
        automation.claim_queue_execution("recovery")


def test_cancel_checks_does_not_interrupt_an_active_publish(monkeypatch):
    item = enroll()
    item.update(status="running", stage="publish_clicked")
    item["network_recovery"]["state"] = "retrying"
    queue = setup_service(monkeypatch, item)
    with pytest.raises(RuntimeError, match="active attempt"):
        asyncio.run(service.cancel_network_recovery("recovery"))
    assert queue["daily_batches"][0]["posts"][0]["executions"][0]["network_recovery"]["state"] == "retrying"


def test_preparation_claim_does_not_consume_publishing_retry(monkeypatch):
    from backend.services import automation_service as automation
    from backend.services.queue_scheduler import DEFAULT_SCHEDULER_CONFIG
    item = enroll(execution(preparation_mode="brief"))
    queue = setup_service(monkeypatch, item)
    item = queue["daily_batches"][0]["posts"][0]["executions"][0]
    recovered(item, ORIGIN + 300, service.current_fingerprint(item))
    policy.queue_retry(item, ORIGIN + 300)
    monkeypatch.setattr(automation, "load_posting_queue", service.load_posting_queue)
    monkeypatch.setattr(automation, "save_posting_queue", service.save_posting_queue)
    monkeypatch.setattr(automation, "with_queue_claim_lock", lambda callback: callback())
    monkeypatch.setattr(automation, "get_scheduler_config", lambda: DEFAULT_SCHEDULER_CONFIG)
    monkeypatch.setattr(automation, "active_in_memory_scheduler_leases", lambda: [])
    monkeypatch.setattr(automation.lifecycle, "acquire", Mock())
    context = automation.lifecycle.OWNER_TOKEN.set("owner")
    try:
        result = automation.claim_queue_execution("recovery", "preparer")
        assert result["targetExec"]["network_recovery"]["retry_count"] == 0
        assert result["targetExec"]["network_recovery"]["state"] == "ready"
    finally:
        automation.lifecycle.OWNER_TOKEN.reset(context)


@pytest.mark.parametrize("status", ["failed_before_publish", "skipped_unauthenticated"])
def test_failed_preparation_stops_recovery_instead_of_looping(monkeypatch, status):
    item = enroll(execution(preparation_mode="brief"))
    recovered(item)
    policy.queue_retry(item, ORIGIN + 330)
    item.update(status=status, stage="failed_before_publish", error="login_required")
    policy.refresh_recovery(item, "reel", ORIGIN + 340)
    assert item["network_recovery"]["state"] == "needs_review"
    assert not policy.retry_admitted(item, ORIGIN + 340)


def test_confirmation_is_not_starved_by_many_overdue_first_checks(monkeypatch):
    queue = setup_service(monkeypatch, enroll())
    other = enroll(execution())
    other["execution_id"] = "confirmation"
    policy.apply_measurement(other, sample(), service.current_fingerprint(other), ORIGIN + 300)
    queue["daily_batches"][0]["posts"][0]["executions"].append(other)
    monkeypatch.setattr(service.time, "time", lambda: ORIGIN + 330)
    assert service.maintain_and_claim_check()[0] == "confirmation"
