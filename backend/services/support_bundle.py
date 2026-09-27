import re
from datetime import datetime, timezone

ALWAYS_REDACT_KEY = re.compile(
    r"(password|passwd|proxy_pass|proxy_user|cookie|authorization|access[_-]?token|refresh[_-]?token|secret|email)",
    re.IGNORECASE,
)
CONTENT_KEY = re.compile(
    r"^(base_caption|spun_caption|caption|first_comment|comment_link)$",
    re.IGNORECASE,
)

def sanitize_diagnostic_string(value: str, options: dict | None = None) -> str:
    if options is None:
        options = {}
    sanitized = str(value)
    root_dir = options.get("rootDir", "")
    if root_dir:
        sanitized = sanitized.replace(str(root_dir), "[APP_ROOT]")

    sanitized = re.sub(r"://[^\s/@:]+:[^\s/@]+@", "://[REDACTED_CREDENTIALS]@", sanitized)
    sanitized = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED_TOKEN]", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(
        r"\b(password|passwd|proxy_pass|proxy_user|access_token|refresh_token|token|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[REDACTED_EMAIL]", sanitized, flags=re.IGNORECASE)

    if not options.get("includeContent"):
        sanitized = re.sub(r"https?://[^\s\"'<>]+", "[REDACTED_URL]", sanitized, flags=re.IGNORECASE)

    return sanitized

def sanitize_diagnostic_value(value, options: dict | None = None, key: str = ""):
    if options is None:
        options = {}
    if ALWAYS_REDACT_KEY.search(key):
        return "[REDACTED]"
    if not options.get("includeContent") and CONTENT_KEY.search(key):
        return "[CONTENT_NOT_INCLUDED]"
    if isinstance(value, list):
        return [sanitize_diagnostic_value(item, options, key) for item in value]
    if isinstance(value, dict):
        return {
            k: sanitize_diagnostic_value(v, options, k)
            for k, v in value.items()
        }
    if isinstance(value, str):
        return sanitize_diagnostic_string(value, options)
    return value

def build_execution_diagnostic(match: dict, options: dict | None = None) -> dict:
    if options is None:
        options = {}
    execution = match["execution"]
    post = match["post"]
    batch = match["batch"]

    report = {
        "schema_version": "1.0",
        "generated_at": options.get("generatedAt") or datetime.now(timezone.utc).isoformat(),
        "report_type": "execution",
        "user_description": sanitize_diagnostic_string(
            options.get("description", ""),
            {**options, "includeContent": True},
        )[:4000],
        "execution": {
            "execution_id": execution.get("execution_id"),
            "profile_id": execution.get("profile_id"),
            "batch_id": batch.get("batch_id"),
            "batch_name": batch.get("name"),
            "post_id": post.get("post_id"),
            "post_type": post.get("type"),
            "scheduled_at": execution.get("scheduled_at"),
            "started_at": execution.get("started_at"),
            "ended_at": execution.get("ended_at"),
            "status": execution.get("status"),
            "stage": execution.get("stage"),
            "error": execution.get("error"),
            "retry_count": execution.get("retry_count", 0),
            "preparation_mode": execution.get("preparation_mode", "off"),
            "preparation_status": execution.get("preparation_status"),
            "stage_history": execution.get("stage_history") or [],
            "permalink_status": execution.get("permalink_status"),
            "permalink_recovery_attempted": execution.get("permalink_recovery_attempted", False),
            "post_match_confidence": execution.get("post_match_confidence"),
            "first_comment_status": execution.get("first_comment_status"),
            "first_comment_method": execution.get("first_comment_method"),
            "container_stopped_at": execution.get("container_stopped_at"),
            "container_cleanup": execution.get("container_cleanup"),
            "container_cleanup_error": execution.get("container_cleanup_error"),
            "logs": execution.get("logs") or [],
            "telemetry": execution.get("telemetry"),
            "preparation_telemetry": execution.get("preparation_telemetry"),
        },
        "privacy": {
            "content_included": options.get("includeContent") is True,
            "screenshots_included": options.get("includeEvidence") is True,
            "browser_profile_included": False,
            "credentials_included": False,
        },
    }

    if options.get("includeContent"):
        report["execution"]["base_caption"] = post.get("base_caption", "")
        report["execution"]["spun_caption"] = execution.get("spun_caption", "")
        report["execution"]["first_comment"] = post.get("first_comment")
        report["execution"]["post_url"] = execution.get("post_url")
        report["execution"]["media_file"] = post.get("media_file", "")

    return sanitize_diagnostic_value(report, options)

def build_queue_diagnostic_summary(queue: dict, options: dict | None = None) -> dict:
    if options is None:
        options = {}
    executions = []
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                executions.append({
                    "execution_id": execution.get("execution_id"),
                    "profile_id": execution.get("profile_id"),
                    "batch_id": batch.get("batch_id"),
                    "post_type": post.get("type"),
                    "status": execution.get("status"),
                    "stage": execution.get("stage"),
                    "scheduled_at": execution.get("scheduled_at"),
                    "started_at": execution.get("started_at"),
                    "ended_at": execution.get("ended_at"),
                    "error": execution.get("error"),
                    "permalink_status": execution.get("permalink_status"),
                    "first_comment_status": execution.get("first_comment_status"),
                    "container_stopped_at": execution.get("container_stopped_at"),
                })

    executions.sort(key=lambda item: str(item.get("scheduled_at") or ""), reverse=True)

    summary = {
        "schema_version": "1.0",
        "generated_at": options.get("generatedAt") or datetime.now(timezone.utc).isoformat(),
        "report_type": "global",
        "user_description": sanitize_diagnostic_string(
            options.get("description", ""),
            {**options, "includeContent": True},
        )[:4000],
        "counts": {
            "total": len(executions),
            "published": len([x for x in executions if x.get("status") == "published"]),
            "failed": len([x for x in executions if x.get("status") in ("failed", "failed_before_publish")]),
            "unresolved": len([x for x in executions if x.get("status") in ("uncertain", "needs_review")]),
            "active": len([x for x in executions if x.get("status") in ("running", "preparing")]),
        },
        "recent_executions": executions[:50],
        "privacy": {
            "content_included": False,
            "screenshots_included": False,
            "browser_profile_included": False,
            "credentials_included": False,
        },
    }
    return sanitize_diagnostic_value(summary, {**options, "includeContent": False})
