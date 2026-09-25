"""Local execution telemetry for automation measurement and baseline reporting."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def timed_telemetry_step(name: str):
    """Record method wall time without changing its return or exception behavior."""

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            started = time.perf_counter()
            outcome = "completed"
            try:
                return func(self, *args, **kwargs)
            except Exception:
                outcome = "exception"
                raise
            finally:
                telemetry = getattr(self, "telemetry", None)
                if telemetry is not None:
                    telemetry.record_step(
                        name,
                        (time.perf_counter() - started) * 1000.0,
                        outcome=outcome,
                    )

        return wrapper

    return decorator


class TelemetryRecorder:
    """Collect bounded, JSON-serializable measurements for one execution."""

    MAX_STEP_EVENTS = 250
    MAX_LOCATOR_EVENTS = 300
    MAX_OCR_SAMPLES = 500
    MAX_SEMANTIC_EVENTS = 100

    def __init__(self, profile_id: str, task_name: str, evidence_dir: str):
        self.profile_id = profile_id
        self.task_name = task_name
        self.evidence_dir = evidence_dir
        self.started_at = _utc_now()
        self._started_monotonic = time.perf_counter()
        self.ended_at: str | None = None
        self.outcome: str | None = None
        self._total_duration_ms: float | None = None
        self.steps: list[dict] = []
        self.locator_events: list[dict] = []
        self.ocr_samples: list[dict] = []
        self.semantic_events: list[dict] = []
        self.stage_durations_ms: dict[str, float] = {}
        self._current_stage: str | None = None
        self._stage_started = self._started_monotonic
        self.environment = {
            "screen_size": None,
            "configured_screen_resolution": None,
            "locale": None,
            "theme": None,
            "theme_confidence": None,
            "browser_zoom": None,
        }

    def set_environment(self, **values) -> None:
        for key, value in values.items():
            if key in self.environment and value is not None:
                self.environment[key] = value

    def observe_screen(self, screen) -> None:
        if screen is None or not getattr(screen, "size", 0):
            return
        height, width = screen.shape[:2]
        self.environment["screen_size"] = [int(width), int(height)]
        if self.environment.get("theme") is None:
            mean_luminance = float(screen.mean())
            self.environment["theme"] = "dark" if mean_luminance < 105.0 else "light"
            self.environment["theme_confidence"] = 0.5

    def record_stage(self, stage: str) -> None:
        now = time.perf_counter()
        if self._current_stage:
            elapsed = (now - self._stage_started) * 1000.0
            self.stage_durations_ms[self._current_stage] = round(
                self.stage_durations_ms.get(self._current_stage, 0.0) + elapsed,
                2,
            )
        self._current_stage = stage
        self._stage_started = now

    def record_step(self, name: str, duration_ms: float, outcome: str = "completed", **metadata) -> None:
        if len(self.steps) >= self.MAX_STEP_EVENTS:
            return
        self.steps.append({
            "name": name,
            "duration_ms": round(max(0.0, float(duration_ms)), 2),
            "outcome": outcome,
            "recorded_at": _utc_now(),
            **metadata,
        })

    def record_navigation(self, url: str, duration_ms: float, outcome: str) -> None:
        try:
            parsed = urlparse(url)
            destination = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        except Exception:
            destination = "unparseable"
        self.record_step("navigation", duration_ms, outcome=outcome, destination=destination)

    def record_ocr(
        self,
        duration_ms: float,
        region: str,
        candidate_count: int,
        confidence_values: list[float] | None = None,
        outcome: str = "completed",
    ) -> None:
        if len(self.ocr_samples) >= self.MAX_OCR_SAMPLES:
            return
        confidences = confidence_values or []
        self.ocr_samples.append({
            "duration_ms": round(max(0.0, float(duration_ms)), 2),
            "region": region,
            "candidate_count": int(candidate_count),
            "max_confidence": round(max(confidences), 4) if confidences else None,
            "outcome": outcome,
        })

    def record_locator(
        self,
        locator: str,
        tier: str,
        region: str,
        duration_ms: float,
        found: bool,
        confidence: float | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        if len(self.locator_events) >= self.MAX_LOCATOR_EVENTS:
            return
        self.locator_events.append({
            "locator": locator,
            "tier": tier,
            "region": region,
            "duration_ms": round(max(0.0, float(duration_ms)), 2),
            "found": bool(found),
            "confidence": round(float(confidence), 4) if confidence is not None else None,
            "fallback_reason": fallback_reason,
        })

    def record_semantic(self, proposal: dict, goal: str, state: str) -> None:
        if len(self.semantic_events) >= self.MAX_SEMANTIC_EVENTS:
            return
        self.semantic_events.append({
            "goal": goal,
            "state": state,
            "provider": proposal.get("provider"),
            "model": proposal.get("model"),
            "latency_ms": proposal.get("latency_ms"),
            "confidence": proposal.get("confidence"),
            "candidate_id": proposal.get("candidate_id"),
            "shadow_mode": proposal.get("shadow_mode"),
            "validated": proposal.get("validated"),
            "validation_reason": proposal.get("validation_reason"),
            "fresh_candidate_confirmed": proposal.get("fresh_candidate_confirmed"),
            "observed_state": proposal.get("observed_state"),
        })

    def finalize(self, outcome: str) -> dict:
        if self.ended_at is None:
            now = time.perf_counter()
            if self._current_stage:
                elapsed = (now - self._stage_started) * 1000.0
                self.stage_durations_ms[self._current_stage] = round(
                    self.stage_durations_ms.get(self._current_stage, 0.0) + elapsed,
                    2,
                )
                self._current_stage = None
            self.ended_at = _utc_now()
            self._total_duration_ms = round((now - self._started_monotonic) * 1000.0, 2)
        self.outcome = outcome
        snapshot = self.snapshot()
        self._write(snapshot)
        return snapshot

    def snapshot(self) -> dict:
        total_duration_ms = self._total_duration_ms
        if total_duration_ms is None:
            total_duration_ms = (time.perf_counter() - self._started_monotonic) * 1000.0
        ocr_durations = [item["duration_ms"] for item in self.ocr_samples]
        inference_samples = [item for item in self.ocr_samples if item.get("outcome") != "cache_hit"]
        inference_durations = [item["duration_ms"] for item in inference_samples]
        cache_hits = len(self.ocr_samples) - len(inference_samples)
        tier_counts: dict[str, int] = {}
        fallback_count = 0
        for event in self.locator_events:
            tier = event["tier"]
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            if event.get("fallback_reason"):
                fallback_count += 1

        return {
            "schema_version": "1.0",
            "profile_id": self.profile_id,
            "task": self.task_name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "total_duration_ms": round(total_duration_ms, 2),
            "stage_durations_ms": dict(self.stage_durations_ms),
            "steps": list(self.steps),
            "ocr": {
                "calls": len(self.ocr_samples),
                "total_duration_ms": round(sum(ocr_durations), 2),
                "median_duration_ms": _percentile(ocr_durations, 0.50),
                "p95_duration_ms": _percentile(ocr_durations, 0.95),
                "inference_calls": len(inference_samples),
                "inference_total_duration_ms": round(sum(inference_durations), 2),
                "cache_hits": cache_hits,
                "cache_hit_rate_pct": round((cache_hits / len(self.ocr_samples)) * 100.0, 1)
                if self.ocr_samples else None,
                "samples": list(self.ocr_samples),
            },
            "locators": {
                "events": list(self.locator_events),
                "tier_counts": tier_counts,
                "fallback_count": fallback_count,
            },
            "semantic_fallbacks": list(self.semantic_events),
            "environment": dict(self.environment),
        }

    def _write(self, payload: dict) -> None:
        os.makedirs(self.evidence_dir, exist_ok=True)
        target = os.path.join(self.evidence_dir, "telemetry.json")
        temp = f"{target}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temp, target)
