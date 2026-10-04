import asyncio
import json
import os
import re
import subprocess
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import (
    ROOT_DIR,
    PROFILES_DIR,
    SCRIPTS_DIR,
    BRAINS_DIR,
    BRAIN_CLI,
    AUTOMATION_PYTHON,
    MANAGER_INSTANCE_ID,
)
from backend.services.docker_service import (
    get_container_status,
    count_running_profile_containers,
    claim_container_start_admission,
    automation_worker_env,
    get_scheduler_config,
    CONTAINER_START_SPACING_MS,
)
from backend.services.queue_scheduler import (
    is_lease_active,
    scheduler_snapshot,
    can_acquire_scheduler_slot,
    claim_execution_lease,
    refresh_execution_lease,
    release_execution_lease,
    worker_silence_timeout_ms,
    find_stale_running_executions,
)
from backend.services.queue_safety import (
    apply_automation_permalink_result,
    apply_comment_retry_result,
    apply_warming_result,
    can_retry_first_comment,
)
from backend.services.profile_pipeline import (
    ordered_due_executions,
    count_buffered_preparations,
    batch_iteration_availability,
)
from backend.services.queue_service import (
    load_posting_queue,
    save_posting_queue,
    find_queue_execution,
    with_queue_claim_lock,
    persist_queue_execution_stage,
    apply_interrupted_execution_recovery,
)

from backend.services import container_lifecycle as lifecycle

# Brain CLI wrapper
def run_brain_cli(args: list[str]) -> dict:
    try:
        cmd = [str(AUTOMATION_PYTHON), str(BRAIN_CLI), "--root", str(BRAINS_DIR), *args]
        out = subprocess.check_output(cmd, cwd=str(ROOT_DIR), text=True, stderr=subprocess.PIPE, timeout=10).strip()
        return json.loads(out or "{}")
    except subprocess.CalledProcessError as err:
        stdout = (err.stdout or "").strip()
        if stdout:
            try:
                parsed = json.loads(stdout)
                if parsed.get("error"):
                    raise RuntimeError(parsed["error"])
            except json.JSONDecodeError:
                pass
        stderr = (err.stderr or "").strip()
        raise RuntimeError(stderr or str(err))

def resolve_brain_pin(brain_id: str, requested_version: str | None = None) -> dict:
    listing = run_brain_cli(["list"])
    family = next((b for b in listing.get("brains", []) if b.get("id") == brain_id), None)
    if not family:
        raise RuntimeError(f"Brain {brain_id} is not installed")
    directory = requested_version or family.get("active_version") or "bundled_default"
    version = next((v for v in family.get("versions", []) if v.get("directory") == directory), None)
    if not version or version.get("status") != "valid":
        raise RuntimeError(f"Brain {brain_id} version {directory} is not valid")
    return {
        "id": brain_id,
        "directory": directory,
        "version": version.get("version"),
        "digest": version.get("digest"),
        "brain_api_version": version.get("brain_api_version"),
    }

# In-memory tasks registry
active_automation_tasks: dict[str, dict] = {}
PREPARATION_HANDOFF_SECONDS = 120.0
CONTAINER_STOP_RETRY_DELAYS = (0.0, 2.0, 5.0)

def is_task_process_active(record: dict | None) -> bool:
    if not record or record.get("status") != "running":
        return False
    if record.get("lifecycle_active") is True:
        return True
    proc = record.get("process")
    return bool(proc and proc.returncode is None)

def active_in_memory_scheduler_leases() -> list[dict]:
    leases = []
    for record in active_automation_tasks.values():
        if is_task_process_active(record) and is_lease_active(record.get("scheduler_lease")):
            leases.append(record["scheduler_lease"])
    return leases

def current_scheduler_snapshot(queue: dict | None = None) -> dict:
    if queue is None:
        queue = load_posting_queue()
    return scheduler_snapshot(queue, active_in_memory_scheduler_leases(), get_scheduler_config())

def serialize_task_state(task: dict | None) -> dict:
    if not task:
        return {"status": "idle", "logs": []}
    return {k: v for k, v in task.items() if k != "process"}

def normalize_stored_log(line: str, profile_id: str) -> dict:
    val = str(line or "").strip()
    match = re.match(r"^\[(.*?)\]\s*\[(.*?)\]\s*\[(.*?)\]\s*(.*)$", val)
    if match:
        return {"timestamp": match.group(1), "profile_id": match.group(2), "level": match.group(3), "message": match.group(4)}
    is_stderr = val.startswith("[STDERR]")
    return {
        "timestamp": "",
        "profile_id": profile_id,
        "level": "WARN" if is_stderr else "INFO",
        "message": val[8:].strip() if is_stderr else val,
    }

def latest_queue_task_state(profile_id: str) -> dict | None:
    queue = load_posting_queue()
    candidates = []
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.get("profile_id") == profile_id:
                    task = "warming" if post.get("type") == "warming" else ("reel" if post.get("type") == "reel" else "post")
                    candidates.append({"execution": execution, "task": task})

    if not candidates:
        return None

    def sort_key(item):
        ex = item["execution"]
        ts = ex.get("started_at") or ex.get("published_at") or ex.get("scheduled_at") or ""
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    candidates.sort(key=sort_key, reverse=True)
    target = candidates[0]
    execution = target["execution"]
    task = target["task"]

    active = active_automation_tasks.get(profile_id)
    is_proc_active = is_task_process_active(active)
    raw_status = execution.get("status", "idle")
    if raw_status == "running" and not is_proc_active:
        raw_status = "stopped"

    status_map = {
        "pending": "idle",
        "ready": "idle",
        "preparing": "running",
        "running": "running",
        "published": "completed",
        "completed": "completed",
        "failed": "failed",
        "failed_before_publish": "failed",
        "failed_after_publish": "failed",
        "uncertain": "uncertain",
        "needs_review": "uncertain",
        "stopped": "stopped",
    }

    logs = [normalize_stored_log(l, profile_id) for l in (execution.get("logs") or [])[-300:]]

    return {
        "profile_id": profile_id,
        "task": task,
        "status": status_map.get(raw_status, "idle"),
        "started_at": execution.get("started_at") or execution.get("scheduled_at"),
        "ended_at": execution.get("ended_at") or execution.get("published_at"),
        "logs": logs,
        "error": execution.get("error"),
        "evidence_dir": execution.get("evidence_dir"),
        "execution_id": execution.get("execution_id"),
        "source": "queue",
    }

# Container readiness and lifecycle helpers
async def run_profile_lifecycle_action(profile_id: str, action: str, timeout_sec: float = 90.0) -> str:
    script_path = SCRIPTS_DIR / "run_profile.sh"
    proc = await asyncio.create_subprocess_exec(
        "bash", str(script_path), profile_id, action,
        cwd=str(ROOT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        if proc.returncode != 0:
            err_msg = stderr_b.decode("utf-8", errors="replace").strip() or stdout_b.decode("utf-8", errors="replace").strip()
            raise RuntimeError(err_msg)
        return stdout_b.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise TimeoutError(f"Lifecycle action '{action}' on {profile_id} timed out")
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise

async def ensure_profile_container_ready(profile_id: str) -> dict:
    lifecycle.mark_used(profile_id, lifecycle.OWNER_TOKEN.get())
    original_status = get_container_status(profile_id)
    if original_status == "paused":
        await run_profile_lifecycle_action(profile_id, "unpause")
    elif original_status != "running":
        await run_profile_lifecycle_action(profile_id, "start")

    deadline = time.time() + 45.0
    container_name = f"isolated_{profile_id}"
    while time.time() < deadline:
        if get_container_status(profile_id) != "running":
            await asyncio.sleep(0.75)
            continue
        try:
            chk = await asyncio.create_subprocess_exec(
                "docker", "exec", "-u", "chromeuser", "-e", "DISPLAY=:99",
                container_name, "sh", "-lc", "xdotool search --onlyvisible --class google-chrome | head -1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout_b, _ = await asyncio.wait_for(chk.communicate(), timeout=3.0)
            window_id = stdout_b.decode("utf-8", errors="replace").strip()
            if window_id:
                return {"started": original_status != "running", "status": "running"}
        except asyncio.CancelledError:
            if 'chk' in locals() and chk.returncode is None:
                chk.kill()
                await chk.wait()
            raise
        except Exception:
            if 'chk' in locals() and chk.returncode is None:
                chk.kill()
                await chk.wait()
        await asyncio.sleep(0.75)
    raise RuntimeError(f"Container {container_name} started but Chrome was not ready within 45 seconds")

async def _stop_container_with_retries(profile_id: str) -> dict:
    last_error = None
    for attempt, delay in enumerate(CONTAINER_STOP_RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            if get_container_status(profile_id, strict=True) == "stopped":
                return {"stopped": True, "already_stopped": True, "attempts": attempt}
            await run_profile_lifecycle_action(profile_id, "stop")
            if get_container_status(profile_id, strict=True) != "stopped":
                raise RuntimeError("Container is still running after Stop")
            return {
                "stopped": True,
                "already_stopped": False,
                "attempts": attempt,
            }
        except Exception as error:
            last_error = error
    raise RuntimeError(
        f"Could not stop container for {profile_id} after {len(CONTAINER_STOP_RETRY_DELAYS)} attempts: {last_error}"
    )


async def stop_profile_container_after_report(profile_id: str) -> dict:
    return await lifecycle.release(profile_id, lifecycle.OWNER_TOKEN.get(), _stop_container_with_retries)


async def _terminate_owned_worker(profile_id, token):
    record = active_automation_tasks.get(profile_id)
    if not record or record.get("container_owner_token") != token:
        return
    proc = record.get("process")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except ProcessLookupError:
            pass
    if record.get("status") == "running":
        record["status"] = "stale"
        record["error"] = "Container ownership expired or automation was interrupted"
    record["lifecycle_active"] = False


async def cleanup_orphaned_automation_containers(force: bool = False) -> dict:
    await lifecycle.recover(_stop_container_with_retries, _terminate_owned_worker, force=force)
    return {"remaining": len(lifecycle.snapshot())}


# Task Runner implementations
async def launch_manual_automation(
    profile_id: str,
    task: str,
    scrolls: int | None = None,
    caption: str | None = None,
    comment_link: str | None = None,
    media: str | None = None,
    brain_version: str | None = None,
) -> dict:
    if task not in ("warming", "post", "reel", "comment"):
        raise ValueError(f"Invalid task '{task}'. Allowed: warming, post, reel, comment")

    existing = active_automation_tasks.get(profile_id)
    if is_task_process_active(existing):
        raise RuntimeError(f"Automation is already running for profile '{profile_id}'")

    profile_dir = PROFILES_DIR / profile_id
    if not profile_dir.exists():
        raise FileNotFoundError(f"Profile '{profile_id}' not found")

    slot_kind = "preparer" if task == "warming" else "publisher"
    scheduler_cfg = get_scheduler_config()
    slot_check = can_acquire_scheduler_slot(
        load_posting_queue(),
        active_in_memory_scheduler_leases(),
        slot_kind,
        profile_id,
        scheduler_cfg,
    )
    if not slot_check["allowed"]:
        return {
            "error": f"Scheduler capacity unavailable: {slot_check['reason']}",
            "scheduler": slot_check["snapshot"],
            "status_code": 409,
        }

    now_ms = time.time() * 1000
    now_iso = datetime.now(timezone.utc).isoformat()
    manual_lease = {
        "lease_id": str(uuid.uuid4()),
        "owner_id": MANAGER_INSTANCE_ID,
        "kind": slot_kind,
        "profile_id": profile_id,
        "claimed_at": now_iso,
        "heartbeat_at": now_iso,
        "expires_at": datetime.fromtimestamp((now_ms + scheduler_cfg["lease_ttl_ms"]) / 1000, tz=timezone.utc).isoformat(),
    }

    runner_script = ROOT_DIR / "automation" / "runner.py"
    args = ["-u", str(runner_script), "--profile", profile_id, "--task", task]

    brain_id = "facebook_post" if task == "post" else ("facebook_reel" if task == "reel" else None)
    brain_pin = resolve_brain_pin(brain_id, brain_version) if brain_id else None
    if brain_pin:
        args.extend(["--brain-version", brain_pin["directory"]])
    if scrolls is not None:
        args.extend(["--scrolls", str(scrolls)])
    if caption:
        args.extend(["--caption", caption])
    if comment_link:
        args.extend(["--comment-link", comment_link])
    if media:
        args.extend(["--media", media])

    task_record = {
        "profile_id": profile_id,
        "task": task,
        "status": "running",
        "started_at": now_iso,
        "ended_at": None,
        "logs": [],
        "result": None,
        "error": None,
        "process": None,
        "scheduler_lease": manual_lease,
        "brain": brain_pin,
        "last_activity_at": now_iso,
    }
    token = lifecycle.acquire(profile_id, "manual:" + manual_lease["lease_id"])
    task_record["container_owner_token"] = token
    active_automation_tasks[profile_id] = task_record
    context_token = lifecycle.OWNER_TOKEN.set(token)
    try:
        asyncio.create_task(_run_manual_subprocess(profile_id, args, task_record))
    finally:
        lifecycle.OWNER_TOKEN.reset(context_token)
    return serialize_task_state(task_record)

async def _run_manual_subprocess(profile_id: str, args: list[str], task_record: dict):
    try:
        lifecycle.mark_used(profile_id, lifecycle.OWNER_TOKEN.get())
        proc = await asyncio.create_subprocess_exec(
            str(AUTOMATION_PYTHON), *args,
            cwd=str(ROOT_DIR),
            env=automation_worker_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task_record["process"] = proc

        async def read_stdout():
            while True:
                line_b = await proc.stdout.readline()
                if not line_b:
                    break
                line = line_b.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                        if isinstance(parsed, dict) and ("success" in parsed or "profile_id" in parsed):
                            task_record["result"] = parsed
                            continue
                    except Exception:
                        pass
                task_record["logs"].append(normalize_stored_log(line, profile_id))
                if len(task_record["logs"]) > 300:
                    task_record["logs"].pop(0)

        async def read_stderr():
            while True:
                line_b = await proc.stderr.readline()
                if not line_b:
                    break
                line = line_b.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
                task_record["logs"].append(normalize_stored_log(f"[STDERR] {line}", profile_id))
                if len(task_record["logs"]) > 300:
                    task_record["logs"].pop(0)

        await asyncio.gather(read_stdout(), read_stderr())
        code = await proc.wait()

        ended_at = datetime.now(timezone.utc).isoformat()
        task_record["ended_at"] = ended_at
        task_record["process"] = None
        task_record["last_scheduler_lease"] = task_record.get("scheduler_lease")
        task_record["scheduler_lease"] = None

        res = task_record.get("result") or {}
        res_status = res.get("status")
        if task_record["status"] == "stopped":
            task_record["logs"].append(normalize_stored_log(f"[{ended_at}] [{profile_id}] [INFO] Task stopped by user.", profile_id))
        elif res_status and res_status.startswith("skipped_"):
            task_record["status"] = res_status
            task_record["error"] = res.get("error")
        elif res_status in ("needs_review", "uncertain") or code == 2:
            task_record["status"] = "needs_review" if res_status == "needs_review" else "uncertain"
            task_record["error"] = res.get("error") or "Publication requires operator review or could not be visually confirmed"
        elif code == 0:
            task_record["status"] = "completed"
        else:
            task_record["status"] = "failed"
            task_record["error"] = res.get("error") or f"Task failed with exit code {code}"
    except Exception as err:
        task_record["ended_at"] = datetime.now(timezone.utc).isoformat()
        task_record["status"] = "failed"
        task_record["error"] = str(err)
        task_record["scheduler_lease"] = None
    finally:
        try:
            await _terminate_owned_worker(profile_id, task_record["container_owner_token"])
        except Exception as error:
            task_record["worker_cleanup_error"] = str(error)
        task_record["process"] = None
        try:
            task_record["container_cleanup"] = await stop_profile_container_after_report(profile_id)
            task_record["container_stopped_at"] = datetime.now(timezone.utc).isoformat()
            task_record["container_cleanup_error"] = None
        except Exception as cleanup_error:
            task_record["container_cleanup_error"] = str(cleanup_error)

def stop_automation_task(profile_id: str) -> dict:
    record = active_automation_tasks.get(profile_id)
    if not record or not is_task_process_active(record):
        raise RuntimeError(f"No active automation running for profile '{profile_id}'")

    record["status"] = "stopped"
    proc = record.get("process")
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass

        async def _kill_later():
            await asyncio.sleep(2.0)
            try:
                if proc.returncode is None:
                    proc.kill()
            except Exception:
                pass
        asyncio.create_task(_kill_later())

    return {"success": True, "message": f"Terminated automation for profile '{profile_id}'"}

# Queue Claim & Execution Functions
def claim_queue_execution(execution_id: str, kind: str = "publisher") -> dict:
    def _claim_fn():
        queue = load_posting_queue()
        match = find_queue_execution(queue, execution_id)
        if not match:
            raise RuntimeError("Execution not found")
        target_exec = match["execution"]
        target_post = match["post"]
        target_batch = match["batch"]

        standalone_warming = target_post.get("type") == "warming"
        prep_mode = target_exec.get("preparation_mode") or "off"

        if kind == "preparer" and not standalone_warming and (prep_mode == "off" or target_exec.get("preparation_status") != "pending"):
            err = RuntimeError("Execution does not require passive preparation")
            err.schedulerReason = "preparation_not_pending"
            raise err

        if kind == "publisher" and prep_mode != "off" and target_exec.get("preparation_status") != "ready":
            err = RuntimeError("Execution is not ready for the publisher slot")
            err.schedulerReason = "preparation_not_ready"
            raise err

        if target_exec.get("container_cleanup_in_progress"):
            err = RuntimeError("Container cleanup is in progress")
            err.schedulerReason = "container_cleanup_in_progress"
            raise err

        if kind == "publisher":
            iteration_check = batch_iteration_availability(
                target_batch,
                target_execution=target_exec,
            )
            if not iteration_check["allowed"]:
                err = RuntimeError(f"Batch iteration is not ready: {iteration_check['reason']}")
                err.schedulerReason = iteration_check["reason"]
                raise err

        lease_id = str(uuid.uuid4())
        claim = claim_execution_lease(
            queue, target_exec,
            lease_id=lease_id,
            owner_id=MANAGER_INSTANCE_ID,
            kind=kind,
            config=get_scheduler_config(),
            additional_leases=active_in_memory_scheduler_leases(),
        )
        if not claim["claimed"]:
            err = RuntimeError(f"Scheduler could not claim execution: {claim['reason']}")
            err.schedulerReason = claim["reason"]
            raise err

        token = lifecycle.OWNER_TOKEN.get()
        if not token:
            raise RuntimeError("Missing automation ownership context")
        lifecycle.acquire(target_exec["profile_id"], execution_id, token)
        now_iso = datetime.now(timezone.utc).isoformat()
        target_exec["status"] = "preparing" if (kind == "preparer" and not standalone_warming) else "running"
        target_exec["started_at"] = now_iso
        target_exec["ended_at"] = None
        target_exec["error"] = None
        target_exec["logs"] = []

        history = target_exec.get("stage_history")
        if not isinstance(history, list):
            history = []
            target_exec["stage_history"] = history

        stage_name = "warming" if standalone_warming else ("preparing" if kind == "preparer" else "running")
        target_exec["stage"] = stage_name
        history.append({
            "stage": stage_name,
            "timestamp": now_iso,
            "reason": f"{kind}_scheduler_lease_claimed",
            "lease_id": lease_id,
        })
        target_exec["stage_updated_at"] = now_iso
        target_exec["automation_container_owned"] = True
        target_exec["container_owner_schema"] = 1
        target_exec["container_owner_token"] = token
        target_exec["container_handoff_expires_at"] = None

        if not save_posting_queue(queue):
            raise RuntimeError("Failed to persist scheduler claim")
        return {"targetExec": target_exec, "targetPost": target_post, "leaseId": lease_id, "lease": claim["lease"]}

    return with_queue_claim_lock(_claim_fn)

async def _execute_queue_preparation_inner(execution_id: str):
    claim_res = claim_queue_execution(execution_id, "preparer")
    target_exec = claim_res["targetExec"]
    lease_id = claim_res["leaseId"]
    lease = claim_res["lease"]
    profile_id = target_exec["profile_id"]

    runner_script = ROOT_DIR / "automation" / "runner.py"
    args = [
        "-u", str(runner_script),
        "--profile", profile_id,
        "--task", "preparation",
        "--preparation-mode", target_exec.get("preparation_mode", "brief"),
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    task_record = {
        "profile_id": profile_id,
        "task": "preparation",
        "status": "running",
        "started_at": now_iso,
        "ended_at": None,
        "logs": [],
        "result": None,
        "current_stage": "preparing",
        "error": None,
        "process": None,
        "lifecycle_active": True,
        "queue_execution_id": execution_id,
        "scheduler_lease": lease,
        "last_activity_at": now_iso,
    }
    task_record["container_owner_token"] = lifecycle.OWNER_TOKEN.get()
    active_automation_tasks[profile_id] = task_record

    try:
        container_readiness = await ensure_profile_container_ready(profile_id)
        task_record["container_started_by_queue"] = container_readiness["started"]
        task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
        task_record["lifecycle_active"] = False
    except Exception as error:
        task_record["lifecycle_active"] = False
        task_record["status"] = "failed"
        task_record["error"] = str(error)
        task_record["scheduler_lease"] = None
        task_record["ended_at"] = datetime.now(timezone.utc).isoformat()

        queue = load_posting_queue()
        match = find_queue_execution(queue, execution_id)
        if match and match["execution"].get("scheduler_lease", {}).get("lease_id") == lease_id:
            match["execution"]["status"] = "failed_before_publish"
            match["execution"]["stage"] = "failed_before_publish"
            match["execution"]["preparation_status"] = "failed"
            match["execution"]["error"] = f"Container startup failed: {error}"
            match["execution"]["ended_at"] = task_record["ended_at"]
            release_execution_lease(match["execution"], lease_id)
            save_posting_queue(queue)

        try:
            await stop_profile_container_after_report(profile_id)
        except Exception:
            pass
        asyncio.create_task(_delayed_dispatch(100))
        return {"success": False, "status": "failed_before_publish", "error": str(error)}

    # Spawn process
    proc = await asyncio.create_subprocess_exec(
        str(AUTOMATION_PYTHON), *args,
        cwd=str(ROOT_DIR),
        env=automation_worker_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    task_record["process"] = proc

    async def read_out():
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and ("status" in parsed or "success" in parsed):
                        task_record["result"] = parsed
                        continue
                except Exception:
                    pass
            task_record["logs"].append(normalize_stored_log(line, profile_id))
            stage_match = re.match(r"^\[(.*?)\]\s*\[(.*?)\]\s*\[STAGE\]\s*Execution stage:\s*([a-z_]+)\s*$", line, re.IGNORECASE)
            if stage_match:
                task_record["current_stage"] = stage_match.group(3)
                persist_queue_execution_stage(execution_id, stage_match.group(3), stage_match.group(1), lease_id)

    async def read_err():
        while True:
            line_b = await proc.stderr.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            task_record["logs"].append(normalize_stored_log(f"[STDERR] {line}", profile_id))

    await asyncio.gather(read_out(), read_err())
    code = await proc.wait()

    ended_at = datetime.now(timezone.utc).isoformat()
    task_record["ended_at"] = ended_at
    task_record["process"] = None
    res = task_record.get("result") or {}
    reported_status = res.get("status")

    task_record["status"] = reported_status if reported_status and reported_status.startswith("skipped_") else ("completed" if code == 0 else "failed")

    updated_queue = load_posting_queue()
    match = find_queue_execution(updated_queue, execution_id)
    if match and match["execution"].get("scheduler_lease", {}).get("lease_id") == lease_id:
        ex = match["execution"]
        ex["ended_at"] = ended_at
        ex["preparation_telemetry"] = res.get("telemetry") or None

        if reported_status and reported_status.startswith("skipped_"):
            ex["status"] = reported_status
            ex["stage"] = reported_status
            ex["preparation_status"] = "skipped"
            ex["error"] = res.get("error") or None
        elif code == 0:
            ex["status"] = "ready"
            ex["stage"] = "ready"
            ex["preparation_status"] = "ready"
            ex["preparation_ended_at"] = ended_at
            ex["container_handoff_expires_at"] = datetime.fromtimestamp(
                time.time() + PREPARATION_HANDOFF_SECONDS,
                tz=timezone.utc,
            ).isoformat()
            ex["error"] = None
        else:
            ex["status"] = "failed_before_publish"
            ex["stage"] = "failed_before_publish"
            ex["preparation_status"] = "failed"
            ex["error"] = res.get("error") or f"Preparation exited with code {code}"

        release_execution_lease(ex, lease_id)
        save_posting_queue(updated_queue)

        if code != 0:
            try:
                await stop_profile_container_after_report(profile_id)
            except Exception:
                pass

        asyncio.create_task(_delayed_dispatch(100))
    return {"success": code == 0, "status": reported_status or "completed", "code": code}


async def _run_owned_queue(execution_id, action, *args, preparation=False):
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    profile_id = match["execution"].get("profile_id") if match else None
    token = uuid.uuid4().hex
    context_token = lifecycle.OWNER_TOKEN.set(token)
    hold = False
    try:
        result = await action(execution_id, *args)
        if preparation and result.get("success") and profile_id:
            prepared = find_queue_execution(load_posting_queue(), execution_id)
            if prepared and prepared["execution"].get("status") == "ready":
                hold = lifecycle.handoff(profile_id, token)
        return result
    finally:
        try:
            if profile_id and not hold:
                try:
                    await _terminate_owned_worker(profile_id, token)
                except Exception as error:
                    print(f"Worker termination failed for {profile_id}: {error}")
                try:
                    await lifecycle.release(profile_id, token, _stop_container_with_retries)
                except Exception as error:
                    print(f"Container cleanup failed for {profile_id}: {error}")
        finally:
            lifecycle.OWNER_TOKEN.reset(context_token)


async def execute_queue_preparation(execution_id: str):
    return await _run_owned_queue(execution_id, _execute_queue_preparation_inner, preparation=True)

async def _execute_queue_item_inner(execution_id: str, scheduler_kind: str = "publisher"):
    claim_res = claim_queue_execution(execution_id, scheduler_kind)
    target_exec = claim_res["targetExec"]
    target_post = claim_res["targetPost"]
    lease_id = claim_res["leaseId"]
    lease = claim_res["lease"]
    profile_id = target_exec["profile_id"]

    runner_script = ROOT_DIR / "automation" / "runner.py"
    task_type = "warming" if target_post.get("type") == "warming" else ("reel" if target_post.get("type") == "reel" else "post")
    args = ["-u", str(runner_script), "--profile", profile_id, "--task", task_type]

    brain_id = "facebook_post" if task_type == "post" else ("facebook_reel" if task_type == "reel" else None)
    brain_pin = resolve_brain_pin(brain_id) if brain_id else None
    if brain_pin:
        args.extend(["--brain-version", brain_pin["directory"]])
        pin_q = load_posting_queue()
        pin_m = find_queue_execution(pin_q, execution_id)
        if pin_m and pin_m["execution"].get("scheduler_lease", {}).get("lease_id") == lease_id:
            pin_m["execution"]["brain"] = brain_pin
            save_posting_queue(pin_q)

    if task_type == "warming":
        args.extend(["--scrolls", str(target_post.get("scrolls") or 4)])
    if target_exec.get("spun_caption"):
        args.extend(["--caption", target_exec["spun_caption"]])
    if target_post.get("first_comment"):
        args.extend(["--comment-link", target_post["first_comment"]])
    if target_post.get("media_file"):
        args.extend(["--media", target_post["media_file"]])

    now_iso = datetime.now(timezone.utc).isoformat()
    task_record = {
        "profile_id": profile_id,
        "task": task_type,
        "status": "running",
        "started_at": now_iso,
        "ended_at": None,
        "logs": [],
        "result": None,
        "current_stage": target_exec.get("stage", "running"),
        "queue_execution_id": execution_id,
        "scheduler_lease": lease,
        "brain": brain_pin,
        "last_activity_at": now_iso,
        "error": None,
        "process": None,
        "lifecycle_active": True,
    }
    task_record["container_owner_token"] = lifecycle.OWNER_TOKEN.get()
    active_automation_tasks[profile_id] = task_record

    try:
        container_readiness = await ensure_profile_container_ready(profile_id)
        task_record["container_started_by_queue"] = container_readiness["started"]
        task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
        task_record["lifecycle_active"] = False
    except Exception as error:
        task_record["lifecycle_active"] = False
        task_record["status"] = "failed"
        task_record["error"] = str(error)
        task_record["ended_at"] = datetime.now(timezone.utc).isoformat()

        queue = load_posting_queue()
        match = find_queue_execution(queue, execution_id)
        if match and match["execution"].get("scheduler_lease", {}).get("lease_id") == lease_id:
            match["execution"]["status"] = "failed_before_publish"
            match["execution"]["stage"] = "failed_before_publish"
            match["execution"]["error"] = f"Container startup failed: {error}"
            match["execution"]["ended_at"] = task_record["ended_at"]
            release_execution_lease(match["execution"], lease_id)
            save_posting_queue(queue)

        try:
            await stop_profile_container_after_report(profile_id)
        except Exception:
            pass
        asyncio.create_task(_delayed_dispatch(100))
        return {"success": False, "status": "failed_before_publish", "error": str(error)}

    proc = await asyncio.create_subprocess_exec(
        str(AUTOMATION_PYTHON), *args,
        cwd=str(ROOT_DIR),
        env=automation_worker_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    task_record["process"] = proc

    async def read_out():
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and ("status" in parsed or "success" in parsed):
                        task_record["result"] = parsed
                        continue
                except Exception:
                    pass
            task_record["logs"].append(normalize_stored_log(line, profile_id))
            stage_match = re.match(r"^\[(.*?)\]\s*\[(.*?)\]\s*\[STAGE\]\s*Execution stage:\s*([a-z_]+)\s*$", line, re.IGNORECASE)
            if stage_match:
                task_record["current_stage"] = stage_match.group(3)
                persist_queue_execution_stage(execution_id, stage_match.group(3), stage_match.group(1), lease_id)

    async def read_err():
        while True:
            line_b = await proc.stderr.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            task_record["logs"].append(normalize_stored_log(f"[STDERR] {line}", profile_id))

    await asyncio.gather(read_out(), read_err())
    code = await proc.wait()

    ended_at = datetime.now(timezone.utc).isoformat()
    task_record["ended_at"] = ended_at
    task_record["process"] = None
    res = task_record.get("result") or {}
    reported_status = res.get("status")
    stage = res.get("current_stage") or task_record.get("current_stage")
    reached_publish = stage in ("publish_clicked", "verifying")

    if reported_status and reported_status.startswith("skipped_"):
        final_status = reported_status
    elif reported_status == "failed_after_publish":
        final_status = "failed_after_publish"
    elif reported_status in ("needs_review", "uncertain") or reached_publish:
        final_status = "needs_review" if reported_status == "needs_review" else "uncertain"
    elif code == 0:
        final_status = "completed"
    else:
        final_status = "failed"

    task_record["status"] = final_status

    updated_queue = load_posting_queue()
    match = find_queue_execution(updated_queue, execution_id)
    if match and match["execution"].get("scheduler_lease", {}).get("lease_id") == lease_id:
        ex = match["execution"]
        ex["ended_at"] = ended_at
        ex["stage"] = stage or ("published" if reported_status == "published" else "unknown")

        if isinstance(res.get("stage_history"), list):
            ex["stage_history"] = res["stage_history"]
        if res.get("brain") or task_record.get("brain"):
            ex["brain"] = res.get("brain") or task_record.get("brain")

        if task_type == "warming" and code == 0 and reported_status == "completed":
            ex["status"] = "completed"
            ex["stage"] = "completed"
            ex["error"] = None
            apply_warming_result(ex, res)
        elif reported_status and reported_status.startswith("skipped_"):
            ex["status"] = reported_status
            ex["stage"] = reported_status
            ex["error"] = res.get("error") or None
        elif reported_status == "published":
            ex["status"] = "published"
            ex["published_at"] = ended_at
            ex["error"] = None
            apply_automation_permalink_result(ex, res, task_type)
            if res.get("first_comment"):
                ex["first_comment_status"] = res["first_comment"]
                ex["first_comment_method"] = res.get("first_comment_method")
                if res["first_comment"] == "submitted_verified":
                    ex["first_comment_verified_at"] = ended_at
        elif reported_status == "failed_after_publish":
            ex["status"] = "failed_after_publish"
            ex["stage"] = "failed_after_publish"
            ex["error"] = res.get("error") or "Publication was not found during post-result verification"
        elif reported_status in ("needs_review", "uncertain") or code == 2 or reached_publish:
            ex["status"] = "needs_review" if reported_status == "needs_review" else "uncertain"
            ex["error"] = res.get("error") or "Publication requires operator review or could not be visually confirmed"
        else:
            ex["retry_count"] = (ex.get("retry_count") or 0) + 1
            ex["status"] = "failed_before_publish"
            ex["error"] = res.get("error") or (
                "Runner exited without a confirmed published outcome"
                if code == 0 else f"Runner exited with exit code {code}"
            )

        if res.get("evidence_dir"):
            ex["evidence_dir"] = res["evidence_dir"]
        if res.get("telemetry"):
            ex["telemetry"] = res["telemetry"]

        ex["logs"] = [l["message"] for l in task_record["logs"]]
        release_execution_lease(ex, lease_id)
        save_posting_queue(updated_queue)

        cleanup = None
        cleanup_error = None
        try:
            cleanup = await stop_profile_container_after_report(profile_id)
        except Exception as error:
            cleanup_error = str(error)

        cleanup_q = load_posting_queue()
        cl_match = find_queue_execution(cleanup_q, execution_id)
        if cl_match:
            cl_match["execution"]["container_stopped_at"] = datetime.now(timezone.utc).isoformat() if (cleanup and cleanup.get("stopped")) else None
            cl_match["execution"]["container_cleanup"] = cleanup
            cl_match["execution"]["container_cleanup_error"] = cleanup_error
            if cleanup:
                cl_match["execution"]["automation_container_owned"] = False
                cl_match["execution"]["container_handoff_expires_at"] = None
            save_posting_queue(cleanup_q)

        asyncio.create_task(_delayed_dispatch(100))
        return {"success": ex["status"] in ("published", "completed"), "status": ex["status"], "code": code}

    return {"success": False, "status": "fenced", "code": code}


async def execute_queue_item(execution_id: str, scheduler_kind: str = "publisher"):
    return await _run_owned_queue(execution_id, _execute_queue_item_inner, scheduler_kind)

async def _execute_queue_comment_retry_inner(execution_id: str, start_admission_claimed: bool = False):
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise RuntimeError("Execution not found")

    comment_text = match["execution"].get("first_comment") or match["post"].get("first_comment") or ""
    if not can_retry_first_comment(match["execution"], comment_text):
        raise RuntimeError("Comment retry is allowed only after a definite pre-submission input failure")

    profile_id = match["execution"]["profile_id"]
    existing = active_automation_tasks.get(profile_id)
    if is_task_process_active(existing):
        raise RuntimeError(f"Automation is already running for profile '{profile_id}'")

    scheduler_cfg = get_scheduler_config()
    slot_check = can_acquire_scheduler_slot(
        queue, active_in_memory_scheduler_leases(), "publisher", profile_id, scheduler_cfg
    )
    if not slot_check["allowed"]:
        err = RuntimeError(f"Scheduler capacity unavailable: {slot_check['reason']}")
        err.statusCode = 409
        raise err

    if get_container_status(profile_id) != "running" and count_running_profile_containers() >= scheduler_cfg["max_active_profile_containers"]:
        err = RuntimeError("The active profile-container limit has been reached.")
        err.statusCode = 409
        raise err

    if get_container_status(profile_id) != "running" and not start_admission_claimed:
        admission = claim_container_start_admission()
        if not admission["allowed"]:
            err = RuntimeError(f"Container start delayed: {admission['reason']}.")
            err.statusCode = 409
            raise err

    now_ms = time.time() * 1000
    now_iso = datetime.now(timezone.utc).isoformat()
    lease = {
        "lease_id": str(uuid.uuid4()),
        "owner_id": MANAGER_INSTANCE_ID,
        "kind": "publisher",
        "profile_id": profile_id,
        "claimed_at": now_iso,
        "heartbeat_at": now_iso,
        "expires_at": datetime.fromtimestamp((now_ms + scheduler_cfg["lease_ttl_ms"]) / 1000, tz=timezone.utc).isoformat(),
    }
    lifecycle.acquire(profile_id, execution_id, lifecycle.OWNER_TOKEN.get())
    match["execution"]["comment_retry_status"] = "running"
    match["execution"]["comment_retry_started_at"] = now_iso
    match["execution"]["first_comment_note"] = None
    match["execution"]["automation_container_owned"] = True
    match["execution"]["container_owner_schema"] = 1
    match["execution"]["container_owner_token"] = lifecycle.OWNER_TOKEN.get()
    match["execution"]["container_handoff_expires_at"] = None
    save_posting_queue(queue)

    task_record = {
        "profile_id": profile_id,
        "task": "comment",
        "comment_retry": True,
        "queue_execution_id": execution_id,
        "status": "running",
        "started_at": now_iso,
        "ended_at": None,
        "logs": [],
        "result": None,
        "error": None,
        "process": None,
        "scheduler_lease": lease,
        "last_activity_at": now_iso,
        "lifecycle_active": True,
    }
    task_record["container_owner_token"] = lifecycle.OWNER_TOKEN.get()
    active_automation_tasks[profile_id] = task_record

    try:
        await ensure_profile_container_ready(profile_id)
        task_record["lifecycle_active"] = False
    except Exception as error:
        task_record["lifecycle_active"] = False
        task_record["status"] = "failed"
        task_record["error"] = str(error)
        failed_q = load_posting_queue()
        failed_m = find_queue_execution(failed_q, execution_id)
        if failed_m:
            failed_m["execution"]["comment_retry_status"] = "failed_to_start"
            failed_m["execution"]["comment_retry_ended_at"] = datetime.now(timezone.utc).isoformat()
            failed_m["execution"]["first_comment_note"] = f"Comment retry could not start: {error}"
            save_posting_queue(failed_q)
        try:
            await stop_profile_container_after_report(profile_id)
        except Exception:
            pass
        raise error

    runner_script = ROOT_DIR / "automation" / "runner.py"
    args = [
        "-u", str(runner_script),
        "--profile", profile_id,
        "--task", "comment",
        "--comment-link", comment_text,
    ]
    if match["execution"].get("post_url"):
        args.extend(["--post-url", match["execution"]["post_url"]])

    proc = await asyncio.create_subprocess_exec(
        str(AUTOMATION_PYTHON), *args,
        cwd=str(ROOT_DIR),
        env=automation_worker_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    task_record["process"] = proc

    async def read_out():
        while True:
            line_b = await proc.stdout.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and "status" in parsed:
                        task_record["result"] = parsed
                        continue
                except Exception:
                    pass
            task_record["logs"].append(normalize_stored_log(line, profile_id))

    async def read_err():
        while True:
            line_b = await proc.stderr.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            task_record["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            task_record["logs"].append(normalize_stored_log(f"[STDERR] {line}", profile_id))

    await asyncio.gather(read_out(), read_err())
    code = await proc.wait()

    ended_at = datetime.now(timezone.utc).isoformat()
    task_record["ended_at"] = ended_at
    task_record["process"] = None
    task_record["scheduler_lease"] = None
    task_record["status"] = "completed" if code == 0 else (task_record.get("result", {}).get("status") or "failed")

    up_q = load_posting_queue()
    up_m = find_queue_execution(up_q, execution_id)
    if up_m:
        apply_comment_retry_result(up_m["execution"], task_record.get("result") or {}, ended_at)
        up_m["execution"]["first_comment_note"] = None if code == 0 else (task_record.get("result", {}).get("error") or f"Comment retry exited with code {code}")
        up_m["execution"]["logs"] = [
            *(up_m["execution"].get("logs") or []),
            *[f"[Comment retry] {entry['message']}" for entry in task_record["logs"]],
        ][-500:]
        save_posting_queue(up_q)

    try:
        await stop_profile_container_after_report(profile_id)
    except Exception:
        pass
    return {"success": code == 0, "status": up_m["execution"].get("first_comment_status") if up_m else "failed"}


async def execute_queue_comment_retry(execution_id: str, start_admission_claimed: bool = False):
    return await _run_owned_queue(execution_id, _execute_queue_comment_retry_inner, start_admission_claimed)

async def _delayed_dispatch(delay_ms: int = 100):
    await asyncio.sleep(delay_ms / 1000.0)
    try:
        await dispatch_pending_queue()
    except Exception as err:
        print(f"Delayed dispatch error: {err}")

# Background loops
def recover_stale_queue_executions(reason: str = "expired_scheduler_lease"):
    try:
        queue = load_posting_queue()
        modified = False
        live_lease_ids = {l["lease_id"] for l in active_in_memory_scheduler_leases()}
        live_comment_retries = {
            r["queue_execution_id"] for r in active_automation_tasks.values()
            if is_task_process_active(r) and r.get("comment_retry") is True
        }

        for batch in queue.get("daily_batches", []):
            for post in batch.get("posts", []):
                for execution in post.get("executions", []):
                    if execution.get("comment_retry_status") == "running" and execution.get("execution_id") not in live_comment_retries:
                        execution["comment_retry_status"] = "interrupted"
                        execution["comment_retry_ended_at"] = datetime.now(timezone.utc).isoformat()
                        execution["first_comment_status"] = "submission_pending"
                        execution["first_comment_note"] = "Comment-only retry was interrupted; no automatic retry was attempted."
                        modified = True

        for exec_item in find_stale_running_executions(queue, live_lease_ids):
            stale_lease = exec_item.get("scheduler_lease")
            apply_interrupted_execution_recovery(exec_item)
            exec_item["scheduler_recovery_reason"] = reason
            exec_item["last_scheduler_lease"] = stale_lease
            exec_item["scheduler_lease"] = None
            print(f"[Scheduler] Recovered stale execution {exec_item.get('execution_id')} as {exec_item.get('status')} (stage: {exec_item.get('last_active_stage')})")
            modified = True

        if modified:
            save_posting_queue(queue)
    except Exception as err:
        print(f"Error recovering stale queue leases: {err}")

def heartbeat_scheduler_leases():
    now_ms = time.time() * 1000
    queue = load_posting_queue()
    queue_modified = False
    scheduler_cfg = get_scheduler_config()

    for record in list(active_automation_tasks.values()):
        if not is_task_process_active(record) or not record.get("scheduler_lease", {}).get("lease_id"):
            continue

        token = record.get("container_owner_token")
        if token and not lifecycle.heartbeat(record["profile_id"], token):
            record["status"] = "stale"
            record["error"] = "Absolute automation deadline exceeded or ownership lost"
            continue

        last_act = record.get("last_activity_at") or record.get("started_at") or ""
        try:
            last_act_ms = datetime.fromisoformat(last_act.replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            last_act_ms = 0.0

        stage = record.get("current_stage") or record.get("result", {}).get("current_stage") or "unknown"
        silence_timeout = worker_silence_timeout_ms(stage, scheduler_cfg["lease_ttl_ms"])

        if now_ms - last_act_ms >= silence_timeout:
            record["status"] = "stale"
            record["error"] = f"Live worker exceeded the {round(silence_timeout / 60000)}-minute silence limit in stage '{stage}'"
            proc = record.get("process")
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            continue

        lease_id = record["scheduler_lease"]["lease_id"]
        q_exec_id = record.get("queue_execution_id")
        if q_exec_id:
            match = find_queue_execution(queue, q_exec_id)
            if match and refresh_execution_lease(match["execution"], lease_id, scheduler_cfg, now_ms):
                record["scheduler_lease"] = dict(match["execution"]["scheduler_lease"])
                queue_modified = True
        elif refresh_execution_lease(record, lease_id, scheduler_cfg, now_ms):
            record["scheduler_lease"] = dict(record["scheduler_lease"])

    if queue_modified:
        save_posting_queue(queue)
    recover_stale_queue_executions("scheduler_lease_expired")

_is_dispatching = False


def _reserve_dispatch_slot(reservations: dict, kind: str, profile_id: str, config: dict) -> bool:
    """Reserve capacity inside one dispatcher pass before async claims can run."""
    if profile_id in reservations["profiles"]:
        return False
    if reservations["total"] >= config["max_total_automation_tasks"]:
        return False
    key = "publishers" if kind == "publisher" else "preparers"
    limit_key = "max_publishers" if kind == "publisher" else "max_preparers"
    if reservations[key] >= config[limit_key]:
        return False
    reservations["profiles"].add(profile_id)
    reservations["total"] += 1
    reservations[key] += 1
    return True


async def _run_dispatched_queue_task(execution_id: str, kind: str):
    """Consume claim races as normal scheduler deferrals instead of orphan errors."""
    try:
        if kind == "preparer":
            match = find_queue_execution(load_posting_queue(), execution_id)
            if not match:
                raise RuntimeError("Execution not found")
            if match["post"].get("type") != "warming":
                return await execute_queue_preparation(execution_id)
        return await execute_queue_item(execution_id, kind)
    except Exception as err:
        message = str(err)
        expected_prefixes = (
            "Batch iteration is not ready:",
            "Scheduler could not claim execution:",
            "Execution is not ready for the publisher slot",
            "Execution does not require passive preparation",
            "Execution not found",
            "queue_claim_in_progress",
            "Container cleanup is in progress",
        )
        if isinstance(err, RuntimeError) and message.startswith(expected_prefixes):
            print(f"[Queue Dispatcher] Deferred {execution_id}: {message}")
            return {"success": False, "status": "deferred", "error": message}
        print(f"[Queue Dispatcher] Execution task {execution_id} failed: {message}")
        traceback.print_exc()
        return {"success": False, "status": "scheduler_error", "error": message}


async def dispatch_pending_queue():
    global _is_dispatching
    if _is_dispatching:
        return
    _is_dispatching = True
    try:
        recover_stale_queue_executions("dispatcher_stale_lease")
        scheduler_cfg = get_scheduler_config()
        queue_snapshot = load_posting_queue()
        due_executions = ordered_due_executions(queue_snapshot, time.time() * 1000)

        initial_slots = scheduler_snapshot(
            queue_snapshot,
            active_in_memory_scheduler_leases(),
            scheduler_cfg,
        )["active"]
        reservations = {
            "profiles": set(),
            "publishers": initial_slots["publishers"],
            "preparers": initial_slots["preparers"],
            "total": initial_slots["total"],
        }

        publisher_batches_claimed = set()
        cooldown_retry_ms = None
        for execution in due_executions:
            exec_id = execution["execution_id"]
            current_q = load_posting_queue()
            match = find_queue_execution(current_q, exec_id)
            if not match or match["execution"].get("status") not in ("pending", "ready"):
                continue
            if match["execution"].get("container_cleanup_in_progress"):
                continue

            batch_id = match["batch"].get("batch_id")
            pid = match["execution"]["profile_id"]
            standalone_warming = match["post"].get("type") == "warming"
            prep_mode = match["execution"].get("preparation_mode") or "off"
            needs_prep = (
                not standalone_warming
                and match["execution"].get("status") == "pending"
                and prep_mode != "off"
                and match["execution"].get("preparation_status") == "pending"
            )

            if needs_prep and count_buffered_preparations(current_q, exec_id) >= scheduler_cfg["max_preparers"]:
                continue

            if get_container_status(pid) != "running" and count_running_profile_containers() >= scheduler_cfg["max_active_profile_containers"]:
                continue

            slot_kind = "preparer" if (needs_prep or standalone_warming) else "publisher"
            if slot_kind == "publisher":
                batch_id = batch_id or f"batch:{id(match['batch'])}"
                if batch_id in publisher_batches_claimed:
                    continue
                iteration_check = batch_iteration_availability(
                    match["batch"],
                    target_execution=match["execution"],
                )
                if not iteration_check["allowed"]:
                    retry_after_ms = iteration_check.get("retry_after_ms")
                    if retry_after_ms is not None:
                        cooldown_retry_ms = (
                            retry_after_ms
                            if cooldown_retry_ms is None
                            else min(cooldown_retry_ms, retry_after_ms)
                        )
                    continue
            availability = can_acquire_scheduler_slot(
                current_q, active_in_memory_scheduler_leases(), slot_kind, pid, scheduler_cfg
            )
            if not availability["allowed"]:
                continue

            if not _reserve_dispatch_slot(reservations, slot_kind, pid, scheduler_cfg):
                continue

            if get_container_status(pid) != "running":
                admission = claim_container_start_admission()
                if not admission["allowed"]:
                    reservations["profiles"].discard(pid)
                    reservations["total"] -= 1
                    reservations["publishers" if slot_kind == "publisher" else "preparers"] -= 1
                    if admission.get("reason") == "container_start_spacing":
                        asyncio.create_task(_delayed_dispatch(CONTAINER_START_SPACING_MS + 100))
                    continue

            print(f"[Queue Dispatcher] Claiming {slot_kind} slot for {exec_id} on profile {pid}...")
            if slot_kind == "publisher":
                publisher_batches_claimed.add(batch_id)
            asyncio.create_task(_run_dispatched_queue_task(exec_id, slot_kind))
        if cooldown_retry_ms is not None:
            asyncio.create_task(_delayed_dispatch(cooldown_retry_ms + 100))
    except Exception as err:
        print(f"Queue Dispatcher error: {err}")
    finally:
        _is_dispatching = False
