"""Editing a batch after it was made: pause and resume, its name, pacing, post content and timing.

Only jobs that have not started change. A job that is preparing, ready, running, posted or
unconfirmed keeps exactly what it ran with, so an edit can never post twice or rewrite the
record of something already on Facebook. The caller holds the queue claim lock, so a job the
dispatcher claims while the form is open is simply no longer pending here and is reported back.
"""

from datetime import datetime, timezone

from backend.services import android_jobs
from backend.services.queue_service import generate_spun_caption

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm")
PREPARATION_MODES = ("off", "brief", "extended")
MAX_NAME = 120
HISTORY_KEEP = 20


class EditError(ValueError):
    """The edit is refused; nothing was changed."""


def can_change(execution: dict) -> bool:
    return (execution.get("status") == "pending" and not execution.get("cloud_attempt_id")
            and not execution.get("container_cleanup_in_progress"))


def content_locked(post: dict) -> str | None:
    """Why this post's content can't be edited, or None."""
    if post.get("type") == "live":
        return "Live posts keep the details they were set up with."
    if post.get("type") == "warming":
        return "Warm-up visits have no content to edit."
    if post.get("website_preparation_id") or post.get("destination_variants"):
        return "Read more posts keep their prepared captions and links."
    return None


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat()


def _note(execution: dict, reason: str, now: datetime) -> None:
    execution.setdefault("stage_history", []).append({"stage": "pending", "timestamp": _iso(now), "reason": reason})


def summary(batch: dict) -> dict:
    """How many jobs can still change, for the edit form."""
    jobs = [execution for post in batch.get("posts", []) for execution in post.get("executions", [])]
    changeable = [execution["execution_id"] for execution in jobs if can_change(execution)]
    return {"total": len(jobs), "changeable": len(changeable), "changeable_ids": changeable}


def set_paused(batch: dict, paused: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if paused:
        batch["paused"], batch["paused_at"] = True, _iso(now)
    else:
        batch.pop("paused", None)
        batch.pop("paused_at", None)
    _remember(batch, now, ["Paused" if paused else "Resumed"], 0)
    batch["updated_at"] = _iso(now)
    return {"batch_id": batch.get("batch_id"), "paused": bool(batch.get("paused"))}


def _remember(batch: dict, now: datetime, changes: list[str], jobs: int) -> None:
    history = batch.setdefault("edit_history", [])
    history.append({"at": _iso(now), "changes": changes, "jobs": jobs})
    del history[:-HISTORY_KEEP]


def _profile_rank(batch: dict) -> dict:
    order = batch.get("profile_execution_order") or batch.get("target_profiles") or []
    return {pid: index for index, pid in enumerate(order)}


def _check_media(post_type: str, media: str) -> None:
    if post_type == "reel" and not media.lower().endswith(VIDEO_SUFFIXES):
        raise EditError("A reel needs a video file (.mp4, .mov or .webm).")


def _post_diff(post: dict, change: dict) -> tuple[dict, dict]:
    """The fields this change really alters, and the post's content after it. Raises EditError."""
    fields = {key: change[key] for key in ("base_caption", "first_comment", "media_file", "ai_spin") if key in change}
    for key in ("base_caption", "first_comment", "media_file"):
        if key in fields and fields[key] is not None and not isinstance(fields[key], str):
            raise EditError(f"{key} must be text.")
    if "ai_spin" in fields and not isinstance(fields["ai_spin"], bool):
        raise EditError("ai_spin must be true or false.")
    if "first_comment" in fields:
        fields["first_comment"] = (fields["first_comment"] or "").strip() or None
    if "media_file" in fields:
        fields["media_file"] = fields["media_file"] or ""
    current = {"base_caption": post.get("base_caption") or "", "first_comment": post.get("first_comment") or None,
               "media_file": post.get("media_file") or "", "ai_spin": post.get("ai_spin") is not False}
    fields = {key: value for key, value in fields.items() if value != current[key]}
    after = current | fields
    if fields:
        locked = content_locked(post)
        if locked:
            raise EditError(locked)
        if not str(after["media_file"]).strip() and not str(after["base_caption"]).strip():
            raise EditError("A post needs media or a caption.")
        if "media_file" in fields:
            _check_media(post.get("type", "photo"), after["media_file"])
    return fields, after


def _find_post(batch: dict, post_id) -> int:
    index = next((i for i, post in enumerate(batch.get("posts", [])) if post.get("post_id") == post_id), None)
    if index is None:
        raise EditError(f"Post {post_id} is not in this batch.")
    return index


def _edit_post(batch: dict, change: dict, now: datetime, ranks: dict) -> tuple[list[str], set[str]]:
    posts = batch.get("posts", [])
    index = _find_post(batch, change.get("post_id"))
    post = posts[index]
    fields, after = _post_diff(post, change)
    if not fields:
        return [], set()
    targets = [execution for execution in post.get("executions", []) if can_change(execution)]
    if not targets:
        return [], set()
    if len(targets) < len(post.get("executions", [])):
        # Some accounts already ran this post: they keep it as it was, and the rest move to
        # an edited copy right after it, so each account's post order stays the same.
        copies = sum(1 for other in posts if str(other.get("post_id", "")).startswith(f"{post.get('post_id')}_edit"))
        edited = {key: value for key, value in post.items() if key != "executions"}
        edited["post_id"] = f"{post.get('post_id')}_edit{copies + 1}"
        edited["edited_from"] = post.get("post_id")
        edited["executions"] = targets
        target_ids = {execution["execution_id"] for execution in targets}
        post["executions"] = [execution for execution in post["executions"] if execution["execution_id"] not in target_ids]
        posts.insert(index + 1, edited)
        post = edited

    post.update(fields)
    if "base_caption" in fields:
        post.pop("hook_preparation", None)  # the approved hook belonged to the old caption
    respin = "base_caption" in fields or "ai_spin" in fields
    for execution in targets:
        if respin:
            caption = after["base_caption"]
            execution["spun_caption"] = (generate_spun_caption(caption, ranks.get(execution.get("profile_id"), 0),
                                                               execution.get("profile_id"))
                                         if after["ai_spin"] else caption)
        if "first_comment" in fields:
            execution.pop("first_comment", None)  # the post's comment applies
        _note(execution, "content_edited", now)
    labels = {"base_caption": "caption", "first_comment": "first comment", "media_file": "media", "ai_spin": "AI spin"}
    name = f"post {index + 1}"
    return [f"{name}: {', '.join(labels[key] for key in fields)}"], {execution["execution_id"] for execution in targets}


def _retime(batch: dict, choice: dict, now: datetime, ranks: dict, plan_times, offsets) -> set[str]:
    """New start times for every job that hasn't started, from a parsed timing choice."""
    jobs = [execution for post in batch.get("posts", []) for execution in post.get("executions", [])
            if can_change(execution)]
    by_account: dict[str, list] = {}
    for execution in jobs:  # posts are in batch order, so each list is the account's post order
        by_account.setdefault(execution.get("profile_id"), []).append(execution)
    accounts = sorted(by_account, key=lambda pid: ranks.get(pid, len(ranks)))
    if choice["timing"] == "auto":
        for pid in accounts:
            items = by_account[pid]
            times = plan_times(pid, len(items), {execution["execution_id"] for execution in items})
            for execution, at in zip(items, sorted(times)):
                execution["scheduled_at"] = _iso(datetime.fromtimestamp(at / 1000, tz=timezone.utc))
    else:
        start = now if choice["timing"] == "now" else max(now, choice["start_at"])
        steps = offsets(len(accounts), choice["spread_minutes"] or 0)
        for pid, offset in zip(accounts, steps):
            at = _iso(datetime.fromtimestamp(start.timestamp() + offset / 1000, tz=timezone.utc))
            for execution in by_account[pid]:
                execution["scheduled_at"] = at
    for execution in jobs:
        execution.pop("time_set_by_user", None)
        _note(execution, "rescheduled", now)

    window = batch.setdefault("schedule_window", {})
    for key in ("start_at", "spread_minutes"):
        window.pop(key, None)
    window.update(timing=choice["timing"], start_now=choice["timing"] == "now",
                  follow_active_hours=choice["timing"] == "auto")
    if choice["timing"] != "auto":
        window["spread_minutes"] = choice["spread_minutes"] or 0  # every account keeps its own start
        if choice["start_at"]:
            window["start_at"] = choice["start_at"].isoformat()
    return {execution["execution_id"] for execution in jobs}


def apply(batch: dict, changes: dict, *, choice: dict | None = None, plan_times=None, offsets=None,
          now: datetime | None = None) -> dict:
    """Applies a batch edit in place. Raises EditError before touching anything it refuses."""
    now = now or datetime.now(timezone.utc)
    ranks = _profile_rank(batch)
    labels: list[str] = []
    touched: set[str] = set()
    window = batch.setdefault("schedule_window", {})

    # Check the simple fields first so a refusal leaves the batch as it was.
    name = changes.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
            raise EditError(f"The batch name must be 1–{MAX_NAME} characters.")
    delay = changes.get("batch_iteration_delay_seconds")
    if delay is not None:
        if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= 3600:
            raise EditError("The rest between posts must be 0–3600 seconds.")
    mode = changes.get("session_preparation_mode")
    if mode is not None and mode not in PREPARATION_MODES:
        raise EditError("session_preparation_mode must be off, brief or extended.")
    post_changes = changes.get("posts") or []
    if not isinstance(post_changes, list) or not all(isinstance(item, dict) for item in post_changes):
        raise EditError("posts must be a list of post changes.")
    for item in post_changes:  # refuse a locked or invalid post before anything moves
        _post_diff(batch["posts"][_find_post(batch, item.get("post_id"))], item)

    if name is not None and name.strip() != batch.get("name"):
        batch["name"] = name.strip()
        labels.append("name")
    if delay is not None and delay != window.get("batch_iteration_delay_seconds"):
        window["batch_iteration_delay_seconds"] = delay
        labels.append("rest between posts")
    if mode is not None and mode != window.get("session_preparation_mode"):
        window["session_preparation_mode"] = mode
        for post in batch.get("posts", []):
            if post.get("type") == "live":
                continue
            for execution in post.get("executions", []):
                if not can_change(execution) or android_jobs.phone_of(execution.get("profile_id", "")):
                    continue
                if execution.get("preparation_status") in ("pending", "not_requested", None):
                    execution["preparation_mode"] = mode
                    execution["preparation_status"] = "not_requested" if mode == "off" else "pending"
                    touched.add(execution["execution_id"])
        labels.append("warm-up")

    for item in post_changes:
        post_labels, ids = _edit_post(batch, item, now, ranks)
        labels += post_labels
        touched |= ids

    if choice is not None:
        ids = _retime(batch, choice, now, ranks, plan_times, offsets)
        if ids:
            labels.append({"now": "start now", "auto": "planned times (Follow active hours)",
                           "exact": "new start time"}[choice["timing"]])
            touched |= ids

    if labels:
        _remember(batch, now, labels, len(touched))
        batch["updated_at"] = _iso(now)
    return {"changes": labels, "jobs_changed": len(touched)}


def edit_job(execution: dict, post: dict, *, scheduled_at: datetime | None = None,
             caption: str | None = None, now: datetime | None = None) -> list[str]:
    """One account's own time or caption."""
    now = now or datetime.now(timezone.utc)
    if not can_change(execution):
        raise EditError("This job has already started, so it can't be changed.")
    labels = []
    if caption is not None:
        locked = content_locked(post)
        if locked:
            raise EditError(locked)
        if not caption.strip() and not (post.get("media_file") or "").strip():
            raise EditError("A post needs media or a caption.")
        if caption != execution.get("spun_caption"):
            execution["spun_caption"] = caption
            labels.append("caption")
    if scheduled_at is not None:
        execution["scheduled_at"] = _iso(scheduled_at)
        execution["time_set_by_user"] = True
        labels.append("time")
    if labels:
        _note(execution, "job_edited", now)
    return labels
