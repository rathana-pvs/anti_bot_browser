import test from 'node:test';
import assert from 'node:assert/strict';

import { buildQueueTelemetrySummary } from '../telemetrySummary.js';

test('queue telemetry summary calculates outcomes, percentiles, and OCR regions', () => {
  const executions = [
    {
      status: 'published',
      telemetry: {
        total_duration_ms: 1000,
        ocr: { samples: [{ region: 'bottom_action_bar', duration_ms: 100 }] },
        locators: { tier_counts: { target_region_ocr: 2 }, fallback_count: 0 },
        semantic_fallbacks: [{
          state: 'media_ready', latency_ms: 4, validated: true,
          fresh_candidate_confirmed: true, validation_reason: 'shadow_mode_active_click_blocked',
        }],
      },
    },
    {
      status: 'uncertain',
      telemetry: {
        total_duration_ms: 3000,
        ocr: { samples: [
          { region: 'bottom_action_bar', duration_ms: 300 },
          { region: 'bottom_action_bar', duration_ms: 1, outcome: 'cache_hit' },
        ] },
        locators: { tier_counts: { full_screen_ocr: 1 }, fallback_count: 1 },
      },
    },
    { status: 'failed_before_publish', review_status: 'resolved_not_published' },
    { status: 'pending' },
  ];

  const summary = buildQueueTelemetrySummary(executions);
  assert.equal(summary.terminal_executions, 3);
  assert.equal(summary.measured_executions, 2);
  assert.equal(summary.confirmed_publication_rate_pct, 33.3);
  assert.equal(summary.uncertain_rate_pct, 33.3);
  assert.equal(summary.human_review_rate_pct, 66.7);
  assert.equal(summary.median_execution_duration_ms, 2000);
  assert.equal(summary.median_ocr_duration_ms, 200);
  assert.equal(summary.ocr_lookups, 3);
  assert.equal(summary.ocr_inference_calls, 2);
  assert.equal(summary.ocr_cache_hits, 1);
  assert.equal(summary.ocr_cache_hit_rate_pct, 33.3);
  assert.deepEqual(summary.ocr_by_region.bottom_action_bar, {
    calls: 2,
    average_duration_ms: 200,
    p95_duration_ms: 290,
  });
  assert.deepEqual(summary.locator_tier_counts, { target_region_ocr: 2, full_screen_ocr: 1 });
  assert.equal(summary.locator_fallback_rate_pct, 33.3);
  assert.equal(summary.semantic_proposals, 1);
  assert.equal(summary.semantic_validated, 1);
  assert.equal(summary.semantic_validation_rate_pct, 100);
  assert.equal(summary.semantic_fresh_confirmed, 1);
  assert.equal(summary.semantic_shadow_blocked, 1);
  assert.equal(summary.p95_semantic_latency_ms, 4);
  assert.deepEqual(summary.semantic_by_state.media_ready, {
    proposals: 1, validated: 1, fresh_confirmed: 1,
  });
});

test('queue telemetry summary reports null metrics before observations exist', () => {
  const summary = buildQueueTelemetrySummary([{ status: 'pending' }]);
  assert.equal(summary.terminal_executions, 0);
  assert.equal(summary.median_execution_duration_ms, null);
  assert.equal(summary.confirmed_publication_rate_pct, null);
});
