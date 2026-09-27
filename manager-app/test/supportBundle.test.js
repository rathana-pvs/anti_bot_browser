import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildExecutionDiagnostic,
  buildQueueDiagnosticSummary,
  sanitizeDiagnosticValue,
} from '../supportBundle.js';

test('diagnostic sanitizer removes credentials, email, URLs, and app root', () => {
  const value = sanitizeDiagnosticValue({
    proxy_pass: 'secret-pass',
    line: 'password=hunter2 https://user:pass@proxy.test/path user@example.com /workspace/profiles/p1',
  }, { rootDir: '/workspace' });
  assert.equal(value.proxy_pass, '[REDACTED]');
  assert.doesNotMatch(value.line, /hunter2|user:pass|user@example\.com|\/workspace/);
  assert.match(value.line, /\[REDACTED_URL\]/);
  assert.match(value.line, /\[APP_ROOT\]/);
});

test('execution report excludes post content by default', () => {
  const match = {
    batch: { batch_id: 'batch_1', name: 'Test batch' },
    post: {
      post_id: 'p_1', type: 'reel', media_file: 'private.mp4',
      base_caption: 'private caption', first_comment: 'https://example.com/private',
    },
    execution: {
      execution_id: 'exec_1', profile_id: 'profile_001', status: 'published',
      spun_caption: 'private spun caption', logs: ['Opened https://example.com/private'],
    },
  };
  const report = buildExecutionDiagnostic(match, { generatedAt: '2026-01-01T00:00:00.000Z' });
  assert.equal(report.execution.base_caption, undefined);
  assert.equal(report.execution.first_comment, undefined);
  assert.equal(report.execution.media_file, undefined);
  assert.equal(report.execution.logs[0], 'Opened [REDACTED_URL]');
  assert.equal(report.privacy.content_included, false);
});

test('explicit content inclusion never includes credential fields', () => {
  const match = {
    batch: { batch_id: 'batch_1', name: 'Test batch' },
    post: {
      post_id: 'p_1', type: 'photo', media_file: 'image.png',
      base_caption: 'caption', first_comment: 'https://example.com/article',
    },
    execution: {
      execution_id: 'exec_1', profile_id: 'profile_001', status: 'published',
      spun_caption: 'caption', logs: ['proxy_pass=secret'], telemetry: { password: 'never' },
    },
  };
  const report = buildExecutionDiagnostic(match, { includeContent: true });
  assert.equal(report.execution.base_caption, 'caption');
  assert.equal(report.execution.first_comment, 'https://example.com/article');
  assert.doesNotMatch(JSON.stringify(report), /secret|never/);
});

test('global report contains only bounded content-free execution summaries', () => {
  const queue = { daily_batches: [{
    batch_id: 'batch_1', posts: [{ post_id: 'p_1', type: 'photo', base_caption: 'private', executions: [{
      execution_id: 'exec_1', profile_id: 'profile_001', status: 'failed_before_publish',
      error: 'Failed at https://example.com/private', scheduled_at: '2026-01-01T00:00:00Z',
    }] }],
  }] };
  const report = buildQueueDiagnosticSummary(queue);
  assert.equal(report.counts.failed, 1);
  assert.equal(report.recent_executions[0].error, 'Failed at [REDACTED_URL]');
  assert.doesNotMatch(JSON.stringify(report), /private/);
});
