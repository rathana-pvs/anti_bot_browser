function percentile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  if (sorted.length === 1) return Math.round(sorted[0] * 100) / 100;
  const position = (sorted.length - 1) * fraction;
  const lower = Math.floor(position);
  const upper = Math.min(sorted.length - 1, lower + 1);
  const result = sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
  return Math.round(result * 100) / 100;
}

function percent(count, total) {
  return total > 0 ? Math.round((count / total) * 1000) / 10 : null;
}

export function buildQueueTelemetrySummary(executions) {
  const terminal = executions.filter((item) =>
    ['published', 'failed', 'failed_before_publish', 'uncertain', 'needs_review'].includes(item.status)
  );
  const measured = terminal.filter((item) => Number(item.telemetry?.total_duration_ms) >= 0);
  const durations = measured.map((item) => Number(item.telemetry.total_duration_ms));
  const ocrSamples = measured.flatMap((item) => item.telemetry?.ocr?.samples || []);
  const ocrInferenceSamples = ocrSamples.filter((sample) => sample.outcome !== 'cache_hit');
  const ocrCacheHits = ocrSamples.length - ocrInferenceSamples.length;
  const ocrDurations = ocrInferenceSamples
    .map((sample) => Number(sample.duration_ms))
    .filter((value) => Number.isFinite(value) && value >= 0);
  const locatorTierCounts = {};
  let locatorFallbacks = 0;
  let locatorEvents = 0;
  for (const item of measured) {
    const locators = item.telemetry?.locators || {};
    for (const [tier, countValue] of Object.entries(locators.tier_counts || {})) {
      const count = Number(countValue) || 0;
      locatorTierCounts[tier] = (locatorTierCounts[tier] || 0) + count;
      locatorEvents += count;
    }
    locatorFallbacks += Number(locators.fallback_count) || 0;
  }

  const semanticEvents = measured.flatMap((item) => item.telemetry?.semantic_fallbacks || []);
  const semanticLatencies = semanticEvents
    .map((event) => Number(event.latency_ms))
    .filter((value) => Number.isFinite(value) && value >= 0);
  const semanticByState = {};
  let semanticValidated = 0;
  let semanticFreshConfirmed = 0;
  let semanticShadowBlocked = 0;
  for (const event of semanticEvents) {
    const state = event.state || 'unknown';
    if (!semanticByState[state]) {
      semanticByState[state] = { proposals: 0, validated: 0, fresh_confirmed: 0 };
    }
    semanticByState[state].proposals += 1;
    if (event.validated) {
      semanticValidated += 1;
      semanticByState[state].validated += 1;
    }
    if (event.fresh_candidate_confirmed) {
      semanticFreshConfirmed += 1;
      semanticByState[state].fresh_confirmed += 1;
    }
    if (event.validation_reason === 'shadow_mode_active_click_blocked') {
      semanticShadowBlocked += 1;
    }
  }

  const byRegion = {};
  for (const sample of ocrInferenceSamples) {
    const region = sample.region || 'unknown';
    const duration = Number(sample.duration_ms);
    if (!Number.isFinite(duration) || duration < 0) continue;
    if (!byRegion[region]) byRegion[region] = { calls: 0, total_duration_ms: 0, samples: [] };
    byRegion[region].calls += 1;
    byRegion[region].total_duration_ms += duration;
    byRegion[region].samples.push(duration);
  }
  for (const [region, stats] of Object.entries(byRegion)) {
    byRegion[region] = {
      calls: stats.calls,
      average_duration_ms: Math.round((stats.total_duration_ms / stats.calls) * 100) / 100,
      p95_duration_ms: percentile(stats.samples, 0.95),
    };
  }

  const published = terminal.filter((item) => item.status === 'published').length;
  const uncertain = terminal.filter((item) => ['uncertain', 'needs_review'].includes(item.status)).length;
  const failed = terminal.filter((item) => ['failed', 'failed_before_publish'].includes(item.status)).length;
  const reviewed = terminal.filter((item) =>
    ['uncertain', 'needs_review'].includes(item.status) || Boolean(item.review_status)
  ).length;

  return {
    terminal_executions: terminal.length,
    measured_executions: measured.length,
    confirmed_publication_rate_pct: percent(published, terminal.length),
    uncertain_rate_pct: percent(uncertain, terminal.length),
    failed_before_publish_rate_pct: percent(failed, terminal.length),
    human_review_rate_pct: percent(reviewed, terminal.length),
    median_execution_duration_ms: percentile(durations, 0.50),
    p95_execution_duration_ms: percentile(durations, 0.95),
    ocr_calls: ocrDurations.length,
    ocr_lookups: ocrSamples.length,
    ocr_inference_calls: ocrInferenceSamples.length,
    ocr_cache_hits: ocrCacheHits,
    ocr_cache_hit_rate_pct: percent(ocrCacheHits, ocrSamples.length),
    median_ocr_duration_ms: percentile(ocrDurations, 0.50),
    p95_ocr_duration_ms: percentile(ocrDurations, 0.95),
    ocr_by_region: byRegion,
    locator_tier_counts: locatorTierCounts,
    locator_fallback_rate_pct: percent(locatorFallbacks, locatorEvents),
    semantic_proposals: semanticEvents.length,
    semantic_validated: semanticValidated,
    semantic_validation_rate_pct: percent(semanticValidated, semanticEvents.length),
    semantic_fresh_confirmed: semanticFreshConfirmed,
    semantic_shadow_blocked: semanticShadowBlocked,
    median_semantic_latency_ms: percentile(semanticLatencies, 0.50),
    p95_semantic_latency_ms: percentile(semanticLatencies, 0.95),
    semantic_by_state: semanticByState,
  };
}
