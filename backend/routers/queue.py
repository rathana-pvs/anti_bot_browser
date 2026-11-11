from automation.warming_options import normalize_warming_options
from automation.live_encoder import ENCODER_FAILURE_REASONS

import asyncio
import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from fastapi.responses import FileResponse

from backend.config import PROFILES_DIR
from backend.services import active_hours, android_jobs, batch_edit, posting_plan, queue_eta
from backend.services.execution_evidence import resolve_execution_evidence
from backend.services.caption_hook import prepare_scheduled_post
from backend.services.website_service import snapshot_post, execution_variant
from backend.services.queue_service import (
    load_posting_queue,
    save_posting_queue,
    find_queue_execution,
    generate_spun_caption,
    with_queue_claim_lock,
    DETAIL_FIELDS,
    execution_details,
)
from backend.services.queue_safety import (
    is_execution_deletion_locked,
    validate_facebook_permalink,
    apply_verified_permalink_backfill,
    apply_comment_evidence_backfill,
    can_retry_first_comment,
)
from backend.services.queue_scheduler import (
    compute_execution_schedule,
    SPREAD_CHOICES_MINUTES,
    spread_offsets_ms,
    can_acquire_scheduler_slot,
    preparation_has_room,
    scheduler_snapshot,
)
from backend.services.profile_pipeline import (
    shuffled_profile_order,
    count_buffered_preparations,
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
    cancel_all_local_posts,
)

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.post("/network-recovery/{execution_id}/cancel")
async def cancel_recovery(execution_id: str):
    from backend.services.queue_network_recovery import cancel_network_recovery
    try:
        return await cancel_network_recovery(execution_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Execution not found") from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/cancel-all")
async def cancel_all_posts():
    try:
        return await cancel_all_local_posts()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch_posts(batch_id: str):
    try:
        return await cancel_all_local_posts(batch_id=batch_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Batch not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

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
                    "title": post.get("title"),
                    "pinned_comment": exec_item.get("pinned_comment", post.get("pinned_comment")),
                    "end_after_comment": exec_item.get("end_after_comment", post.get("end_after_comment", False)),
                    "first_comment": exec_item.get("first_comment", post.get("first_comment")),
                    "scrolls": post.get("scrolls"),
                    "warming_options": post.get("warming_options"),
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
        "failed": len([e for e in all_executions if e.get("status") in ("failed", "failed_before_publish", "failed_after_publish")]),
        "uncertain": len([e for e in all_executions if e.get("status") in ("uncertain", "needs_review")]),
        "skipped": len([e for e in all_executions if e.get("status") and e["status"].startswith("skipped")]),
    }

    scheduler = current_scheduler_snapshot(queue)
    try:
        busy = (scheduler.get("available") or {}).get("total", 1) <= 0
        timing = queue_eta.explain(queue, time.time() * 1000, busy=busy)
    except Exception as error:  # an estimate must never break the queue view
        print(f"[Queue] Could not estimate start times: {error}")
        timing = {}
    for item in all_executions:
        if item.get("execution_id") in timing:
            item.update(timing[item["execution_id"]])
    telemetry_summary = build_queue_telemetry_summary(all_executions)
    # Logs and telemetry were most of a 14 MB response sent every few seconds;
    # the detail drawer reads them per item from /execution/{id}/details.
    return {
        "queue_version": queue.get("queue_version", "2.0"),
        "stats": stats,
        "scheduler": scheduler,
        "telemetry_summary": telemetry_summary,
        "batches": [_batch_without_details(batch) for batch in queue.get("daily_batches", [])],
        "executions": [_without_details(item) for item in all_executions],
    }


def _without_details(execution: dict) -> dict:
    if not any(key in execution for key in DETAIL_FIELDS):
        return execution
    kept = {key: value for key, value in execution.items() if key not in DETAIL_FIELDS}
    if "logs" in execution:
        kept["log_count"] = len(execution.get("logs") or [])
    return kept


def _batch_without_details(batch: dict) -> dict:
    return {**batch, "posts": [{**post, "executions": [_without_details(item) for item in post.get("executions", [])]}
                               for post in batch.get("posts", [])]}


@router.get("/execution/{execution_id}/details")
def get_execution_details(execution_id: str):
    """One item's logs and telemetry, which the queue list leaves out."""
    match = find_queue_execution(load_posting_queue(), execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")
    details = execution_details(match["execution"])
    return {"execution_id": execution_id, **{key: details.get(key) for key in DETAIL_FIELDS}}

@router.post("/schedule-preview")
def schedule_preview(payload: dict = Body(...)):
    """What the chosen timing means for each selected account before the batch is made: its
    local time and waking hours, and for Follow active hours the times the plan would pick.
    Passing the returned plan_seed when creating the batch gives the same plan while nothing
    else in the queue changed."""
    profile_ids = payload.get("profile_ids")
    if not isinstance(profile_ids, list) or not all(isinstance(pid, str) for pid in profile_ids) or len(profile_ids) > 500:
        raise HTTPException(status_code=400, detail="profile_ids must be a list of profile ids")
    window = payload.get("schedule_window") or {}
    try:
        choice = parse_schedule_choice(window, window.get("start_now") is True)
        posts_each = int(payload.get("posts_per_account") or 1)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    posts_each = max(1, min(posts_each, 50))
    seed = random.randrange(1, 2**31)
    now_ms = time.time() * 1000
    start_ms = choice["start_at"].timestamp() * 1000 if choice["start_at"] else now_ms
    end_ms = start_ms + max(0, choice["spread_minutes"] or 0) * 60_000
    queue = load_posting_queue() if choice["timing"] == "auto" else None
    # Editing a batch: its own jobs that haven't started are the ones being planned again.
    replanned: dict[str, set] = {}
    if queue is not None and isinstance(payload.get("exclude_batch_id"), str):
        for batch in queue.get("daily_batches", []):
            if batch.get("batch_id") == payload["exclude_batch_id"]:
                for post in batch.get("posts", []):
                    for execution in post.get("executions", []):
                        if batch_edit.can_change(execution):
                            replanned.setdefault(execution.get("profile_id"), set()).add(execution["execution_id"])
    plan = posting_plan.settings()
    cache: dict = {}
    accounts = []
    for pid in dict.fromkeys(profile_ids):
        if not (PROFILES_DIR / pid / "config.json").is_file():
            continue
        config = active_hours._profile_config(pid, cache)
        hours = active_hours.waking_hours(config)
        entry = {
            "profile_id": pid,
            "name": config.get("name") or pid,
            "timezone": getattr(active_hours.account_zone(config), "key", None),
            "awake_hours": [hours[0], (hours[0] + hours[1]) % 24] if hours else None,
            "awake_at_start": active_hours.within_active_hours(config, start_ms),
            "awake_at_end": active_hours.within_active_hours(config, end_ms),
        }
        if choice["timing"] == "auto":
            try:
                times = plan_account(queue, pid, config, now_ms, posts_each, seed, plan, replanned.get(pid))
                entry["planned_at"] = [datetime.fromtimestamp(at / 1000, tz=timezone.utc).isoformat() for at in times]
            except posting_plan.NoRoom as error:
                entry["planned_at"], entry["error"] = [], str(error)
        accounts.append(entry)
    return {"timing": choice["timing"], "plan_seed": seed, "plan": plan,
            "start_at": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
            "end_at": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(), "accounts": accounts}


def plan_account(queue, profile_id, config, now_ms, count, seed, plan, exclude=None) -> list[float]:
    """`count` auto-planned times for one account, each kept apart from the ones before it.
    `exclude` leaves the account's jobs that are being planned again out of the way."""
    rand = random.Random(f"{seed}:{profile_id}")
    times: list[float] = []
    for _ in range(count):
        times.append(posting_plan.plan_time(queue, profile_id, config, now_ms, rand, plan, extra=times, exclude=exclude))
    return sorted(times)


MAX_SCHEDULE_AHEAD_DAYS = 30
# A start a moment in the past (the form was open a while) still counts as "now".
PAST_START_GRACE_SECONDS = 120
TIMINGS = ("now", "auto", "exact")


def parse_schedule_choice(schedule_window: dict, start_now: bool, now: datetime | None = None) -> dict:
    """How the batch is timed: {timing, start_at, spread_minutes, follow_active_hours}.

    - "now": every account starts right away, whatever the hour.
    - "auto" (Schedule with Follow active hours): no time is entered; each account's posts are
      planned inside its own waking day, with the gap and daily limit.
    - "exact" (Schedule without it): the full date and time picked on this PC (ISO with offset),
      for every account. A time that has passed is refused instead of running at once.
    An older client sends none of these: timing is None and the old fixed gap is used.
    """
    now = now or datetime.now(timezone.utc)
    timing = schedule_window.get("timing")
    if timing is not None and timing not in TIMINGS:
        raise ValueError("timing must be now, auto or exact")
    if start_now:
        timing = "now"
    elif timing == "now":
        start_now = True

    spread_minutes = schedule_window.get("spread_minutes")
    if spread_minutes is not None:
        if isinstance(spread_minutes, bool) or not isinstance(spread_minutes, int) or spread_minutes not in SPREAD_CHOICES_MINUTES:
            raise ValueError(f"spread_minutes must be one of {', '.join(map(str, SPREAD_CHOICES_MINUTES))}")

    if timing == "auto":
        return {"timing": "auto", "start_at": None, "spread_minutes": None, "follow_active_hours": True}

    start_at = None
    raw = schedule_window.get("start_at")
    if timing == "exact" or (not start_now and raw):
        if not raw:
            raise ValueError("Pick the date and time to start.")
        try:
            start_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("The start time is not a valid date and time.") from None
        if start_at.tzinfo is None:
            raise ValueError("The start time needs a timezone offset.")
        if (now - start_at).total_seconds() > PAST_START_GRACE_SECONDS:
            raise ValueError("That start time has already passed. Pick a later time or use Start now.")
        if (start_at - now).days >= MAX_SCHEDULE_AHEAD_DAYS:
            raise ValueError(f"Schedule at most {MAX_SCHEDULE_AHEAD_DAYS} days ahead.")
        timing = "exact"
    # An exact time is followed as given; "now" never waits either.
    follow = timing is None
    return {"timing": timing, "start_at": start_at, "spread_minutes": spread_minutes, "follow_active_hours": follow}


@router.post("/batch")
async def create_batch(payload: dict = Body(...)):
    name = payload.get("name")
    target_profiles = payload.get("target_profiles")
    schedule_window = payload.get("schedule_window") or {}
    posts = payload.get("posts")
    if isinstance(posts, list):
        if any(isinstance(post, dict) and post.get('destination_variants') for post in posts):
            raise HTTPException(status_code=400, detail='Destination variants must come from a reviewed Read more preparation.')
        try:
            posts = [snapshot_post(post, target_profiles or []) if isinstance(post, dict) else post for post in posts]
        except (ValueError, TypeError, AttributeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        try:
            posts = [prepare_scheduled_post(post) if isinstance(post, dict) else post for post in posts]
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail="Caption hook approval is invalid or stale; preview again") from None

    if not isinstance(target_profiles, list) or len(target_profiles) == 0:
        raise HTTPException(status_code=400, detail="Please select at least one target profile.")
    if not isinstance(posts, list) or len(posts) == 0:
        raise HTTPException(status_code=400, detail="Please provide at least one post item.")

    for post in posts:
        if not isinstance(post, dict):
            raise HTTPException(status_code=400, detail="Each post must be an object")
        if post.get("type") == "warming":
            try:
                post["warming_options"] = normalize_warming_options(post.get("warming_options"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    if any(post.get("type") == "live" for post in posts):
        from backend.services import live_service
        try:
            if len(set(target_profiles)) != len(target_profiles):
                raise ValueError('Select each profile only once for a Live batch')
            for pid in target_profiles:
                live_service.launch_settings(pid)
            # The actual copied destination is locked at execution time. Cached
            # keys cannot identify the destination of a newly prepared broadcast.
            for post in posts:
                if post.get("type") == "live":
                    post.update(live_service.validate_playback(post.get("loop", False), post.get("max_duration_seconds"), post.get("muted", False), post.get("playback_mode", "auto")))
                    live_service.resolve_video(post.get("media_file"))
                    details = live_service.validate_details(post.get("title", ""), post.get("base_caption", ""), post.get("pinned_comment"))
                    post.update(title=details["title"], base_caption=details["caption"], pinned_comment=details.get("pinned_comment"), ai_spin=False)
                    post['end_after_comment'] = live_service.validate_end_after_comment(post.get('end_after_comment', False), details.get('pinned_comment'))
                    if post.get("first_comment"):
                        raise ValueError("First comments are not supported for Live")
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=f"Live setup required: {error}") from error

    queue = load_posting_queue()
    preparation_ids = [(post.get('read_more') or {}).get('preparation_id') for post in posts if post.get('read_more')]
    if len(set(preparation_ids)) != len(preparation_ids):
        raise HTTPException(status_code=400, detail='Prepare separate Read more links for each post item.')
    request_fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if preparation_ids else None
    batch_id = ('batch_web_' + hashlib.sha256(json.dumps(preparation_ids).encode()).hexdigest()[:32]
                if preparation_ids else f"batch_{int(time.time() * 1000)}")
    existing = next((batch for batch in queue.get('daily_batches', []) if batch.get('batch_id') == batch_id), None)
    if existing:
        if existing.get('request_fingerprint') != request_fingerprint:
            raise HTTPException(status_code=409, detail='This Read more preparation already belongs to a batch. Prepare a new posting job.')
        return {'success': True, 'batch_id': batch_id, 'accepted_profiles': existing['target_profiles'],
                'skipped_profiles': [], 'total_executions': sum(len(post['executions']) for post in existing['posts']), 'batch': existing}

    used_preparations = {post.get('website_preparation_id') for batch in queue.get('daily_batches', []) for post in batch.get('posts', [])}
    if any(preparation_id in used_preparations for preparation_id in preparation_ids):
        raise HTTPException(status_code=409, detail='These comment links already belong to a posting job. Prepare new Read more links.')
    start_time_str = schedule_window.get("start_time") or "09:00"
    end_time_str = schedule_window.get("end_time") or "21:00"

    raw_stagger = schedule_window.get("profile_stagger_seconds")
    legacy_stagger = schedule_window.get("profile_stagger_minutes")
    if raw_stagger is not None:
        try:
            stagger_seconds = max(10, int(raw_stagger))
        except ValueError:
            stagger_seconds = 60
    elif legacy_stagger is not None:
        try:
            stagger_seconds = max(10, int(legacy_stagger) * 60)
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

    try:
        choice = parse_schedule_choice(
            schedule_window, payload.get("start_now") is True or schedule_window.get("start_now") is True)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    timing, start_at, spread_minutes, follow_active_hours = (
        choice["timing"], choice["start_at"], choice["spread_minutes"], choice["follow_active_hours"])
    plan_seed = schedule_window.get("plan_seed")
    if not isinstance(plan_seed, int) or isinstance(plan_seed, bool):
        plan_seed = random.randrange(1, 2**31)

    accepted_profiles = []
    skipped_profiles = []

    for pid in target_profiles:
        cfg_path = PROFILES_DIR / pid / "config.json"
        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if cfg.get("automation") and cfg["automation"].get("enabled") is False and not android_jobs.accepts_automation(cfg):
                    skipped_profiles.append({"profile_id": pid, "reason": cfg["automation"].get("reason") or "manual_control_only"})
                    continue
            except Exception:
                pass
        accepted_profiles.append(pid)

    phone_profiles = {pid for pid in accepted_profiles if android_jobs.phone_of(pid)}

    if not accepted_profiles:
        raise HTTPException(status_code=400, detail={
            "error": "All selected profiles have automation disabled in their config.json.",
            "skipped": skipped_profiles,
        })

    is_start_now = timing == "now" or payload.get("start_now") is True or schedule_window.get("start_now") is True

    previous_batch = (queue.get("daily_batches") or [None])[0]
    previous_order = (previous_batch.get("profile_execution_order") or previous_batch.get("target_profiles") or []) if previous_batch else []
    previous_last = previous_order[-1] if previous_order else None
    profile_execution_order = shuffled_profile_order(accepted_profiles, previous_last)

    posting_orders = {}
    for pid in accepted_profiles:
        order = list(range(len(posts)))
        posting_orders[pid] = order

    spread_offsets = spread_offsets_ms(len(profile_execution_order), spread_minutes) if spread_minutes is not None else None
    # Follow active hours: each account's posts get their own planned times (gap and daily limit).
    planned = {}
    if timing == "auto":
        plan, plan_cache, plan_now = posting_plan.settings(), {}, time.time() * 1000
        for pid in profile_execution_order:
            try:
                planned[pid] = plan_account(queue, pid, active_hours._profile_config(pid, plan_cache), plan_now,
                                            len(posts), plan_seed, plan)
            except posting_plan.NoRoom as error:
                raise HTTPException(status_code=409, detail=f"{pid}: {error}") from None

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
    elif start_at:
        window_start = start_at.astimezone().replace(tzinfo=None)
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
        item_preparation_mode = "off" if post.get("type") == "live" else preparation_mode
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
            if timing == "auto":
                sched_ms = planned[pid][slot_idx]
            elif spread_offsets is not None:
                # The account's own start: the chosen moment (or now) plus its random place in the spread.
                start_ms = time.time() * 1000 if is_start_now else max(time.time() * 1000, start_at.timestamp() * 1000)
                sched_ms = start_ms + spread_offsets[p_idx]
            sched_dt = datetime.fromtimestamp(sched_ms / 1000, tz=timezone.utc)

            spun_caption = generate_spun_caption(post.get("base_caption", ""), p_idx, pid) if post.get("ai_spin") is not False else post.get("base_caption", "")

            executions.append({
                "execution_id": f"exec_{int(time.time() * 1000)}_{p_idx}_{post_idx}",
                "profile_id": pid,
                "scheduled_at": sched_dt.isoformat(),
                "batch_iteration_index": slot_idx * len(profile_execution_order) + p_idx,
                "spun_caption": spun_caption,
                **execution_variant(post, pid),
                "status": "pending",
                "stage": "pending",
                "stage_history": [{"stage": "pending", "timestamp": datetime.now(timezone.utc).isoformat()}],
                # Phone accounts have no passive browsing session to prepare.
                "preparation_mode": "off" if pid in phone_profiles else item_preparation_mode,
                "preparation_status": "not_requested" if item_preparation_mode == "off" or pid in phone_profiles else "pending",
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
            "website_preparation_id": (post.get("read_more") or {}).get("preparation_id"),
            "hook_preparation": post.get("hook_preparation"),
            "first_comment": post.get("first_comment"),
            "scrolls": max(1, int(post.get("scrolls", 4))),
            "warming_options": post.get("warming_options"),
            **({"title": post.get("title", ""), "pinned_comment": post.get("pinned_comment"), "end_after_comment": post.get("end_after_comment", False), "loop": post.get("loop", False), "muted": post.get("muted", False), "max_duration_seconds": post.get("max_duration_seconds"), "playback_mode": post.get("playback_mode", "auto")} if post.get("type") == "live" else {}),
            "ai_spin": post.get("ai_spin") is not False,
            "executions": executions,
        })

    new_batch = {
        "request_fingerprint": request_fingerprint,
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
            "execution_flow": "resource_bounded",
            "follow_active_hours": follow_active_hours,
            **({"timing": timing} if timing else {}),
            **({"start_at": start_at.isoformat()} if start_at else {}),
            **({"spread_minutes": spread_minutes} if spread_minutes is not None else {}),
        },
        "posting_order_per_profile": posting_orders,
        "posts": formatted_posts,
    }

    if "daily_batches" not in queue:
        queue["daily_batches"] = []
    queue["daily_batches"].insert(0, new_batch)
    if not save_posting_queue(queue):
        raise HTTPException(status_code=500, detail="Could not persist the batch")

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
    if isinstance(posts, list):
        try:
            posts = [prepare_scheduled_post(post) if isinstance(post, dict) else post for post in posts]
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail="Caption hook approval is invalid or stale; preview again") from None
    if not isinstance(posts, list) or not posts:
        raise HTTPException(status_code=400, detail="Please provide at least one post item.")
    for post in posts:
        if not isinstance(post, dict) or (not str(post.get("media_file") or "").strip() and not str(post.get("base_caption") or "").strip()):
            raise HTTPException(status_code=400, detail="Each appended post needs media or a caption.")
        if post.get("type", "photo") not in ("photo", "reel"):
            raise HTTPException(status_code=400, detail="Appended post type must be 'photo' or 'reel'.")

    if any(post.get('read_more') for post in posts):
        raise HTTPException(status_code=400, detail='Create a new batch for prepared Read more posts.')

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
        # With a spread, each account keeps its own start for the posts added later.
        own_start = {}
        if (batch.get("schedule_window") or {}).get("spread_minutes") is not None:
            for execution in existing_executions:
                try:
                    at = datetime.fromisoformat(str(execution.get("scheduled_at") or "").replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    continue
                pid = execution.get("profile_id")
                own_start[pid] = min(own_start.get(pid, at), at)
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
                    "scheduled_at": own_start.get(profile_id, batch_start).isoformat(),
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
                "hook_preparation": post.get("hook_preparation"),
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

def _find_batch(queue, batch_id):
    batch = next((item for item in queue.get("daily_batches", []) if item.get("batch_id") == batch_id), None)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


def _dispatch_soon():
    try:
        asyncio.get_running_loop().create_task(dispatch_pending_queue())
    except RuntimeError:
        pass


def _set_paused(batch_id: str, paused: bool):
    def change():
        queue = load_posting_queue()
        result = batch_edit.set_paused(_find_batch(queue, batch_id), paused)
        if not save_posting_queue(queue):
            raise HTTPException(status_code=500, detail="Could not save the batch")
        return result
    return with_queue_claim_lock(change)


@router.post("/batch/{batch_id}/pause")
async def pause_batch(batch_id: str):
    """Nothing new in the batch starts until it is resumed; jobs already working finish."""
    return _set_paused(batch_id, True)


@router.post("/batch/{batch_id}/resume")
async def resume_batch(batch_id: str):
    result = _set_paused(batch_id, False)
    _dispatch_soon()
    return result


@router.get("/batch/{batch_id}/edit")
def batch_edit_info(batch_id: str):
    """What the edit form needs: the batch and which of its jobs can still change."""
    queue = load_posting_queue()
    batch = _find_batch(queue, batch_id)
    locked = {post.get("post_id"): batch_edit.content_locked(post) for post in batch.get("posts", [])}
    return {"batch": batch, **batch_edit.summary(batch), "content_locked": locked}


@router.put("/batch/{batch_id}")
async def update_batch(batch_id: str, payload: dict = Body(...)):
    """Edits the jobs that haven't started. `expected_ids` (the changeable jobs the form showed)
    lets the answer name the ones that started while the form was open; they keep what they had."""
    window = payload.get("schedule_window")
    choice = None
    if window is not None:
        if not isinstance(window, dict):
            raise HTTPException(status_code=400, detail="schedule_window must be an object")
        try:
            choice = parse_schedule_choice(window, window.get("start_now") is True)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        if choice["timing"] is None:
            raise HTTPException(status_code=400, detail="Choose Start now, Follow active hours or a start time.")
    seed = window.get("plan_seed") if window else None
    if not isinstance(seed, int) or isinstance(seed, bool):
        seed = random.randrange(1, 2**31)
    expected = payload.get("expected_ids") or []

    def change():
        queue = load_posting_queue()
        batch = _find_batch(queue, batch_id)
        plan, cache, now_ms = posting_plan.settings(), {}, time.time() * 1000

        def plan_times(pid, count, exclude):
            return plan_account(queue, pid, active_hours._profile_config(pid, cache), now_ms, count, seed, plan, exclude)

        try:
            result = batch_edit.apply(batch, payload, choice=choice, plan_times=plan_times, offsets=spread_offsets_ms)
        except batch_edit.EditError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except posting_plan.NoRoom as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        if result["changes"] and not save_posting_queue(queue):
            raise HTTPException(status_code=500, detail="Could not save the batch")
        still = set(batch_edit.summary(batch)["changeable_ids"])
        names = {e["execution_id"]: e.get("profile_id") for p in batch.get("posts", []) for e in p.get("executions", [])}
        started = [{"execution_id": eid, "profile_id": names.get(eid)} for eid in expected
                   if isinstance(eid, str) and eid not in still]
        return {"success": True, **result, "started_while_editing": started, "batch": batch}

    result = with_queue_claim_lock(change)
    _dispatch_soon()
    return result


@router.put("/execution/{execution_id}")
async def update_execution(execution_id: str, payload: dict = Body(...)):
    """One account's own start time (`scheduled_at`, ISO with offset) or caption."""
    scheduled_at = None
    if payload.get("scheduled_at") is not None:
        try:
            choice = parse_schedule_choice({"timing": "exact", "start_at": payload["scheduled_at"]}, False)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        scheduled_at = choice["start_at"]
    caption = payload.get("caption")
    if caption is not None and not isinstance(caption, str):
        raise HTTPException(status_code=400, detail="caption must be text")

    def change():
        queue = load_posting_queue()
        match = find_queue_execution(queue, execution_id)
        if not match:
            raise HTTPException(status_code=404, detail="Execution not found")
        try:
            labels = batch_edit.edit_job(match["execution"], match["post"], scheduled_at=scheduled_at, caption=caption)
        except batch_edit.EditError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        if labels and not save_posting_queue(queue):
            raise HTTPException(status_code=500, detail="Could not save the job")
        return {"success": True, "changes": labels, "execution": match["execution"]}

    result = with_queue_claim_lock(change)
    _dispatch_soon()
    return result


@router.delete("/batch/{batch_id}")
def delete_batch(batch_id: str):
    def remove():
        queue = load_posting_queue()
        batches = queue.get("daily_batches", [])
        if not any(b.get("batch_id") == batch_id for b in batches):
            raise HTTPException(status_code=404, detail="Batch not found")
        # Explicit deletion removes queue history regardless of outcome. It
        # never resolves publication, retries work or releases profile locks.
        queue["daily_batches"] = [b for b in batches if b.get("batch_id") != batch_id]
        if not save_posting_queue(queue):
            raise HTTPException(status_code=500, detail="Could not save batch deletion")
        return {"success": True, "message": f"Batch {batch_id} removed"}
    return with_queue_claim_lock(remove)

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

@router.post("/batch/{batch_id}/rerun")
async def rerun_batch(batch_id: str):
    """Queue safe local retries together; the dispatcher applies normal capacity limits."""
    from backend.services.network_recovery import safe_to_retry, stop_recovery
    from backend.services.queue_safety import has_publication_evidence
    from backend.services import container_lifecycle

    def enqueue():
        queue = load_posting_queue()
        batch = next((b for b in queue.get("daily_batches", []) if b.get("batch_id") == batch_id), None)
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        queued = 0
        review = 0
        blocked = 0
        finished = 0
        owners = container_lifecycle.snapshot()
        now = datetime.now(timezone.utc).isoformat()
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                status = execution.get("status", "")
                if status in {"published", "completed"}:
                    finished += 1
                    continue
                # Schema v1 ownership lives in the lifecycle registry. Old
                # terminal queue rows may retain the flag after watchdog cleanup.
                owned = execution.get("automation_container_owned") and (
                    execution.get("container_owner_schema") != 1
                    or execution.get("profile_id") in owners
                )
                if (execution.get("cloud_attempt_id") or execution.get("container_cleanup_in_progress")
                        or owned or execution.get("profile_id") in owners
                        or status in {"running", "preparing"}):
                    blocked += 1
                    continue
                if status in {"uncertain", "needs_review", "failed_after_publish"} or has_publication_evidence(execution):
                    review += 1
                    continue
                if post.get("type") == "live":
                    from backend.services import live_service
                    prior = live_service.get_status(execution["profile_id"])
                    if (execution.get("broadcast_confirmed") or (
                            prior.get("status") == "needs_review"
                            and prior.get("queue_execution_id") == execution["execution_id"])):
                        review += 1
                        continue
                    # Only definite pre-stream failures or unstarted work may
                    # be retried together. A stopped broadcast may have aired.
                    if status not in {"failed_before_publish", "pending", "ready", "cancelled"}:
                        review += 1
                        continue
                eligible = (safe_to_retry(execution) or status.startswith("skipped_")
                            or status in {"cancelled", "stopped"})
                if not eligible:
                    continue
                stop_recovery(execution, "cancelled")
                execution["automation_container_owned"] = False
                execution.update(status="pending", stage="pending", error=None, scheduled_at=now)
                execution.pop("cancel_requested_at", None)
                if execution.get("preparation_mode", "off") != "off":
                    execution["preparation_status"] = "pending"
                history = execution.get("stage_history")
                if not isinstance(history, list):
                    history = execution["stage_history"] = []
                history.append({"stage": "pending", "timestamp": now, "reason": "batch_manual_retry"})
                queued += 1
        if queued and not save_posting_queue(queue):
            raise HTTPException(status_code=500, detail="Could not persist batch retries")
        return {"success": True, "queued": queued, "needs_review": review,
                "blocked": blocked, "already_finished": finished,
                "message": (f"Queued {queued} posts. {review} posts require publication review before retrying. "
                            f"{blocked} posts are active, cleaning up, or cloud-managed. "
                            f"{finished} posts are already finished.")}

    result = with_queue_claim_lock(enqueue)
    if result["queued"]:
        asyncio.create_task(dispatch_pending_queue())
    return result


@router.post("/run-now/{execution_id}")
async def run_execution_now(execution_id: str):
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    target_exec = match["execution"]
    if target_exec.get("cloud_attempt_id"):
        raise HTTPException(status_code=409, detail="Remote attempts are managed by the cloud; retries require a new cloud attempt.")
    target_post = match["post"]
    current_status = target_exec.get("status", "")
    if current_status in {"cancelled", "stopped"}:
        # Re-read and persist under the dispatcher lock so cancellation cleanup
        # or another retry cannot race this transition.
        def enqueue_interrupted():
            from backend.services import container_lifecycle
            from backend.services.network_recovery import stop_recovery
            from backend.services.queue_safety import has_publication_evidence

            queue = load_posting_queue()
            match = find_queue_execution(queue, execution_id)
            if not match:
                raise HTTPException(status_code=404, detail="Execution not found")
            execution = match["execution"]
            status = execution.get("status")
            if execution.get("cloud_attempt_id") or status not in {"cancelled", "stopped"}:
                raise HTTPException(status_code=409, detail="Execution is no longer available for a local retry")
            owners = container_lifecycle.snapshot()
            owned = execution.get("automation_container_owned") and (
                execution.get("container_owner_schema") != 1 or execution["profile_id"] in owners
            )
            if execution.get("container_cleanup_in_progress") or owned or execution["profile_id"] in owners:
                raise HTTPException(status_code=409, detail="Wait for the profile container to finish stopping before retrying")
            if has_publication_evidence(execution):
                raise HTTPException(status_code=409, detail="Publication outcome requires review before retrying")
            if match["post"].get("type") == "live":
                from backend.services import live_service
                prior = live_service.get_status(execution["profile_id"])
                if (status == "stopped" or execution.get("broadcast_confirmed") or (
                        prior.get("status") == "needs_review"
                        and prior.get("queue_execution_id") == execution_id)):
                    raise HTTPException(status_code=409, detail="Resolve the previous Live outcome before retrying")
            now = datetime.now(timezone.utc).isoformat()
            stop_recovery(execution, "cancelled")
            execution.update(status="pending", stage="pending", error=None, scheduled_at=now,
                             automation_container_owned=False)
            execution.pop("cancel_requested_at", None)
            if execution.get("preparation_mode", "off") != "off":
                execution["preparation_status"] = "pending"
            if not isinstance(execution.get("stage_history"), list):
                execution["stage_history"] = []
            execution["stage_history"].append({"stage": "pending", "timestamp": now, "reason": "manual_retry"})
            if not save_posting_queue(queue):
                raise HTTPException(status_code=500, detail="Could not persist retry")

        with_queue_claim_lock(enqueue_interrupted)
        asyncio.create_task(dispatch_pending_queue())
        return {"success": True, "queued": True, "message": "Retry queued for this item."}

    if target_post.get("type") == "live" and current_status in ("uncertain", "needs_review"):
        raise HTTPException(status_code=409, detail="Resolve the previous Live outcome and confirm it ended on Facebook before retrying")

    if target_post.get("type") == "live":
        from backend.services import live_service
        prior = live_service.get_status(target_exec["profile_id"])
        if prior.get("status") == "needs_review" and prior.get("queue_execution_id") == execution_id:
            raise HTTPException(status_code=409, detail="This same Live attempt has an unresolved outcome. Create a new Live job, or resolve this attempt before retrying it.")

    is_manual_retry = current_status.startswith("skipped_") or current_status in (
        "failed",
        "failed_before_publish",
        "failed_after_publish",
        "uncertain",
        "needs_review",
    )
    standalone_warming = target_post.get("type") == "warming"
    prep_mode = target_exec.get("preparation_mode") or "off"
    effective_status = "pending" if is_manual_retry else current_status
    effective_preparation_status = target_exec.get("preparation_status")
    if is_manual_retry and prep_mode != "off" and effective_preparation_status != "ready":
        effective_preparation_status = "pending"
    needs_prep = (
        not standalone_warming
        and effective_status == "pending"
        and prep_mode != "off"
        and effective_preparation_status == "pending"
    )

    target_batch = match.get("batch") or {}
    deferred_reason = None
    if target_batch:
        iteration_check = batch_iteration_availability(
            target_batch,
            target_execution=target_exec,
        )
        if not iteration_check["allowed"]:
            if is_manual_retry:
                deferred_reason = "waiting for this profile's previous iteration and delay"
            else:
                raise HTTPException(
                    status_code=409,
                    detail="This profile's previous iteration is still running or its delay has not finished.",
                )

    scheduler_cfg = get_scheduler_config()
    if needs_prep and not preparation_has_room(
        count_buffered_preparations(queue, execution_id),
        scheduler_snapshot(queue, active_in_memory_scheduler_leases(), scheduler_cfg)["active"]["posts"],
        scheduler_cfg,
    ):
        if is_manual_retry:
            deferred_reason = "waiting for a free job slot"
        else:
            raise HTTPException(status_code=409, detail="Every job slot is busy preparing or posting.")

    pid = target_exec["profile_id"]
    phone = android_jobs.phone_of(pid)  # a phone has no container; it waits only for the phone itself
    if phone and android_jobs.phone_busy(phone.get("device_serial")):
        if is_manual_retry:
            deferred_reason = "waiting for the phone to finish its current job"
        else:
            raise HTTPException(status_code=409, detail="The phone is running another job.")

    container_running = not phone and await asyncio.to_thread(get_container_status, pid) == "running"
    if not phone and not container_running and await asyncio.to_thread(count_running_profile_containers) >= scheduler_cfg["max_active_profile_containers"]:
        if is_manual_retry:
            deferred_reason = "waiting for a profile-container slot"
        else:
            raise HTTPException(status_code=409, detail="The active profile-container limit has been reached.")

    if not phone and not container_running and deferred_reason is None:
        admission = await asyncio.to_thread(claim_container_start_admission)
        if not admission["allowed"]:
            if is_manual_retry:
                deferred_reason = f"waiting for container admission ({admission['reason']})"
            else:
                raise HTTPException(status_code=409, detail=f"Container start delayed: {admission['reason']}.")

    # Persist a retry transition only after every admission check succeeds. This
    # prevents a rejected run-now request from leaving an execution runnable.
    if is_manual_retry:
        now_iso = datetime.now(timezone.utc).isoformat()
        from backend.services.network_recovery import stop_recovery
        stop_recovery(target_exec, "cancelled")
        target_exec["status"] = "pending"
        target_exec["stage"] = "pending"
        target_exec["error"] = None
        if prep_mode != "off" and effective_preparation_status != "ready":
            target_exec["preparation_status"] = "pending"
        history = target_exec.get("stage_history")
        if not isinstance(history, list):
            history = []
            target_exec["stage_history"] = history
        history.append({"stage": "pending", "timestamp": now_iso, "reason": "manual_retry"})
        save_posting_queue(queue)

    if deferred_reason:
        asyncio.create_task(dispatch_pending_queue())
        return {
            "success": True,
            "queued": True,
            "message": f"Retry queued for execution {execution_id}: {deferred_reason}.",
        }

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

    from backend.services.network_recovery import reviewable
    if target_exec.get("status") not in ("uncertain", "needs_review") and not reviewable(target_exec):
        raise HTTPException(status_code=400, detail=f"Execution is in '{target_exec.get('status')}' state, not 'uncertain' or 'needs_review'")

    validated_post_url = None
    if resolution == "published" and post_url and isinstance(post_url, str) and post_url.strip():
        validated_post_url = validate_facebook_permalink(post_url, "reel" if target_post.get("type") == "reel" else "post")
        if not validated_post_url:
            raise HTTPException(status_code=400, detail="The supplied URL is not a recognized Facebook post or reel permalink.")

    live_state = None
    if target_post.get("type") == "live":
        from backend.services import live_service
        pid = target_exec["profile_id"]
        if payload.get("confirmed_ended") is not True:
            raise HTTPException(status_code=400, detail="Confirm that the broadcast has ended on Facebook")
        if pid in live_service.CONTROLS or pid in live_service.lifecycle.snapshot():
            raise HTTPException(status_code=409, detail="Wait for the Live stream and profile container to stop")
        live_state = live_service.get_status(pid)

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
    if not save_posting_queue(queue):
        raise HTTPException(status_code=500, detail="Could not persist the review resolution")

    if live_state and live_state.get("queue_execution_id") == execution_id and live_state.get("status") == "needs_review":
        live_service.clear_review(target_exec["profile_id"], from_queue=True)

    return {"success": True, "message": f"Execution resolved as {resolution}", "execution": target_exec}

EVIDENCE_IMAGE_LIMIT_BYTES = 20 * 1024 * 1024


def _execution_evidence_dir(execution_id: str) -> Path | None:
    """Return the execution's evidence folder, only if it is inside its own profile's evidence root."""
    match = find_queue_execution(load_posting_queue(), execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")
    return resolve_execution_evidence(PROFILES_DIR, match["execution"], match["post"].get("type", ""))


@router.get("/execution/{execution_id}/evidence")
def list_execution_evidence(execution_id: str):
    directory = _execution_evidence_dir(execution_id)
    if directory is None:
        return {"images": []}
    # In the order they were taken, so a follow-up's screenshots (a phone's first comment) come last.
    shots = [path for path in directory.glob("*.png") if path.is_file() and not path.is_symlink()]
    images = [path.name for path in sorted(shots, key=lambda path: (path.stat().st_mtime, path.name))]
    response = {"images": images}
    diagnostic_file = directory / 'diagnostics.json'
    if diagnostic_file.is_file() and not diagnostic_file.is_symlink() and diagnostic_file.stat().st_size <= 16384:
        try:
            diagnostic = json.loads(diagnostic_file.read_text())
            # Only fixed diagnostic codes and a numeric exit code leave this endpoint.
            safe = {}
            stage = diagnostic.get('failure_stage')
            if stage in {'preparing', 'stream_start_intent', 'sending', 'starting', 'live', 'ending', 'commenting', 'stopping'}:
                safe['failure_stage'] = stage
            for key in ('failure_code', 'cleanup_failure_code'):
                code = diagnostic.get(key)
                if code in {'task_cancelled', 'action_timeout', 'workflow_error', 'unexpected_error',
                            'TypeError', 'KeyError', 'AttributeError', 'OSError'}:
                    safe[key] = code
            reason = diagnostic.get('encoder_failure_reason')
            if reason in ENCODER_FAILURE_REASONS:
                safe['encoder_failure_reason'] = reason
            code = diagnostic.get('encoder_exit_code')
            if type(code) is int:
                safe['encoder_exit_code'] = code
            state = diagnostic.get('final_browser_state')
            if state in {'live', 'ended', 'unconfirmed'}:
                safe['final_browser_state'] = state
            response['diagnostics'] = safe
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return response


@router.get("/execution/{execution_id}/evidence/{filename}")
def get_execution_evidence_image(execution_id: str, filename: str):
    directory = _execution_evidence_dir(execution_id)
    if directory is None:
        raise HTTPException(status_code=404, detail="No evidence for this execution")
    if Path(filename).name != filename or not filename.endswith(".png"):
        raise HTTPException(status_code=400, detail="Only PNG screenshots can be viewed")
    file = directory / filename
    if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(directory):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if file.stat().st_size > EVIDENCE_IMAGE_LIMIT_BYTES:
        raise HTTPException(status_code=413, detail="Screenshot is too large")
    return FileResponse(file, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


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

    if android_jobs.phone_of(pid):  # no container: the comment waits for the phone as a follow-up
        result = android_jobs.request_comment_retry(execution_id)
        asyncio.create_task(dispatch_pending_queue())
        return {**result, "message": "The first comment will be retried when the phone is free."}

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
