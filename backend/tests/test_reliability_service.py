from datetime import datetime, timedelta, timezone

from backend.services.reliability_service import build_reliability_scorecard, flatten_queue_executions


def execution(index, *, status="published", telemetry=True, evidence=True, **extra):
    value = {
        "execution_id": f"exec-{index}",
        "status": status,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    if telemetry:
        value["telemetry"] = {"total_duration_ms": 1000 + index}
    if evidence:
        value["evidence_dir"] = f"run-{index}"
    return value


def test_empty_scorecard_never_reports_success():
    result = build_reliability_scorecard([])
    assert result["status"] == "insufficient_data"
    assert not result["gates"][0]["passed"]


def test_pilot_pass_requires_measured_sample_and_safety_gates():
    result = build_reliability_scorecard([execution(index) for index in range(30)])
    assert result["status"] == "pass"
    assert result["metrics"]["definitive_outcome_rate_pct"] == 100.0
    assert all(gate["passed"] for gate in result["gates"])


def test_uncertain_duplicate_and_missing_evidence_fail_pilot():
    rows = [execution(index) for index in range(30)]
    rows[0] = execution(0, status="uncertain", evidence=False, duplicate_detected=True)
    rows[1] = execution(1, evidence=False)
    result = build_reliability_scorecard(rows)
    assert result["status"] == "fail"
    failed = {gate["name"] for gate in result["gates"] if not gate["passed"]}
    assert {"uncertain_rate", "evidence_coverage", "duplicate_safety"} <= failed


def test_old_unresolved_attempt_is_called_out():
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    rows = [execution(index) for index in range(29)] + [
        execution(30, status="needs_review", ended_at=old.isoformat()),
    ]
    result = build_reliability_scorecard(rows)
    assert result["metrics"]["unresolved_over_24h"] == 1
    assert next(gate for gate in result["gates"] if gate["name"] == "unresolved_age")["passed"] is False


def test_queue_flattening_preserves_post_and_batch_context():
    queue = {"daily_batches": [{"batch_id": "batch-1", "posts": [{
        "type": "photo", "executions": [{"execution_id": "exec-1", "status": "published"}],
    }]}]}
    assert flatten_queue_executions(queue)[0]["post_type"] == "photo"
    assert flatten_queue_executions(queue)[0]["batch_id"] == "batch-1"

