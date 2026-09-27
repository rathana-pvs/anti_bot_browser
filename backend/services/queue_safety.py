import re
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

UNRESOLVED_EXECUTION_STATUSES = {"uncertain", "needs_review"}
FINAL_CLICK_STAGES = {"publish_clicked", "verifying"}
PRE_PUBLISH_STAGES = {
    "pending",
    "preparing",
    "ready",
    "composing",
    "ready_to_publish",
}

def is_unresolved_execution(execution: dict | None) -> bool:
    return bool(execution and execution.get("status") in UNRESOLVED_EXECUTION_STATUSES)

def is_execution_deletion_locked(execution: dict | None) -> bool:
    if not execution:
        return False
    if is_unresolved_execution(execution) or execution.get("status") in ("running", "preparing"):
        return True
    stage = latest_persisted_stage(execution)
    was_explicitly_resolved = execution.get("review_status") in ("resolved_published", "resolved_not_published")
    return bool(stage in FINAL_CLICK_STAGES and execution.get("status") != "published" and not was_explicitly_resolved)

def batch_contains_unresolved_execution(batch: dict | None) -> bool:
    if not batch:
        return False
    for post in batch.get("posts", []):
        for execution in post.get("executions", []):
            if is_execution_deletion_locked(execution):
                return True
    return False

def latest_persisted_stage(execution: dict | None) -> str | None:
    if not execution:
        return None
    if isinstance(execution.get("stage"), str) and execution["stage"].strip():
        return execution["stage"].strip()
    history = execution.get("stage_history")
    if isinstance(history, list):
        for item in reversed(history):
            stage = item.get("stage")
            if isinstance(stage, str) and stage.strip():
                return stage.strip()
    return None

def classify_interrupted_execution(execution: dict | None) -> dict:
    interrupted_stage = latest_persisted_stage(execution)
    if interrupted_stage and interrupted_stage in PRE_PUBLISH_STAGES:
        return {
            "status": "failed_before_publish",
            "stage": "failed_before_publish",
            "interruptedStage": interrupted_stage,
            "error": f"Execution interrupted during '{interrupted_stage}' before the publish action",
        }
    return {
        "status": "uncertain",
        "stage": "uncertain",
        "interruptedStage": interrupted_stage or "unknown",
        "error": (
            f"Execution interrupted after reaching '{interrupted_stage}'; publication outcome requires review"
            if (interrupted_stage and interrupted_stage in FINAL_CLICK_STAGES)
            else "Execution interrupted without reliable evidence that the publish action was not sent; publication outcome requires review"
        ),
    }

def validate_facebook_permalink(raw_url: str | None, post_type: str = "post") -> str | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    try:
        parsed = urlparse(raw_url.strip())
    except Exception:
        return None

    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return None

    host = (parsed.hostname or "").lower()
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        return None

    path = parsed.path or "/"
    if path in ("/", "/me", "/home.php", "/login.php", "/checkpoint"):
        return None

    query_params = parse_qs(parsed.query)

    is_reel = bool(
        re.match(r"^/reel/\d+", path)
        or re.match(r"^/[^/]+/videos/\d+", path)
        or (path.startswith("/watch") and "v" in query_params)
    )
    is_post = bool(
        re.match(r"^/[^/]+/posts/[a-zA-Z0-9_-]+", path)
        or (path == "/permalink.php" and "story_fbid" in query_params)
        or (path.startswith("/photo") and "fbid" in query_params)
        or (path.startswith("/story.php") and "story_fbid" in query_params)
        or re.match(r"^/groups/[^/]+/permalink/\d+", path)
        or is_reel
    )

    if (post_type == "reel" and not is_reel) or (post_type != "reel" and not is_post):
        return None

    allowed_query_keys = {"story_fbid", "id", "fbid", "v", "set"}
    filtered_query = {k: v for k, v in query_params.items() if k in allowed_query_keys}
    clean_query = urlencode(filtered_query, doseq=True)

    cleaned = parsed._replace(query=clean_query, fragment="")
    return urlunparse(cleaned)

def apply_verified_permalink_backfill(execution: dict, raw_url: str, post_type: str = "post", metadata: dict | None = None) -> dict:
    if metadata is None:
        metadata = {}
    if not execution or execution.get("status") != "published":
        raise ValueError("Permalink backfill is allowed only for published executions")

    validated_url = validate_facebook_permalink(raw_url, post_type)
    if not validated_url:
        raise ValueError("The supplied URL is not a recognized Facebook post or reel permalink")

    verified_at = metadata.get("verifiedAt") or datetime.now(timezone.utc).isoformat()
    try:
        numeric_conf = float(metadata.get("matchConfidence", 1.0))
        match_conf = max(0.0, min(1.0, numeric_conf))
    except (TypeError, ValueError):
        match_conf = 1.0

    execution["post_url"] = validated_url
    execution["post_url_verified_at"] = verified_at
    execution["post_match_confidence"] = match_conf
    execution["permalink_status"] = "verified"
    execution["permalink_source"] = metadata.get("source") or "manual_backfill"
    execution["permalink_note"] = metadata.get("note") or None

    history = execution.get("stage_history")
    if not isinstance(history, list):
        history = []
        execution["stage_history"] = history
    history.append({
        "stage": execution.get("stage") or "published",
        "timestamp": verified_at,
        "reason": "verified_permalink_backfill",
    })
    return execution

def apply_automation_permalink_result(
    execution: dict | None,
    result: dict | None,
    post_type: str = "post",
    recorded_at: str | None = None,
) -> dict | None:
    if not execution or not result or not isinstance(result, dict):
        return execution
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc).isoformat()

    recovery_attempted = result.get("permalink_recovery_attempted") is True
    execution["permalink_recovery_attempted"] = recovery_attempted

    post_url = result.get("post_url")
    if post_url:
        validated_url = validate_facebook_permalink(post_url, post_type)
        if not validated_url:
            execution["permalink_status"] = "rejected_invalid"
            execution["permalink_missing"] = True
            execution["permalink_source"] = "automation_recovery" if recovery_attempted else "automation_correlation"
            execution["permalink_note"] = "Automation returned a URL that failed manager-side Facebook permalink validation."
            return execution

        try:
            numeric_conf = float(result.get("post_match_confidence"))
            match_conf = max(0.0, min(1.0, numeric_conf))
        except (TypeError, ValueError):
            match_conf = None

        execution["post_url"] = validated_url
        execution["post_url_verified_at"] = result.get("post_url_verified_at") or recorded_at
        execution["post_match_confidence"] = match_conf
        execution["permalink_status"] = "recovered" if result.get("permalink_status") == "recovered" else "captured"
        execution["permalink_missing"] = False
        execution["permalink_source"] = "automation_recovery" if execution["permalink_status"] == "recovered" else "automation_correlation"
        execution["permalink_note"] = None
        return execution

    if execution.get("post_url"):
        return execution

    execution["permalink_missing"] = True
    execution["permalink_status"] = "missing_after_recovery" if recovery_attempted else "unresolved"
    execution["permalink_source"] = "automation_recovery" if recovery_attempted else "automation_correlation"
    execution["permalink_note"] = (
        "No confident permalink was found within the bounded recovery pass."
        if recovery_attempted
        else "No confident permalink was returned by automation."
    )
    return execution

def apply_comment_evidence_backfill(execution: dict, status: str, metadata: dict | None = None) -> dict:
    if metadata is None:
        metadata = {}
    allowed_statuses = {"submitted_verified", "submitted_unverified", "submission_pending"}
    if not execution or execution.get("status") != "published":
        raise ValueError("Comment evidence can be attached only to published executions")
    if status not in allowed_statuses:
        raise ValueError("Invalid comment evidence status")
    if status == "submitted_verified" and not metadata.get("evidenceDir"):
        raise ValueError("Verified comment evidence requires an evidence directory")

    recorded_at = metadata.get("recordedAt") or datetime.now(timezone.utc).isoformat()
    execution["first_comment_status"] = status
    execution["first_comment_evidence_dir"] = metadata.get("evidenceDir") or None
    execution["first_comment_source"] = metadata.get("source") or "manual_backfill"
    execution["first_comment_note"] = metadata.get("note") or None
    if status == "submitted_verified":
        execution["first_comment_verified_at"] = recorded_at

    history = execution.get("stage_history")
    if not isinstance(history, list):
        history = []
        execution["stage_history"] = history
    history.append({
        "stage": execution.get("stage") or "published",
        "timestamp": recorded_at,
        "reason": f"comment_evidence_{status}",
    })
    return execution

def can_retry_first_comment(execution: dict | None, comment_text: str | None) -> bool:
    safe_retry_states = {None, "failed_to_start", "failed_before_submission"}
    return bool(
        execution
        and execution.get("status") == "published"
        and execution.get("first_comment_status") == "failed_input_not_found"
        and isinstance(comment_text, str)
        and comment_text.strip()
        and execution.get("comment_retry_status") in safe_retry_states
    )

def apply_comment_retry_result(execution: dict, result: dict | None, recorded_at: str | None = None) -> dict:
    if not execution or execution.get("status") != "published":
        raise ValueError("Comment retry results can be attached only to published executions")
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc).isoformat()

    reported = result.get("first_comment") if result else None
    allowed = {
        "submitted_verified",
        "submitted_unverified",
        "submission_pending",
        "failed_input_not_found",
    }
    if reported in allowed:
        status = reported
    else:
        status = "failed_input_not_found" if (result and result.get("error") == "comment_input_not_found") else "submission_pending"

    execution["first_comment_status"] = status
    execution["first_comment_method"] = result.get("first_comment_method") if result else None
    execution["first_comment_evidence_dir"] = result.get("evidence_dir") if result else None
    execution["first_comment_source"] = "comment_only_retry"
    execution["first_comment_retry_count"] = (execution.get("first_comment_retry_count") or 0) + 1
    execution["comment_retry_ended_at"] = recorded_at

    if status == "submitted_verified":
        execution["comment_retry_status"] = "completed_verified"
        execution["first_comment_verified_at"] = recorded_at
    elif status == "failed_input_not_found":
        execution["comment_retry_status"] = "failed_before_submission"
    else:
        execution["comment_retry_status"] = "needs_review"

    return execution

def apply_warming_result(execution: dict | None, result: dict | None) -> dict | None:
    if not execution or not result or not isinstance(result, dict):
        return execution
    allowed_surfaces = {"news_feed", "profile"}
    requested = result.get("warming_surface_requested") if result.get("warming_surface_requested") in allowed_surfaces else None
    actual = result.get("warming_surface") if result.get("warming_surface") in allowed_surfaces else None

    try:
        duration = float(result.get("duration_seconds"))
        dur_val = duration if duration >= 0 else None
    except (TypeError, ValueError):
        dur_val = None

    try:
        scrolls = float(result.get("scroll_actions") if result.get("scroll_actions") is not None else result.get("scroll_count"))
        scroll_val = int(scrolls) if scrolls >= 0 else None
    except (TypeError, ValueError):
        scroll_val = None

    execution["warming_surface_requested"] = requested
    execution["warming_surface"] = actual
    execution["warming_surface_fallback"] = result.get("warming_surface_fallback") is True
    execution["warming_duration_seconds"] = dur_val
    execution["warming_scroll_actions"] = scroll_val
    return execution
