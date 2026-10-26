"""Persisted policy for bounded local Reel timeout recovery (no network I/O)."""
from copy import deepcopy
from datetime import datetime, timezone
import math

WINDOW_SECONDS = 24 * 3600
FRESH_SECONDS = 5 * 60
CONFIRM_SECONDS = 30
MAX_RETRIES = 2
MIN_MBPS = 1.0
TIMEOUT_CODES = {
    "reel_publish_timeout", "reel_publish_loading_timeout",
    "reel_publish_button_timeout", "reel_composer_timeout",
}
ACTIVE_STATES = {"waiting_network", "confirming_network", "waiting_review", "ready", "retrying"}
FAILURE_STATUSES = {"failed", "failed_before_publish", "failed_after_publish", "uncertain", "needs_review"}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else 0.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def next_check(recovery, now):
    origin = timestamp(recovery["started_at"])
    offsets = [300, 900, 1800, *range(3600, WINDOW_SECONDS, 3600)]
    return iso(next((origin + offset for offset in offsets if origin + offset > now),
                    timestamp(recovery["deadline_at"])))


def safe_to_retry(execution):
    from backend.services.queue_safety import has_publication_evidence
    # Only a definite pre-click failure or a reviewed outcome permits resubmission.
    return (execution.get("status") in {"failed", "failed_before_publish", "pending", "ready"}
            and (execution.get("status") != "failed" or execution.get("stage") in {
                "preparing", "ready", "composing", "ready_to_publish", "failed_before_publish",
            })
            and not has_publication_evidence(execution))


def holds_profile(execution):
    recovery = execution.get("network_recovery") or {}
    if (recovery.get("state") not in ACTIVE_STATES
            and execution.get("review_status") == "resolved_not_published" and safe_to_retry(execution)):
        return False
    return bool(recovery and recovery.get("state") not in {"cancelled", "completed"}
                and execution.get("status") not in {"published", "completed", "cancelled"})


def stop_recovery(execution, state):
    recovery = execution.get("network_recovery")
    if recovery:
        recovery.update(state=state, next_check_at=None, probe_token=None, probe_expires_at=None)


def refresh_recovery(execution, post_type, now):
    """Enroll existing/new timeouts and maintain their original deadline."""
    if post_type != "reel" or execution.get("cloud_attempt_id"):
        return
    recovery = execution.get("network_recovery")
    if not recovery:
        origin = timestamp(execution.get("ended_at"))
        if (execution.get("status") not in FAILURE_STATUSES
                or execution.get("error") not in TIMEOUT_CODES or not origin
                or now >= origin + WINDOW_SECONDS
                or execution.get("cancel_requested_at")):
            return
        recovery = execution["network_recovery"] = {
            "state": "waiting_network", "started_at": iso(origin),
            "deadline_at": iso(origin + WINDOW_SECONDS), "next_check_at": iso(origin + 300),
            "retry_count": 0, "check_count": 0, "attempts": [],
            "original_error": execution["error"], "pass_count": 0,
        }
    if execution.get("cancel_requested_at") or execution.get("status") == "cancelled":
        stop_recovery(execution, "cancelled")
        return
    if execution.get("status") in {"published", "completed"}:
        stop_recovery(execution, "completed")
        return
    if recovery["state"] not in ACTIVE_STATES:
        return
    # Do not stop an in-flight publishing attempt at its recovery deadline.
    if recovery["state"] == "retrying" and execution.get("status") in {"running", "preparing"}:
        return
    if now >= timestamp(recovery["deadline_at"]):
        stop_recovery(execution, "expired")
        if execution.get("status") in {"pending", "ready"}:
            execution.update(status="failed_before_publish", stage="failed_before_publish")
        return
    if recovery["state"] == "retrying" and execution.get("status") in FAILURE_STATUSES:
        if recovery["retry_count"] >= MAX_RETRIES:
            stop_recovery(execution, "retry_limit")
        elif execution.get("error") not in TIMEOUT_CODES:
            stop_recovery(execution, "needs_review")
        else:
            recovery.update(state="waiting_network", pass_count=0, healthy_at=None,
                            next_check_at=next_check(recovery, now))
    elif (recovery["state"] == "ready" and recovery.get("attempts")
          and (execution.get("status") in FAILURE_STATUSES
               or execution.get("status", "").startswith("skipped_"))):
        # A fresh preparation failure is not another network timeout. Do not
        # create an unbounded preparer retry loop without a publishing claim.
        stop_recovery(execution, "needs_review")
    elif recovery["state"] == "ready" and not healthy(recovery, now):
        recovery.update(state="waiting_network", pass_count=0, next_check_at=iso(now))
    elif (recovery["state"] == "waiting_review" and safe_to_retry(execution)
          and not healthy(recovery, now)):
        recovery.update(state="waiting_network", pass_count=0, next_check_at=iso(now))


def healthy(recovery, now):
    measured = timestamp(recovery.get("healthy_at"))
    return bool(measured and 0 <= now - measured <= FRESH_SECONDS and recovery.get("pass_count", 0) >= 2)


def measurement_passes(sample):
    for direction in ("upload", "download"):
        result = sample.get(direction) or {}
        rate = result.get("mbps")
        if (not result.get("success") or not isinstance(rate, (int, float))
                or not math.isfinite(rate) or rate <= MIN_MBPS):
            return False
    return bool((sample.get("facebook") or {}).get("success"))


def apply_measurement(execution, sample, fingerprint, now):
    recovery = execution["network_recovery"]
    recovery.update(last_measurement=sample, last_checked_at=iso(now),
                    check_count=recovery["check_count"] + 1,
                    probe_token=None, probe_expires_at=None)
    previous = timestamp(recovery.get("first_pass_at"))
    same_proxy = recovery.get("proxy_fingerprint") == fingerprint
    recovery["proxy_fingerprint"] = fingerprint
    if not measurement_passes(sample):
        recovery.update(state="waiting_network", pass_count=0, healthy_at=None,
                        first_pass_at=None, next_check_at=next_check(recovery, now))
        return
    if (same_proxy and recovery.get("pass_count") == 1
            and CONFIRM_SECONDS <= now - previous <= FRESH_SECONDS):
        recovery.update(pass_count=2, healthy_at=iso(now),
                        state="ready" if safe_to_retry(execution) else "waiting_review",
                        next_check_at=next_check(recovery, now))
    else:
        recovery.update(state="confirming_network", pass_count=1, first_pass_at=iso(now),
                        healthy_at=None, next_check_at=iso(now + CONFIRM_SECONDS))


def queue_retry(execution, now):
    """Archive the prior attempt before resetting its per-attempt publication proof."""
    recovery = execution["network_recovery"]
    if (not safe_to_retry(execution) or not healthy(recovery, now)
            or now >= timestamp(recovery["deadline_at"]) or recovery["retry_count"] >= MAX_RETRIES):
        return False
    if execution.get("status") not in {"pending", "ready"}:
        recovery["attempts"].append(deepcopy({
            key: value for key, value in execution.items() if key != "network_recovery"
        }))
        execution.update(status="pending", stage="pending", error=None, stage_history=[{
            "stage": "pending", "timestamp": iso(now), "reason": "network_recovery_queued",
        }])
        # Preparation handoffs belong to the previous attempt and may be stopped.
        if execution.get("preparation_mode", "off") != "off":
            execution["preparation_status"] = "pending"
        execution.pop("review_status", None)
        execution.pop("reviewed_at", None)
        execution.pop("last_active_stage", None)
    recovery.update(state="ready", next_check_at=None)
    return True


def retry_admitted(execution, now):
    recovery = execution.get("network_recovery")
    if not recovery or recovery.get("state") in {"cancelled", "completed"}:
        return True
    return (recovery.get("state") == "ready" and healthy(recovery, now)
            and now < timestamp(recovery["deadline_at"]) and recovery["retry_count"] < MAX_RETRIES
            and safe_to_retry(execution))


def reviewable(execution):
    recovery = execution.get("network_recovery") or {}
    return (execution.get("status") in FAILURE_STATUSES and bool(recovery)
            and recovery.get("state") not in {"cancelled", "completed"})
