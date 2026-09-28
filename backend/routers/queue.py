import asyncio
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body

from backend.config import PROFILES_DIR
from backend.services.queue_service import (
    load_posting_queue,
    save_posting_queue,
    find_queue_execution,
    generate_spun_caption,
    with_queue_claim_lock,
)
from backend.services.queue_safety import (
    is_execution_deletion_locked,
    batch_contains_unresolved_execution,
    validate_facebook_permalink,
    apply_verified_permalink_backfill,
    apply_comment_evidence_backfill,
    can_retry_first_comment,
)
from backend.services.queue_scheduler import (
    compute_execution_schedule,
    can_acquire_scheduler_slot,
)
from backend.services.profile_pipeline import (
    shuffled_profile_order,
    count_buffered_preparations,
    active_batch_id,
    batch_has_review_hold,
    batch_iteration_availability,
)
from backend.services.telemetry_summary import build_queue_telemetry_summary
from backend.services.docker_service import (
    get_container_status,
    count_running_profile_containers,
    claim_container_start_admission,
    get_scheduler_config,
)
from backend.services.automation_service import (
    current_scheduler_snapshot,
    active_in_memory_scheduler_leases,
    execute_queue_preparation,
    execute_queue_item,
    execute_queue_comment_retry,
    dispatch_pending_queue,
)

router = APIRouter(prefix="/api/queue", tags=["queue"])

@router.get("")
def get_queue():
    queue = load_posting_queue()
    all_executions = []
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for exec_item in post.get("executions", []):
                all_executions.append({
                    **exec_item,
                    "batch_id": batch.get("batch_id"),
                    "batch_name": batch.get("name"),
                    "post_type": post.get("type"),
                    "media_file": post.get("media_file"),
                    "base_caption": post.get("base_caption"),
                    "first_comment": post.get("first_comment"),
                    "scrolls": post.get("scrolls"),
                })

    def parse_sched(item):
        s = item.get("scheduled_at") or ""
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    all_executions.sort(key=parse_sched)

    stats = {
        "total": len(all_executions),
        "pending": len([e for e in all_executions if e.get("status") in ("pending", "ready")]),
        "running": len([e for e in all_executions if e.get("status") in ("running", "preparing")]),
        "published": len([e for e in all_executions if e.get("status") == "published"]),
        "completed": len([e for e in all_executions if e.get("status") == "completed"]),
        "failed": len([e for e in all_executions if e.get("status") in ("failed", "failed_before_publish")]),
        "uncertain": len([e for e in all_executions if e.get("status") in ("uncertain", "needs_review")]),
        "skipped": len([e for e in all_executions if e.get("status") and e["status"].startswith("skipped")]),
    }

    scheduler = current_scheduler_snapshot(queue)
    scheduler["batch_owner_id"] = active_batch_id(queue)
    return {
        "queue_version": queue.get("queue_version", "2.0"),
        "stats": stats,
        "scheduler": scheduler,
        "telemetry_summary": build_queue_telemetry_summary(all_executions),
        "batches": queue.get("daily_batches", []),
        "executions": all_executions,
    }

@router.post("/batch")
async def create_batch(payload: dict = Body(...)):
    name = payload.get("name")
    target_profiles = payload.get("target_profiles")
    schedule_window = payload.get("schedule_window") or {}
    posts = payload.get("posts")

    if not isinstance(target_profiles, list) or len(target_profiles) == 0:
        raise HTTPException(status_code=400, detail="Please select at least one target profile.")
    if not isinstance(posts, list) or len(posts) == 0:
        raise HTTPException(status_code=400, detail="Please provide at least one post item.")

    queue = load_posting_queue()
    batch_id = f"batch_{int(time.time() * 1000)}"
    start_time_str = schedule_window.get("start_time") or "09:00"
    end_time_str = schedule_window.get("end_time") or "21:00"

    raw_stagger = schedule_window.get("profile_stagger_seconds")
    legacy_stagger = schedule_window.get("profile_stagger_minutes")
    if raw_stagger is not None:
        try:
            stagger_seconds = max(0, int(raw_stagger))
        except ValueError:
            stagger_seconds = 60
    elif legacy_stagger is not None:
        try:
            stagger_seconds = max(0, int(legacy_stagger)) * 60
        except ValueError:
            stagger_seconds = 60
    else:
        stagger_seconds = 60

    try:
        iteration_delay_seconds = max(
            0,
            min(3600, int(schedule_window.get("batch_iteration_delay_seconds", 0))),
        )
    except (TypeError, ValueError):
        iteration_delay_seconds = 60

    preparation_mode = schedule_window.get("session_preparation_mode") or "off"
    if preparation_mode not in ("off", "brief", "extended"):
        raise HTTPException(status_code=400, detail="session_preparation_mode must be 'off', 'brief', or 'extended'")

    accepted_profiles = []
    skipped_profiles = []

    for pid in target_profiles:
        cfg_path = PROFILES_DIR / pid / "config.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if cfg.get("automation") and cfg["automation"].get("enabled") is False:
                    skipped_profiles.append({"profile_id": pid, "reason": cfg["automation"].get("reason") or "manual_control_only"})
                    continue
            except Exception:
                pass
        accepted_profiles.append(pid)

    if not accepted_profiles:
        raise HTTPException(status_code=400, detail={
            "error": "All selected profiles have automation disabled in their config.json.",
            "skipped": skipped_profiles,
        })

    is_start_now = payload.get("start_now") is True or schedule_window.get("start_now") is True

    previous_batch = (queue.get("daily_batches") or [None])[0]
    previous_order = (previous_batch.get("profile_execution_order") or previous_batch.get("target_profiles") or []) if previous_batch else []
    previous_last = previous_order[-1] if previous_order else None
    profile_execution_order = shuffled_profile_order(accepted_profiles, previous_last)

    posting_orders = {}
    for pid in accepted_profiles:
        order = list(range(len(posts)))
        posting_orders[pid] = order

    now = datetime.now()
    try:
        start_h, start_m = map(int, start_time_str.split(":"))
    except Exception:
        start_h, start_m = 9, 0
    try:
        end_h, end_m = map(int, end_time_str.split(":"))
    except Exception:
        end_h, end_m = 21, 0

    if is_start_now:
        window_start = now
    else:
        window_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)

    window_end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if window_end <= window_start:
        window_end = window_end.replace(day=window_end.day + 1)

    total_window_ms = max(10 * 60 * 1000, (window_end.timestamp() - window_start.timestamp()) * 1000)
    post_slot_ms = total_window_ms / (len(posts) - 1) if len(posts) > 1 else total_window_ms / 2

    formatted_posts = []
    for post_idx, post in enumerate(posts):
        post_id = f"p_{post_idx + 1}"
        executions = []

        for p_idx, pid in enumerate(profile_execution_order):
            slot_idx = posting_orders[pid].index(post_idx)
            jitter = 0

            sched_ms = compute_execution_schedule(
                now_ms=now.timestamp() * 1000,
                is_start_now=is_start_now,
                slot_index=slot_idx,
                profile_index=p_idx,
                stagger_ms=stagger_seconds * 1000,
                post_slot_ms=post_slot_ms,
                window_start_ms=window_start.timestamp() * 1000,
                jitter_ms=jitter,
            )
            sched_dt = datetime.fromtimestamp(sched_ms / 1000, tz=timezone.utc)

            spun_caption = generate_spun_caption(post.get("base_caption", ""), p_idx, pid) if post.get("ai_spin") is not False else post.get("base_caption", "")

            executions.append({
                "execution_id": f"exec_{int(time.time() * 1000)}_{p_idx}_{post_idx}",
                "profile_id": pid,
                "scheduled_at": sched_dt.isoformat(),
                "batch_iteration_index": slot_idx * len(profile_execution_order) + p_idx,
                "spun_caption": spun_caption,
                "status": "pending",
                "stage": "pending",
                "stage_history": [{"stage": "pending", "timestamp": datetime.now(timezone.utc).isoformat()}],
                "preparation_mode": preparation_mode,
                "preparation_status": "not_requested" if preparation_mode == "off" else "pending",
                "retry_count": 0,
                "error": None,
                "published_at": None,
                "logs": [],
            })

        formatted_posts.append({
            "post_id": post_id,
            "type": post.get("type", "photo"),
            "media_file": post.get("media_file", ""),
            "base_caption": post.get("base_caption", ""),
            "first_comment": post.get("first_comment"),
            "scrolls": max(1, int(post.get("scrolls", 4))),
            "ai_spin": post.get("ai_spin") is not False,
            "executions": executions,
        })

    new_batch = {
        "batch_id": batch_id,
        "name": name or f"Daily Batch {now.strftime('%Y-%m-%d')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_profiles": accepted_profiles,
        "profile_execution_order": profile_execution_order,
        "schedule_window": {
            "start_time": start_time_str,
            "end_time": end_time_str,
            "profile_stagger_seconds": stagger_seconds,
            "batch_iteration_delay_seconds": iteration_delay_seconds,
            "session_preparation_mode": preparation_mode,
            "start_now": is_start_now,
            "execution_flow": "sequential",
        },
        "posting_order_per_profile": posting_orders,
        "posts": formatted_posts,
    }

    if "daily_batches" not in queue:
        queue["daily_batches"] = []
    queue["daily_batches"].insert(0, new_batch)
    save_posting_queue(queue)

    if is_start_now:
        async def _start_soon():
            await asyncio.sleep(0.5)
            await dispatch_pending_queue()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_start_soon())
        except RuntimeError:
            pass

    return {
        "success": True,
        "batch_id": batch_id,
        "accepted_profiles": accepted_profiles,
        "skipped_profiles": skipped_profiles,
        "total_executions": len(accepted_profiles) * len(posts),
        "batch": new_batch,
    }

@router.post("/batch/{batch_id}/posts")
async def append_batch_posts(batch_id: str, payload: dict = Body(...)):
    posts = payload.get("posts")
    if not isinstance(posts, list) or not posts:
        raise HTTPException(status_code=400, detail="Please provide at least one post item.")
    for post in posts:
        if not isinstance(post, dict) or (not str(post.get("media_file") or "").strip() and not str(post.get("base_caption") or "").strip()):
            raise HTTPException(status_code=400, detail="Each appended post needs media or a caption.")
        if post.get("type", "photo") not in ("photo", "reel"):
            raise HTTPException(status_code=400, detail="Appended post type must be 'photo' or 'reel'.")

    def _append():
        queue = load_posting_queue()
        batch = next((item for item in queue.get("daily_batches", []) if item.get("batch_id") == batch_id), None)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        existing_executions = [
            execution
            for existing_post in batch.get("posts", [])
            for execution in existing_post.get("executions", [])
        ]
        if any(execution.get("status") in ("uncertain", "needs_review") for execution in existing_executions):
            raise HTTPException(status_code=409, detail="Resolve the batch's uncertain execution before appending more posts.")
        if not any(execution.get("status") in ("pending", "ready", "running", "preparing") for execution in existing_executions):
            raise HTTPException(status_code=409, detail="This batch has already finished. Create a new batch instead.")

        profile_order = batch.get("profile_execution_order") or batch.get("target_profiles") or []
        if not profile_order:
            raise HTTPException(status_code=409, detail="The batch has no target profiles.")

        preparation_mode = batch.get("schedule_window", {}).get("session_preparation_mode") or "off"
        known_iterations = []
        for execution in existing_executions:
            try:
                known_iterations.append(int(execution["batch_iteration_index"]))
            except (KeyError, TypeError, ValueError):
                pass
        max_iteration = max(known_iterations, default=-1)
        # Normalize batches created by older builds so appended work always
        # stays behind every existing execution instead of jumping the queue.
        for execution in existing_executions:
            if execution.get("batch_iteration_index") is None:
                max_iteration += 1
                execution["batch_iteration_index"] = max_iteration
        next_iteration = max_iteration + 1
        now = datetime.now(timezone.utc)
        existing_schedule_times = []
        for execution in existing_executions:
            try:
                existing_schedule_times.append(datetime.fromisoformat(str(execution.get("scheduled_at") or "").replace("Z", "+00:00")))
            except (TypeError, ValueError):
                pass
        batch_start = min(existing_schedule_times, default=now)
        appended_posts = []
        existing_post_count = len(batch.get("posts", []))

        for append_idx, post in enumerate(posts):
            post_index = existing_post_count + append_idx
            executions = []
            for profile_idx, profile_id in enumerate(profile_order):
                caption = str(post.get("base_caption") or "")
                spun_caption = (
                    generate_spun_caption(caption, profile_idx, profile_id)
                    if post.get("ai_spin") is not False
                    else caption
                )
                execution_id = f"exec_{int(time.time() * 1000)}_append_{append_idx}_{profile_idx}"
                executions.append({
                    "execution_id": execution_id,
                    "profile_id": profile_id,
                    "scheduled_at": batch_start.isoformat(),
                    "batch_iteration_index": next_iteration,
                    "spun_caption": spun_caption,
                    "status": "pending",
                    "stage": "pending",
                    "stage_history": [{
                        "stage": "pending",
                        "timestamp": now.isoformat(),
                        "reason": "appended_to_active_batch",
                    }],
                    "preparation_mode": preparation_mode,
                    "preparation_status": "not_requested" if preparation_mode == "off" else "pending",
                    "retry_count": 0,
                    "error": None,
                    "published_at": None,
                    "logs": [],
                })
                next_iteration += 1

            appended_post = {
                "post_id": f"p_append_{int(time.time() * 1000)}_{append_idx}",
                "type": post.get("type", "photo"),
                "media_file": str(post.get("media_file") or ""),
                "base_caption": str(post.get("base_caption") or ""),
                "first_comment": post.get("first_comment"),
                "scrolls": max(1, int(post.get("scrolls", 4))),
                "ai_spin": post.get("ai_spin") is not False,
                "executions": executions,
            }
            batch.setdefault("posts", []).append(appended_post)
            appended_posts.append(appended_post)

            for profile_id in profile_order:
                batch.setdefault("posting_order_per_profile", {}).setdefault(profile_id, []).append(post_index)

        batch["updated_at"] = now.isoformat()
        if not save_posting_queue(queue):
            raise HTTPException(status_code=500, detail="Failed to save appended posts.")
        return {
            "success": True,
            "batch_id": batch_id,
            "posts_added": len(appended_posts),
            "executions_added": len(appended_posts) * len(profile_order),
        }

    result = with_queue_claim_lock(_append)
    try:
        asyncio.get_running_loop().create_task(dispatch_pending_queue())
    except RuntimeError:
        pass
    return result

@router.delete("/batch/{batch_id}")
def delete_batch(batch_id: str):
    queue = load_posting_queue()
    batches = queue.get("daily_batches", [])
    target_batch = next((b for b in batches if b.get("batch_id") == batch_id), None)
    if not target_batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch_contains_unresolved_execution(target_batch):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a batch containing active, uncertain, or needs-review executions. Stop safely or resolve every ambiguous outcome first.",
        )

    queue["daily_batches"] = [b for b in batches if b.get("batch_id") != batch_id]
    save_posting_queue(queue)
    return {"success": True, "message": f"Batch {batch_id} removed"}

@router.delete("/execution/{execution_id}")
def delete_execution(execution_id: str):
    queue = load_posting_queue()
    found = False
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            execs = post.get("executions", [])
            for idx, ex in enumerate(execs):
                if ex.get("execution_id") == execution_id:
                    if is_execution_deletion_locked(ex):
                        raise HTTPException(
                            status_code=409,
                            detail="Cannot delete an active or unresolved execution. Stop safely or resolve its publication outcome first.",
                        )
                    execs.pop(idx)
                    found = True
                    break
            if found:
                break
        if found:
            break

    if found:
        save_posting_queue(queue)
        return {"success": True, "message": f"Execution {execution_id} removed"}
    raise HTTPException(status_code=404, detail="Execution not found")

@router.post("/run-now/{execution_id}")
async def run_execution_now(execution_id: str):
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    target_exec = match["execution"]
    target_post = match["post"]
    current_status = target_exec.get("status", "")

    if current_status in ("uncertain", "needs_review"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot rerun an execution in '{target_exec['status']}' state directly. Please review on Facebook and resolve the outcome first to prevent duplicate posts.",
        )

    is_manual_retry = current_status.startswith("skipped_") or current_status in ("failed", "failed_before_publish")
    standalone_warming = target_post.get("type") == "warming"
    prep_mode = target_exec.get("preparation_mode") or "off"
    effective_status = "pending" if is_manual_retry else current_status
    effective_preparation_status = (
        "pending"
        if is_manual_retry and prep_mode != "off"
        else target_exec.get("preparation_status")
    )
    needs_prep = (
        not standalone_warming
        and effective_status == "pending"
        and prep_mode != "off"
        and effective_preparation_status == "pending"
    )

    owner_batch_id = active_batch_id(queue)
    target_batch = match.get("batch") or {}
    target_batch_id = target_batch.get("batch_id")
    if owner_batch_id and target_batch_id and owner_batch_id != target_batch_id:
        raise HTTPException(
            status_code=409,
            detail="Another batch is already running. This batch will start after the active batch finishes.",
        )
    if target_batch and batch_has_review_hold(target_batch):
        raise HTTPException(status_code=409, detail="This batch is paused until its uncertain execution is resolved.")
    if not needs_prep and target_batch:
        iteration_check = batch_iteration_availability(target_batch)
        if not iteration_check["allowed"]:
            raise HTTPException(
                status_code=409,
                detail="The previous batch iteration is still running or its delay has not finished.",
            )

    scheduler_cfg = get_scheduler_config()
    if needs_prep and count_buffered_preparations(queue, execution_id) >= scheduler_cfg["max_preparers"]:
        raise HTTPException(status_code=409, detail="All preparation buffers are currently occupied.")

    pid = target_exec["profile_id"]
    if get_container_status(pid) != "running" and count_running_profile_containers() >= scheduler_cfg["max_active_profile_containers"]:
        raise HTTPException(status_code=409, detail="The active profile-container limit has been reached.")

    if get_container_status(pid) != "running":
        admission = claim_container_start_admission()
        if not admission["allowed"]:
            raise HTTPException(status_code=409, detail=f"Container start delayed: {admission['reason']}.")

    # Persist a retry transition only after every admission check succeeds. This
    # prevents a rejected run-now request from leaving an execution runnable.
    if is_manual_retry:
        now_iso = datetime.now(timezone.utc).isoformat()
        target_exec["status"] = "pending"
        target_exec["stage"] = "pending"
        target_exec["error"] = None
        if prep_mode != "off":
            target_exec["preparation_status"] = "pending"
        history = target_exec.get("stage_history")
        if not isinstance(history, list):
            history = []
            target_exec["stage_history"] = history
        history.append({"stage": "pending", "timestamp": now_iso, "reason": "manual_retry"})
        save_posting_queue(queue)

    slot_kind = "preparer" if (needs_prep or standalone_warming) else "publisher"
    if needs_prep:
        asyncio.create_task(execute_queue_preparation(execution_id))
    else:
        asyncio.create_task(execute_queue_item(execution_id, slot_kind))

    msg = (
        f"Started passive preparation for execution {execution_id}"
        if needs_prep
        else (f"Dispatched feed warming {execution_id} immediately" if standalone_warming else f"Dispatched execution {execution_id} immediately")
    )
    return {"success": True, "message": msg}

@router.post("/resolve-uncertain/{execution_id}")
def resolve_uncertain(execution_id: str, payload: dict = Body(...)):
    resolution = payload.get("resolution")
    note = payload.get("note")
    post_url = payload.get("post_url")

    if resolution not in ("published", "not_published"):
        raise HTTPException(status_code=400, detail="Resolution must be either 'published' or 'not_published'")

    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    target_exec = match["execution"]
    target_post = match["post"]

    if target_exec.get("status") not in ("uncertain", "needs_review"):
        raise HTTPException(status_code=400, detail=f"Execution is in '{target_exec.get('status')}' state, not 'uncertain' or 'needs_review'")

    validated_post_url = None
    if resolution == "published" and post_url and isinstance(post_url, str) and post_url.strip():
        validated_post_url = validate_facebook_permalink(post_url, "reel" if target_post.get("type") == "reel" else "post")
        if not validated_post_url:
            raise HTTPException(status_code=400, detail="The supplied URL is not a recognized Facebook post or reel permalink.")

    reviewed_at = datetime.now(timezone.utc).isoformat()
    history = target_exec.get("stage_history")
    if not isinstance(history, list):
        history = []
        target_exec["stage_history"] = history

    if resolution == "published":
        target_exec["status"] = "published"
        target_exec["stage"] = "published"
        target_exec["published_at"] = reviewed_at
        target_exec["review_status"] = "resolved_published"
        target_exec["review_note"] = note or "Manually confirmed published on Facebook"
        target_exec["error"] = None
        if validated_post_url:
            target_exec["post_url"] = validated_post_url
            target_exec["post_url_verified_at"] = reviewed_at
            target_exec["post_match_confidence"] = 1.0
    else:
        target_exec["status"] = "failed_before_publish"
        target_exec["stage"] = "failed_before_publish"
        target_exec["review_status"] = "resolved_not_published"
        target_exec["review_note"] = note or "Manually confirmed NOT published on Facebook"

    history.append({
        "stage": target_exec["stage"],
        "timestamp": reviewed_at,
        "reason": f"operator_resolved_{resolution}",
    })
    target_exec["stage_updated_at"] = reviewed_at
    target_exec["reviewed_at"] = reviewed_at
    save_posting_queue(queue)

    return {"success": True, "message": f"Execution resolved as {resolution}", "execution": target_exec}

@router.post("/backfill-permalink/{execution_id}")
def backfill_permalink(execution_id: str, payload: dict = Body(...)):
    post_url = payload.get("post_url")
    note = payload.get("note")
    match_confidence = payload.get("match_confidence")
    source = payload.get("source") or "manual_backfill"

    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    try:
        apply_verified_permalink_backfill(
            match["execution"],
            post_url,
            "reel" if match["post"].get("type") == "reel" else "post",
            {"note": note, "matchConfidence": match_confidence, "source": source},
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))

    save_posting_queue(queue)
    return {"success": True, "message": "Verified permalink attached to published execution", "execution": match["execution"]}

@router.post("/backfill-comment/{execution_id}")
def backfill_comment(execution_id: str, payload: dict = Body(...)):
    status = payload.get("status")
    note = payload.get("note")
    evidence_dir = payload.get("evidence_dir")
    source = payload.get("source") or "manual_backfill"

    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    try:
        apply_comment_evidence_backfill(
            match["execution"],
            status,
            {"note": note, "evidenceDir": evidence_dir, "source": source},
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))

    save_posting_queue(queue)
    return {"success": True, "message": "Comment evidence attached to published execution", "execution": match["execution"]}

@router.post("/retry-comment/{execution_id}")
async def retry_comment(execution_id: str):
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    comment_text = match["execution"].get("first_comment") or match["post"].get("first_comment") or ""
    if not can_retry_first_comment(match["execution"], comment_text):
        raise HTTPException(status_code=400, detail="Comment retry is allowed only after a definite pre-submission input failure.")

    pid = match["execution"]["profile_id"]
    from backend.services.automation_service import is_task_process_active, active_automation_tasks
    if is_task_process_active(active_automation_tasks.get(pid)):
        raise HTTPException(status_code=409, detail=f"Automation is already running for profile '{pid}'")

    scheduler_cfg = get_scheduler_config()
    slot_check = can_acquire_scheduler_slot(queue, active_in_memory_scheduler_leases(), "publisher", pid, scheduler_cfg)
    if not slot_check["allowed"]:
        raise HTTPException(status_code=409, detail=f"Scheduler capacity unavailable: {slot_check['reason']}")

    if get_container_status(pid) != "running" and count_running_profile_containers() >= scheduler_cfg["max_active_profile_containers"]:
        raise HTTPException(status_code=409, detail="The active profile-container limit has been reached.")

    start_admission_claimed = False
    if get_container_status(pid) != "running":
        admission = claim_container_start_admission()
        if not admission["allowed"]:
            raise HTTPException(status_code=409, detail=f"Container start delayed: {admission['reason']}.")
        start_admission_claimed = True

    asyncio.create_task(execute_queue_comment_retry(execution_id, start_admission_claimed))
    return {"success": True, "message": f"Started comment-only retry for execution {execution_id}"}
