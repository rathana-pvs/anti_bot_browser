def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    position = (len(sorted_vals) - 1) * fraction
    lower = int(position)
    upper = min(len(sorted_vals) - 1, lower + 1)
    res = sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * (position - lower)
    return round(res, 2)

def percent(count: int | float, total: int | float) -> float | None:
    return round((count / total) * 100.0, 1) if total > 0 else None

def build_queue_telemetry_summary(executions: list) -> dict:
    terminal = [
        item for item in executions
        if item.get("status") in ("published", "failed", "failed_before_publish", "uncertain", "needs_review")
    ]
    measured = [
        item for item in terminal
        if item.get("telemetry") and isinstance(item["telemetry"].get("total_duration_ms"), (int, float)) and item["telemetry"]["total_duration_ms"] >= 0
    ]

    durations = [float(item["telemetry"]["total_duration_ms"]) for item in measured]

    ocr_samples = []
    for item in measured:
        samples = item.get("telemetry", {}).get("ocr", {}).get("samples")
        if isinstance(samples, list):
            ocr_samples.extend(samples)

    ocr_inference_samples = [s for s in ocr_samples if s.get("outcome") != "cache_hit"]
    ocr_cache_hits = len(ocr_samples) - len(ocr_inference_samples)
    ocr_durations = [
        float(s["duration_ms"]) for s in ocr_inference_samples
        if isinstance(s.get("duration_ms"), (int, float)) and s["duration_ms"] >= 0
    ]

    locator_tier_counts = {}
    locator_fallbacks = 0
    locator_events = 0
    for item in measured:
        locators = item.get("telemetry", {}).get("locators", {})
        tier_counts = locators.get("tier_counts") or {}
        for tier, count_val in tier_counts.items():
            cnt = int(count_val) if count_val else 0
            locator_tier_counts[tier] = locator_tier_counts.get(tier, 0) + cnt
            locator_events += cnt
        locator_fallbacks += int(locators.get("fallback_count") or 0)

    semantic_events = []
    for item in measured:
        fallbacks = item.get("telemetry", {}).get("semantic_fallbacks")
        if isinstance(fallbacks, list):
            semantic_events.extend(fallbacks)

    semantic_latencies = [
        float(e["latency_ms"]) for e in semantic_events
        if isinstance(e.get("latency_ms"), (int, float)) and e["latency_ms"] >= 0
    ]

    semantic_by_state = {}
    semantic_validated = 0
    semantic_fresh_confirmed = 0
    semantic_shadow_blocked = 0
    for event in semantic_events:
        state = event.get("state") or "unknown"
        if state not in semantic_by_state:
            semantic_by_state[state] = {"proposals": 0, "validated": 0, "fresh_confirmed": 0}
        semantic_by_state[state]["proposals"] += 1
        if event.get("validated"):
            semantic_validated += 1
            semantic_by_state[state]["validated"] += 1
        if event.get("fresh_candidate_confirmed"):
            semantic_fresh_confirmed += 1
            semantic_by_state[state]["fresh_confirmed"] += 1
        if event.get("validation_reason") == "shadow_mode_active_click_blocked":
            semantic_shadow_blocked += 1

    by_region = {}
    for sample in ocr_inference_samples:
        region = sample.get("region") or "unknown"
        dur = sample.get("duration_ms")
        if not isinstance(dur, (int, float)) or dur < 0:
            continue
        dur = float(dur)
        if region not in by_region:
            by_region[region] = {"calls": 0, "total_duration_ms": 0.0, "samples": []}
        by_region[region]["calls"] += 1
        by_region[region]["total_duration_ms"] += dur
        by_region[region]["samples"].append(dur)

    for region, stats in by_region.items():
        calls = stats["calls"]
        by_region[region] = {
            "calls": calls,
            "average_duration_ms": round(stats["total_duration_ms"] / calls, 2) if calls else 0.0,
            "p95_duration_ms": percentile(stats["samples"], 0.95),
        }

    published = len([item for item in terminal if item.get("status") == "published"])
    uncertain = len([item for item in terminal if item.get("status") in ("uncertain", "needs_review")])
    failed = len([item for item in terminal if item.get("status") in ("failed", "failed_before_publish")])
    reviewed = len([item for item in terminal if item.get("status") in ("uncertain", "needs_review") or item.get("review_status")])

    return {
        "terminal_executions": len(terminal),
        "measured_executions": len(measured),
        "confirmed_publication_rate_pct": percent(published, len(terminal)),
        "uncertain_rate_pct": percent(uncertain, len(terminal)),
        "failed_before_publish_rate_pct": percent(failed, len(terminal)),
        "human_review_rate_pct": percent(reviewed, len(terminal)),
        "median_execution_duration_ms": percentile(durations, 0.50),
        "p95_execution_duration_ms": percentile(durations, 0.95),
        "ocr_calls": len(ocr_durations),
        "ocr_lookups": len(ocr_samples),
        "ocr_inference_calls": len(ocr_inference_samples),
        "ocr_cache_hits": ocr_cache_hits,
        "ocr_cache_hit_rate_pct": percent(ocr_cache_hits, len(ocr_samples)),
        "median_ocr_duration_ms": percentile(ocr_durations, 0.50),
        "p95_ocr_duration_ms": percentile(ocr_durations, 0.95),
        "ocr_by_region": by_region,
        "locator_tier_counts": locator_tier_counts,
        "locator_fallback_rate_pct": percent(locator_fallbacks, locator_events),
        "semantic_proposals": len(semantic_events),
        "semantic_validated": semantic_validated,
        "semantic_validation_rate_pct": percent(semantic_validated, len(semantic_events)),
        "semantic_fresh_confirmed": semantic_fresh_confirmed,
        "semantic_shadow_blocked": semantic_shadow_blocked,
        "median_semantic_latency_ms": percentile(semantic_latencies, 0.50),
        "p95_semantic_latency_ms": percentile(semantic_latencies, 0.95),
        "semantic_by_state": semantic_by_state,
    }
