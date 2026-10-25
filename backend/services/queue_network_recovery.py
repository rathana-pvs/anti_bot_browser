"""Background proxy checks; publishing remains owned by the ordinary scheduler."""
import asyncio
from copy import deepcopy
import hashlib
import json
import time
import uuid

from backend.config import PROFILES_DIR
from backend.services import container_lifecycle as lifecycle
from backend.services import network_recovery as policy
from backend.services.proxy_service import check_proxy_speed, check_facebook_response
from backend.services.queue_service import (
    load_posting_queue, save_posting_queue, find_queue_execution, with_queue_claim_lock,
)


def profile_network(profile_id):
    directory = (PROFILES_DIR / str(profile_id)).resolve()
    if directory.parent != PROFILES_DIR.resolve():
        raise ValueError("Invalid recovery profile")
    network = json.loads((directory / "config.json").read_text(encoding="utf-8")).get("network") or {}
    if network.get("mode") == "direct" or not network.get("proxy_host") or not network.get("proxy_port"):
        raise ValueError("Recovery requires an assigned proxy")
    return network


def network_fingerprint(network):
    # Persist only a digest; credentials never enter the queue or UI.
    keys = ("proxy_type", "proxy_host", "proxy_port", "proxy_user", "proxy_pass")
    return hashlib.sha256(json.dumps({key: network.get(key) for key in keys}, sort_keys=True).encode()).hexdigest()


def current_fingerprint(execution):
    try:
        return network_fingerprint(profile_network(execution["profile_id"]))
    except (OSError, ValueError, KeyError):
        return None


def proxy_is_busy(queue, fingerprint, profile_id):
    from backend.services.automation_service import active_automation_tasks, is_task_process_active
    active = [record for record in active_automation_tasks.values() if is_task_process_active(record)]
    active.extend({"profile_id": pid} for pid in lifecycle.snapshot())
    active.extend(execution for batch in queue.get("daily_batches", [])
                  for post in batch.get("posts", []) for execution in post.get("executions", [])
                  if execution.get("status") in {"running", "preparing"})
    for execution in active:
        if execution.get("profile_id") == profile_id:
            return True
        if fingerprint and current_fingerprint(execution) == fingerprint:
            return True
    return False


def proxy_measurement_active(queue, execution, now):
    """Reserve a proxy during a test without consuming a publishing slot."""
    fingerprint = None
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for other in post.get("executions", []):
                recovery = other.get("network_recovery") or {}
                if not recovery.get("probe_token") or policy.timestamp(recovery.get("probe_expires_at")) <= now:
                    continue
                if other.get("profile_id") == execution.get("profile_id"):
                    return True
                fingerprint = fingerprint or current_fingerprint(execution)
                if fingerprint and current_fingerprint(other) == fingerprint:
                    return True
    return False


def _measure(network):
    # Sequential transfers avoid measuring competition between our own tests.
    try:
        # 1 MB finishes in about 8 seconds at the 1 Mbps threshold, within the
        # existing 20-second transfer budget. The manual 5 MB test is too large.
        return {"download": check_proxy_speed(network, "download", sample_bytes=1_000_000),
                "upload": check_proxy_speed(network, "upload", sample_bytes=1_000_000),
                "facebook": check_facebook_response(network)}
    except (OSError, ValueError):
        return {"error": "Proxy test unavailable"}


def maintain_and_claim_check():
    now = time.time()
    queue = load_posting_queue()
    original = deepcopy(queue)
    candidates = []
    probing = False
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                policy.refresh_recovery(execution, post.get("type"), now)
                recovery = execution.get("network_recovery")
                if not recovery or execution.get("cloud_attempt_id"):
                    continue
                if recovery["state"] not in policy.ACTIVE_STATES or recovery["state"] == "retrying":
                    continue
                if execution.get("status") in {"running", "preparing"}:
                    continue
                fingerprint = current_fingerprint(execution)
                if recovery.get("proxy_fingerprint") != fingerprint:
                    recovery.update(pass_count=0, healthy_at=None)
                    if recovery["state"] in {"ready", "confirming_network"}:
                        recovery.update(state="waiting_network", next_check_at=policy.iso(now))
                if policy.healthy(recovery, now) and policy.safe_to_retry(execution):
                    policy.queue_retry(execution, now)
                if recovery["state"] == "ready":
                    continue
                if recovery.get("probe_token") and policy.timestamp(recovery.get("probe_expires_at")) > now:
                    probing = True
                elif policy.timestamp(recovery.get("next_check_at")) <= now:
                    candidates.append((execution, fingerprint))
    claim = None
    if not probing and not lifecycle.UPDATE_MAINTENANCE:
        candidates.sort(key=lambda item: (
            item[0]["network_recovery"]["state"] != "confirming_network",
            policy.timestamp(item[0]["network_recovery"].get("next_check_at")),
        ))
        for execution, fingerprint in candidates:
            if execution.get("container_cleanup_in_progress") or proxy_is_busy(queue, fingerprint, execution["profile_id"]):
                continue
            recovery = execution["network_recovery"]
            token = str(uuid.uuid4())
            recovery.update(probe_token=token, probe_expires_at=policy.iso(now + 120))
            claim = (execution["execution_id"], token, fingerprint)
            break
    if queue != original and not save_posting_queue(queue):
        raise OSError("Could not persist network recovery")
    return claim


async def check_network_recovery():
    """One bounded probe per tick; persisted confirmation replaces a blocking sleep."""
    claim = with_queue_claim_lock(maintain_and_claim_check)
    if not claim:
        return
    execution_id, token, fingerprint = claim
    match = find_queue_execution(load_posting_queue(), execution_id)
    if not match:
        return
    try:
        network = profile_network(match["execution"]["profile_id"])
        fingerprint = network_fingerprint(network)
        sample = await asyncio.to_thread(_measure, network)
    except (OSError, ValueError):
        sample = {"error": "Assigned proxy unavailable"}

    def commit():
        queue = load_posting_queue()
        match = find_queue_execution(queue, execution_id)
        if not match:
            return
        execution = match["execution"]
        recovery = execution.get("network_recovery") or {}
        if recovery.get("probe_token") != token or execution.get("cloud_attempt_id"):
            return
        now = time.time()
        policy.refresh_recovery(execution, match["post"].get("type"), now)
        if recovery.get("probe_token") == token:
            if current_fingerprint(execution) != fingerprint:
                sample_to_save = {"error": "Proxy changed during measurement"}
            else:
                sample_to_save = sample
            policy.apply_measurement(execution, sample_to_save, fingerprint, now)
            if recovery["state"] == "ready":
                policy.queue_retry(execution, now)
        if not save_posting_queue(queue):
            raise OSError("Could not persist network measurement")
    with_queue_claim_lock(commit)


async def cancel_network_recovery(execution_id):
    def cancel():
        queue = load_posting_queue()
        match = find_queue_execution(queue, execution_id)
        if not match:
            raise KeyError("Execution not found")
        execution = match["execution"]
        if execution.get("cloud_attempt_id") or not execution.get("network_recovery"):
            raise ValueError("This execution has no local network recovery")
        if execution.get("status") in {"running", "preparing"}:
            raise RuntimeError("Wait for the active attempt to finish before cancelling recovery")
        policy.stop_recovery(execution, "cancelled")
        now = policy.iso(time.time())
        execution["cancel_requested_at"] = now
        if execution.get("status") in {"pending", "ready"}:
            execution.update(status="cancelled", stage="cancelled", ended_at=now)
            execution.setdefault("stage_history", []).append({
                "stage": "cancelled", "timestamp": now, "reason": "network_recovery_cancelled",
            })
        if not save_posting_queue(queue):
            raise OSError("Could not persist recovery cancellation")
        return execution
    execution = with_queue_claim_lock(cancel)
    if execution.get("container_owner_token"):
        from backend.services.automation_service import _stop_container_with_retries
        await lifecycle.release(execution["profile_id"], execution["container_owner_token"], _stop_container_with_retries)
    return {"success": True, "message": "Network recovery cancelled. Publication outcome was preserved."}
