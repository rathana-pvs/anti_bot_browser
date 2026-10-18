"""Measured pilot gates for publication reliability.

The scorecard never treats missing observations as success. It is intentionally
separate from scheduler decisions and cannot start or retry work.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.services.telemetry_summary import percent


PILOT_TARGETS = {
    "minimum_terminal_executions": 30,
    "maximum_uncertain_rate_pct": 2.0,
    "minimum_telemetry_coverage_pct": 95.0,
    "minimum_evidence_coverage_pct": 95.0,
    "maximum_duplicate_signals": 0,
    "maximum_unresolved_over_24h": 0,
}

TERMINAL_STATUSES = {
    "published", "failed", "failed_before_publish", "failed_after_publish",
    "uncertain", "needs_review",
}
UNRESOLVED_STATUSES = {"uncertain", "needs_review"}


def flatten_queue_executions(queue: dict) -> list[dict]:
    return [
        {**execution, "post_type": post.get("type"), "batch_id": batch.get("batch_id")}
        for batch in queue.get("daily_batches", [])
        for post in batch.get("posts", [])
        for execution in post.get("executions", [])
    ]


def _older_than_24h(execution: dict, now: datetime) -> bool:
    raw = execution.get("ended_at") or execution.get("stage_updated_at") or execution.get("started_at")
    if not raw:
        return False
    try:
        timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (now - timestamp.astimezone(timezone.utc)).total_seconds() > 24 * 60 * 60
    except (TypeError, ValueError):
        return False


def build_reliability_scorecard(
    executions: list[dict], targets: dict | None = None, now: datetime | None = None,
) -> dict:
    targets = {**PILOT_TARGETS, **(targets or {})}
    now = now or datetime.now(timezone.utc)
    terminal = [item for item in executions if item.get("status") in TERMINAL_STATUSES]
    total = len(terminal)
    uncertain = [item for item in terminal if item.get("status") in UNRESOLVED_STATUSES]
    measured = [
        item for item in terminal
        if isinstance(item.get("telemetry"), dict)
        and isinstance(item["telemetry"].get("total_duration_ms"), (int, float))
    ]
    evidenced = [
        item for item in terminal
        if item.get("evidence_dir") or item.get("evidence") or item.get("evidence_manifest")
    ]
    duplicate_signals = [
        item for item in terminal
        if item.get("duplicate_detected") is True or item.get("status") == "duplicate"
    ]
    unresolved_over_24h = [item for item in uncertain if _older_than_24h(item, now)]
    metrics = {
        "terminal_executions": total,
        "uncertain_executions": len(uncertain),
        "uncertain_rate_pct": percent(len(uncertain), total),
        "definitive_outcome_rate_pct": percent(total - len(uncertain), total),
        "telemetry_coverage_pct": percent(len(measured), total),
        "evidence_coverage_pct": percent(len(evidenced), total),
        "duplicate_signals": len(duplicate_signals),
        "unresolved_over_24h": len(unresolved_over_24h),
    }
    enough_samples = total >= targets["minimum_terminal_executions"]
    gates = [
        {
            "name": "sample_size",
            "passed": enough_samples,
            "observed": total,
            "target": f">={targets['minimum_terminal_executions']}",
        },
        {
            "name": "uncertain_rate",
            "passed": enough_samples and metrics["uncertain_rate_pct"] is not None
            and metrics["uncertain_rate_pct"] <= targets["maximum_uncertain_rate_pct"],
            "observed": metrics["uncertain_rate_pct"],
            "target": f"<={targets['maximum_uncertain_rate_pct']}%",
        },
        {
            "name": "telemetry_coverage",
            "passed": enough_samples and metrics["telemetry_coverage_pct"] is not None
            and metrics["telemetry_coverage_pct"] >= targets["minimum_telemetry_coverage_pct"],
            "observed": metrics["telemetry_coverage_pct"],
            "target": f">={targets['minimum_telemetry_coverage_pct']}%",
        },
        {
            "name": "evidence_coverage",
            "passed": enough_samples and metrics["evidence_coverage_pct"] is not None
            and metrics["evidence_coverage_pct"] >= targets["minimum_evidence_coverage_pct"],
            "observed": metrics["evidence_coverage_pct"],
            "target": f">={targets['minimum_evidence_coverage_pct']}%",
        },
        {
            "name": "duplicate_safety",
            "passed": len(duplicate_signals) <= targets["maximum_duplicate_signals"],
            "observed": len(duplicate_signals),
            "target": str(targets["maximum_duplicate_signals"]),
        },
        {
            "name": "unresolved_age",
            "passed": len(unresolved_over_24h) <= targets["maximum_unresolved_over_24h"],
            "observed": len(unresolved_over_24h),
            "target": str(targets["maximum_unresolved_over_24h"]),
        },
    ]
    if not enough_samples:
        status = "insufficient_data"
    else:
        status = "pass" if all(gate["passed"] for gate in gates) else "fail"
    return {
        "status": status,
        "generated_at": now.isoformat(),
        "targets": targets,
        "metrics": metrics,
        "gates": gates,
        "note": "A passing scorecard reports measured local outcomes; it does not authorize publication or deployment.",
    }

