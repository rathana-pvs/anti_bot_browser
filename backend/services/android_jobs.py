"""Queue jobs for phone accounts: photo and reel posts, their first comment, and warming visits.

A phone shows one Android user at a time, so a phone runs one job at a time, whichever of its accounts
it is for. A job opens the account the same way the app does (switch to its user, stop the others,
verify its proxy), keeps the phone held, and runs ``automation/phone_runner.py``.

The first comment is a follow-up, not part of the post job: the post job ends as published with the
comment ``phone_pending``, and the dispatcher starts the comment when the phone is free. That state is
in the queue file, so a restart picks it up again. A comment interrupted while running is never sent
again automatically.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from automation.warming_options import normalize_warming_options
from backend.config import AUTOMATION_PYTHON, AUTOMATION_RUNNER, PROFILES_DIR, ROOT_DIR, SHARED_MEDIA_DIR
from backend.services import account_hold, android_devices, android_live, android_network
from backend.services.adb_client import AdbError
from backend.services.android_devices import ANDROID_RUNTIME, read_profile_runtime
from backend.services.profile_service import atomic_write_json
from backend.services.queue_safety import apply_warming_result
from backend.services.queue_scheduler import release_execution_lease
from backend.services.queue_service import (execution_logs, find_queue_execution, load_posting_queue,
                                            persist_queue_execution_stage, save_posting_queue)

PHONE_RUNNER = AUTOMATION_RUNNER.with_name("phone_runner.py")
IMAGE_TYPES = (".jpg", ".jpeg", ".png")
VIDEO_TYPES = (".mp4",)
# The comment follow-up starts this long after the publish tap: a new post needs a while to show.
COMMENT_DELAY_S = 25
COMMENT_PENDING, COMMENT_RUNNING = "phone_pending", "phone_commenting"

MAX_COMMENT_STARTS = 5
_comments_running: set[str] = set()

# Per-account cooldown so the same account doesn't post on a metronome. Keyed by profile_id, holds
# the monotonic time the last post finished. Only published / uncertain outcomes record a cooldown;
# failed-before-publish does not, so retries aren't throttled. In-memory is fine — a restart takes
# longer than the cooldown anyway.
POST_COOLDOWN_FLOOR_S = 60.0
POST_COOLDOWN_MEAN_EXTRA_S = 60.0
POST_COOLDOWN_CEILING_S = 240.0
_last_post_at: dict[str, float] = {}


def _post_cooldown_seconds() -> float:
    """A 60 s floor plus an exponential tail with mean 60 s, clamped at 240 s: most gaps 60–180 s,
    the occasional longer break — not a flat uniform."""
    extra = min(POST_COOLDOWN_CEILING_S - POST_COOLDOWN_FLOOR_S,
                random.expovariate(1 / POST_COOLDOWN_MEAN_EXTRA_S))
    return POST_COOLDOWN_FLOOR_S + extra


async def _wait_post_cooldown(profile_id: str) -> None:
    last = _last_post_at.get(profile_id)
    if last is None:
        return
    needed = _post_cooldown_seconds()
    waited = time.monotonic() - last
    remaining = needed - waited
    if remaining > 0:
        await asyncio.sleep(remaining)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def phone_of(profile_id: str) -> dict | None:
    """The account's phone runtime (serial and Android user), or None for a PC account."""
    runtime = read_profile_runtime(profile_id) if profile_id else None
    return runtime if runtime and runtime.get("kind") == ANDROID_RUNTIME else None


def phone_busy(serial: str) -> bool:
    return android_devices.job_on(serial) is not None


def accepts_automation(config: dict) -> bool:
    """Phone accounts are created with automation marked unavailable; posting from the phone now
    works, so that marker alone no longer keeps them out of the queue."""
    automation = config.get("automation") or {}
    return ((config.get("runtime") or {}).get("kind") == ANDROID_RUNTIME
            and automation.get("reason") == android_devices.AUTOMATION_UNAVAILABLE)


def can_take_auto_visit(config: dict) -> bool:
    """Auto warming only uses a phone that is set up, here, this PC's to run and idle: nobody is
    watching it in the app, since a visit switches the Android user on screen."""
    from backend.services.android_setup import is_set_up
    runtime = config.get("runtime") or {}
    serial = android_devices.canonical_serial(runtime.get("device_serial"))
    if not serial or not is_set_up(config) or phone_busy(serial) or serial in android_live._sessions:
        return False
    if (android_devices.load_store()["devices"].get(serial) or {}).get("role") == "secondary":
        return False
    try:
        return any(android_devices.canonical_serial(device["serial"]) == serial and device["state"] == "device"
                   for device in android_devices._client().devices())
    except AdbError:
        return False


def media_path(post: dict) -> Path | None:
    name = post.get("media_file") or ""
    if not name:
        return None
    path = (SHARED_MEDIA_DIR / name).resolve()
    return path if path.parent == SHARED_MEDIA_DIR.resolve() else None


def unsupported_reason(post: dict) -> str | None:
    kind = post.get("type") or "photo"
    if kind == "warming":
        if (post.get("warming_options") or {}).get("check"):
            return "Account checks are not available on phones yet."
        return None
    if kind not in ("photo", "post", "reel"):
        return f"{kind.capitalize()} jobs are not available on phones yet."
    path = media_path(post)
    if not path or not path.is_file():
        return "Phone posts need a photo or video from the media library."
    if kind == "reel" and path.suffix.lower() not in VIDEO_TYPES:
        return "Phone reels need an MP4 video."
    if kind != "reel" and path.suffix.lower() not in IMAGE_TYPES:
        return "Phone photo posts need a JPEG or PNG image."
    return None


def _update(execution_id: str, lease_id: str | None, change) -> dict | None:
    """Applies `change` to the execution when this attempt still owns it; returns the execution."""
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        return None
    if lease_id and (match["execution"].get("scheduler_lease") or {}).get("lease_id") != lease_id:
        return None
    change(match["execution"])
    if lease_id:
        release_execution_lease(match["execution"], lease_id)
    save_posting_queue(queue)
    return match["execution"]


def _put_on_hold(profile_id: str, note: str) -> None:
    path = PROFILES_DIR / profile_id / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    account_hold.hold(config, note, datetime.now(timezone.utc))
    atomic_write_json(path, config)


async def _run_runner(args: list[str], record: dict, on_event) -> dict:
    """Runs the phone runner, feeding each event to `on_event`; returns its last-line result."""
    from backend.services.automation_service import automation_worker_env, normalize_stored_log
    proc = await asyncio.create_subprocess_exec(
        str(AUTOMATION_PYTHON), "-u", str(PHONE_RUNNER), *args, cwd=str(ROOT_DIR), env=automation_worker_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    record["process"] = proc
    record["lifecycle_active"] = False
    result: dict = {}
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        record["last_activity_at"] = now_iso()
        parsed = None
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                pass
        if isinstance(parsed, dict) and "event" in parsed:
            on_event(parsed)
            line = " ".join(f"{key}={value}" for key, value in parsed.items() if key != "t")
        elif isinstance(parsed, dict) and "status" in parsed:
            result = parsed
            continue
        elif "Warning" in line or line.startswith(("warnings.warn", "\x1b[")):
            continue  # OCR start-up noise
        record["logs"].append(normalize_stored_log(line, record["profile_id"]))
    record["exit_code"] = await proc.wait()
    record["process"] = None
    return result


# --- post jobs -------------------------------------------------------------------------------------

async def execute_queue_phone(claim_res: dict) -> dict:
    """Runs one claimed photo or reel execution on its phone. Called with the account's queue lease
    and lifecycle ownership already held."""
    from backend.services.automation_service import _delayed_dispatch, active_automation_tasks
    execution, post = claim_res["targetExec"], claim_res["targetPost"]
    if post.get("type") == "warming":
        return await execute_queue_warming(claim_res)
    execution_id, lease_id = execution["execution_id"], claim_res["leaseId"]
    profile_id = execution["profile_id"]
    runtime = phone_of(profile_id) or {}
    serial, user_id = runtime.get("device_serial"), runtime.get("android_user_id")

    def finish(status: str, error: str | None, **fields) -> dict:
        def change(ex):
            ex.update(status=status, error=error, ended_at=now_iso(), automation_container_owned=False, **fields)
            if status == "failed_before_publish":
                ex["retry_count"] = (ex.get("retry_count") or 0) + 1
            if status not in ("uncertain",):
                ex["stage"] = fields.get("stage") or status
        _update(execution_id, lease_id, change)
        asyncio.create_task(_delayed_dispatch(100))
        return {"success": status == "published", "status": status}

    reason = unsupported_reason(post)
    if reason or not serial:
        return finish("skipped_unsupported", reason or "The account's phone is not known.")
    # Space posts for the same account; the phone stays free for other accounts during the wait.
    await _wait_post_cooldown(profile_id)
    if not android_devices.hold_for_job(serial, f"post:{execution_id}"):
        def back_to_pending(ex):
            ex.update(status="pending", stage="pending", automation_container_owned=False)
        _update(execution_id, lease_id, back_to_pending)
        return {"success": False, "status": "deferred", "error": "The phone is running another job"}

    kind = "reel" if post.get("type") == "reel" else "photo"
    record = {"profile_id": profile_id, "task": kind, "status": "running", "started_at": now_iso(),
              "ended_at": None, "logs": [], "result": None, "current_stage": "opening", "error": None,
              "queue_execution_id": execution_id, "scheduler_lease": claim_res["lease"], "process": None,
              # Active while the account opens (up to a few minutes of proxy checks), before the runner exists.
              "last_activity_at": now_iso(), "lifecycle_active": True, "surface": "phone"}
    active_automation_tasks[profile_id] = record
    tapped_at: list[str] = []

    def on_event(event: dict) -> None:
        name = event.get("event")
        if name == "about_to_publish":
            # Durable before the tap result is known: from here a failure is never retried.
            tapped_at.append(now_iso())
            record["current_stage"] = "publish_clicked"
            persist_queue_execution_stage(execution_id, "publish_clicked", tapped_at[0], lease_id)
        elif name == "published":
            record["current_stage"] = "verifying"

    try:
        opened = await asyncio.to_thread(android_network.open_account, profile_id, True)
        if not opened["ready"]:
            record["status"] = "failed"
            return finish("failed_before_publish", f"The phone account is not ready: {opened.get('reason')}")
        evidence = PROFILES_DIR / profile_id / "automation_evidence" / execution_id
        args = ["--serial", serial, "--user", str(user_id), "--task", kind, "--media", str(media_path(post)),
                "--caption", execution.get("spun_caption") or post.get("base_caption") or "",
                "--comment", execution.get("first_comment", post.get("first_comment")) or "", "--comment-later", "--evidence-dir", str(evidence)]
        record["current_stage"] = "running"
        result = await _run_runner(args, record, on_event)
        record["result"] = result
        outcome = result.get("status")
        common = {"logs": [entry["message"] for entry in record["logs"]][-500:], "evidence_dir": str(evidence)}
        if outcome == "published":
            record["status"] = "completed"
            comment = {}
            if result.get("comment") == "pending":
                comment = {"first_comment_status": COMMENT_PENDING, "phone_comment_due_at":
                           datetime.fromtimestamp(time.time() + COMMENT_DELAY_S, tz=timezone.utc).isoformat()}
            return finish("published", None, published_at=now_iso(), stage="published",
                          publish_tapped_at=tapped_at[0] if tapped_at else None, **comment, **common)
        if outcome == "blocked":
            record["status"] = "needs_review"
            _put_on_hold(profile_id, "Facebook logged out or stopped this account on the phone")
            return finish("needs_review", result.get("reason"), **common)
        if outcome == "uncertain" or tapped_at:
            record["status"] = "uncertain"
            return finish("uncertain", result.get("reason") or "The phone job stopped after the publish tap",
                          media_left_on_phone=result.get("media_left"), **common)
        record["status"] = "failed"
        return finish("failed_before_publish", result.get("reason") or
                      f"The phone runner exited with code {record.get('exit_code')}", **common)
    except Exception as error:
        record["status"] = "uncertain" if tapped_at else "failed"
        return finish("uncertain" if tapped_at else "failed_before_publish", f"{type(error).__name__}: {error}")
    finally:
        record["ended_at"] = now_iso()
        record["scheduler_lease"] = None
        record["lifecycle_active"] = False
        android_devices.release_job(serial)
        if record.get("status") in ("completed", "uncertain"):
            _last_post_at[profile_id] = time.monotonic()


# --- warming visits --------------------------------------------------------------------------------

async def execute_queue_warming(claim_res: dict) -> dict:
    """Runs one claimed warming item on its phone: browsing only, nothing published, so a stopped
    visit is simply failed and leaves no post cooldown behind."""
    from backend.services.automation_service import _delayed_dispatch, active_automation_tasks
    execution, post = claim_res["targetExec"], claim_res["targetPost"]
    execution_id, lease_id = execution["execution_id"], claim_res["leaseId"]
    profile_id = execution["profile_id"]
    runtime = phone_of(profile_id) or {}
    serial, user_id = runtime.get("device_serial"), runtime.get("android_user_id")

    def finish(status: str, error: str | None, result: dict | None = None, **fields) -> dict:
        def change(ex):
            ex.update(status=status, stage=status, error=error, ended_at=now_iso(), automation_container_owned=False, **fields)
            if status == "completed":
                apply_warming_result(ex, result)
        _update(execution_id, lease_id, change)
        asyncio.create_task(_delayed_dispatch(100))
        return {"success": status == "completed", "status": status}

    reason = unsupported_reason(post)
    if reason or not serial:
        return finish("skipped_unsupported", reason or "The account's phone is not known.")
    if not android_devices.hold_for_job(serial, f"warming:{execution_id}"):
        def back_to_pending(ex):
            ex.update(status="pending", stage="pending", automation_container_owned=False)
        _update(execution_id, lease_id, back_to_pending)
        return {"success": False, "status": "deferred", "error": "The phone is running another job"}

    record = {"profile_id": profile_id, "task": "warming", "status": "running", "started_at": now_iso(),
              "ended_at": None, "logs": [], "result": None, "current_stage": "opening", "error": None,
              "queue_execution_id": execution_id, "scheduler_lease": claim_res["lease"], "process": None,
              "last_activity_at": now_iso(), "lifecycle_active": True, "surface": "phone"}
    active_automation_tasks[profile_id] = record
    try:
        opened = await asyncio.to_thread(android_network.open_account, profile_id, True)
        if not opened["ready"]:
            record["status"] = "failed"
            return finish("failed_before_publish", f"The phone account is not ready: {opened.get('reason')}")
        evidence = PROFILES_DIR / profile_id / "automation_evidence" / execution_id
        args = ["--serial", serial, "--user", str(user_id), "--task", "warming", "--profile", profile_id,
                "--warming-options", json.dumps(normalize_warming_options(post.get("warming_options"))),
                "--evidence-dir", str(evidence)]
        record["current_stage"] = "warming"
        result = await _run_runner(args, record, lambda event: None)
        record["result"] = result
        common = {"logs": [entry["message"] for entry in record["logs"]][-500:], "evidence_dir": str(evidence)}
        outcome = result.get("status")
        if outcome == "completed":
            record["status"] = "completed"
            return finish("completed", None, result, **common)
        if outcome == "blocked":
            record["status"] = "needs_review"
            _put_on_hold(profile_id, "Facebook logged out or stopped this account on the phone")
            return finish("needs_review", result.get("reason"), **common)
        record["status"] = "failed"
        return finish("failed_before_publish", result.get("reason") or
                      f"The phone runner exited with code {record.get('exit_code')}", **common)
    except Exception as error:
        record["status"] = "failed"
        return finish("failed_before_publish", f"{type(error).__name__}: {error}")
    finally:
        record["ended_at"] = now_iso()
        record["scheduler_lease"] = None
        record["lifecycle_active"] = False
        android_devices.release_job(serial)


# --- setup's first-use pass -----------------------------------------------------------------------

GET_READY_TIMEOUT_S = 240


def run_get_ready(profile_id: str) -> dict:
    """Setup's pass after login: opens Facebook's Gallery once so its consent screens are answered
    before any job. Holds the phone like a job; nothing is posted. Returns the runner's result."""
    import subprocess
    from backend.services.automation_service import automation_worker_env
    runtime = phone_of(profile_id) or {}
    serial = runtime.get("device_serial")
    if not serial or not android_devices.hold_for_job(serial, f"setup:{profile_id}"):
        return {"status": "failed", "reason": "The phone is running a queued job; try again when it finishes."}
    try:
        opened = android_network.open_account(profile_id, True)
        if not opened["ready"]:
            return {"status": "failed", "reason": f"The phone account is not ready: {opened.get('reason')}"}
        evidence = PROFILES_DIR / profile_id / "automation_evidence" / "setup_get_ready"
        completed = subprocess.run(
            [str(AUTOMATION_PYTHON), "-u", str(PHONE_RUNNER), "--serial", serial, "--user",
             str(runtime["android_user_id"]), "--task", "get_ready", "--evidence-dir", str(evidence)],
            cwd=str(ROOT_DIR), env=automation_worker_env(), capture_output=True, text=True,
            timeout=GET_READY_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": "Facebook did not get ready in time."}
    finally:
        android_devices.release_job(serial)
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if "status" in result and "event" not in result:
                return result
    return {"status": "failed", "reason": f"The phone runner exited with code {completed.returncode}"}


# --- first comment follow-ups ----------------------------------------------------------------------

def due_comments(queue: dict, now: float) -> list[tuple[dict, dict]]:
    """(execution, post) pairs whose phone comment can start now. Interrupted ones are settled
    without a retry (the send tap may have happened)."""
    due = []
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                state = execution.get("first_comment_status")
                if state == COMMENT_RUNNING and execution["execution_id"] not in _comments_running:
                    execution["first_comment_status"] = "submission_pending"
                    execution["first_comment_note"] = "The phone comment was interrupted; it was not sent again."
                    execution["_changed"] = True
                elif state == COMMENT_PENDING and execution.get("status") == "published":
                    at = execution.get("phone_comment_due_at")
                    if not at or datetime.fromisoformat(at).timestamp() <= now:
                        due.append((execution, post))
    return due


def dispatch_comments(active_profiles: set[str]) -> list[str]:
    """Starts every due comment whose phone and account are free; returns the started executions."""
    queue = load_posting_queue()
    due = due_comments(queue, time.time())
    changed = False
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.pop("_changed", None):
                    changed = True
    if changed:
        save_posting_queue(queue)
    started = []
    for execution, post in due:
        profile_id = execution["profile_id"]
        runtime = phone_of(profile_id)
        if runtime and phone_busy(runtime["device_serial"]):
            from backend.services import auto_warming
            auto_warming.yield_phone_for_post(queue, runtime["device_serial"])
        if not runtime or profile_id in active_profiles or phone_busy(runtime["device_serial"]):
            continue
        if not android_devices.hold_for_job(runtime["device_serial"], f"comment:{execution['execution_id']}"):
            continue
        active_profiles.add(profile_id)
        asyncio.create_task(run_comment(execution["execution_id"], runtime, post))
        started.append(execution["execution_id"])
    return started


def request_comment_retry(execution_id: str) -> dict:
    """The queue's Retry for a phone comment that was not found: queue it again, any post age."""
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match or match["execution"].get("first_comment_status") != "failed_input_not_found":
        raise RuntimeError("Comment retry is allowed only after the post was not found")
    match["execution"].update(first_comment_status=COMMENT_PENDING, phone_comment_due_at=None,
                              phone_comment_any_age=True, first_comment_note=None)
    save_posting_queue(queue)
    return {"success": True, "status": COMMENT_PENDING}


async def run_comment(execution_id: str, runtime: dict, post: dict) -> None:
    from backend.services.automation_service import active_automation_tasks
    from backend.services import container_lifecycle as lifecycle
    serial = runtime["device_serial"]
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    execution = match["execution"] if match else None
    if not execution or execution.get("first_comment_status") != COMMENT_PENDING:
        android_devices.release_job(serial)
        return
    profile_id = execution["profile_id"]
    try:  # the same account lock the post jobs and browser tasks use
        token = lifecycle.acquire(profile_id, execution_id)
    except RuntimeError:
        android_devices.release_job(serial)
        return
    _comments_running.add(execution_id)
    execution.update(first_comment_status=COMMENT_RUNNING, comment_retry_started_at=now_iso())
    save_posting_queue(queue)
    record = {"profile_id": profile_id, "task": "comment", "status": "running", "started_at": now_iso(),
              "ended_at": None, "logs": [], "result": None, "error": None, "process": None,
              "queue_execution_id": execution_id, "last_activity_at": now_iso(), "surface": "phone"}
    active_automation_tasks[profile_id] = record
    sent: list[bool] = []
    result: dict = {}
    try:
        opened = await asyncio.to_thread(android_network.open_account, profile_id, True)
        if not opened["ready"]:
            result = {"comment": "not_started", "comment_reason": f"The phone account is not ready: {opened.get('reason')}"}
        else:
            args = ["--serial", serial, "--user", str(runtime["android_user_id"]), "--task", "comment",
                    "--kind", "reel" if post.get("type") == "reel" else "photo",
                    "--caption", execution.get("spun_caption") or post.get("base_caption") or "",
                    "--comment", execution.get("first_comment", post.get("first_comment")) or "",
                    # The post's own folder, so the job shows its comment screenshots after the upload ones.
                    "--evidence-dir", str(PROFILES_DIR / profile_id / "automation_evidence" / execution_id)]
            # The looks are timed from when the upload was confirmed; a reel's upload can take minutes.
            confirmed = execution.get("published_at") or execution.get("publish_tapped_at")
            if confirmed and not execution.get("phone_comment_any_age"):
                args += ["--published-ago", str(max(0, int(time.time() - datetime.fromisoformat(confirmed).timestamp())))]
            result = await _run_runner(args, record, lambda e: sent.append(True) if e.get("event") == "about_to_comment" else None)
    except Exception as error:
        result = {"comment": "uncertain" if sent else "not_started", "comment_reason": f"{type(error).__name__}: {error}"}
    finally:
        record["ended_at"] = now_iso()
        _comments_running.discard(execution_id)
        android_devices.release_job(serial)
        await lifecycle.release(profile_id, token, _never_stop)
    comment = result.get("comment")
    if result.get("account_blocked"):
        _put_on_hold(profile_id, "Facebook logged out or stopped this account on the phone")
    status = {"posted": "submitted_verified", "uncertain": "submitted_unverified", "skipped": "failed_input_not_found",
              "not_started": COMMENT_PENDING}.get(comment)
    if status is None:  # no result line: the runner died; after the send tap it may have gone out
        status = "submitted_unverified" if sent else "failed_input_not_found"
    record["status"] = "completed" if status == "submitted_verified" else "failed"

    def change(ex):
        ex["first_comment_status"] = status
        ex["comment_retry_ended_at"] = record["ended_at"]
        ex["first_comment_note"] = result.get("comment_reason") or result.get("reason")
        if status == "submitted_verified":
            ex["first_comment_verified_at"] = record["ended_at"]
        if status == COMMENT_PENDING:  # the phone was not ready: try again on a later pass, a few times
            ex["phone_comment_starts"] = (ex.get("phone_comment_starts") or 0) + 1
            if ex["phone_comment_starts"] >= MAX_COMMENT_STARTS:
                ex["first_comment_status"] = "failed_input_not_found"
            ex["phone_comment_due_at"] = datetime.fromtimestamp(time.time() + 120, tz=timezone.utc).isoformat()
        ex["logs"] = [*execution_logs(ex), *[f"[Phone comment] {e['message']}" for e in record["logs"]]][-500:]
    _update(execution_id, None, change)


async def _never_stop(profile_id: str) -> dict:
    return {"stopped": False, "reason": "phone_account"}
