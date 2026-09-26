import test from 'node:test';
import assert from 'node:assert/strict';

import {
  applyAutomationPermalinkResult,
  applyCommentRetryResult,
  applyCommentEvidenceBackfill,
  applyWarmingResult,
  applyVerifiedPermalinkBackfill,
  batchContainsUnresolvedExecution,
  classifyInterruptedExecution,
  canRetryFirstComment,
  isExecutionDeletionLocked,
  validateFacebookPermalink,
} from '../queueSafety.js';

test('verified permalink backfill is restricted to published executions', () => {
  const execution = { status: 'published', stage: 'published', stage_history: [] };
  applyVerifiedPermalinkBackfill(
    execution,
    'https://www.facebook.com/permalink.php?story_fbid=123&id=456&ref=share',
    'post',
    { verifiedAt: '2026-09-25T13:02:25.000Z', matchConfidence: 0.85, source: 'visual_correlation' },
  );
  assert.equal(execution.post_url, 'https://www.facebook.com/permalink.php?story_fbid=123&id=456');
  assert.equal(execution.post_match_confidence, 0.85);
  assert.equal(execution.permalink_status, 'verified');
  assert.equal(execution.stage_history.at(-1).reason, 'verified_permalink_backfill');
  assert.throws(
    () => applyVerifiedPermalinkBackfill({ status: 'uncertain' }, execution.post_url),
    /only for published/,
  );
});

test('verified comment evidence requires a published execution and evidence directory', () => {
  const execution = { status: 'published', stage: 'published' };
  applyCommentEvidenceBackfill(execution, 'submitted_verified', {
    recordedAt: '2026-09-25T13:06:40.000Z',
    evidenceDir: '/evidence/comment-run',
    source: 'visual_text_match',
  });
  assert.equal(execution.first_comment_status, 'submitted_verified');
  assert.equal(execution.first_comment_evidence_dir, '/evidence/comment-run');
  assert.equal(execution.first_comment_source, 'visual_text_match');
  assert.throws(
    () => applyCommentEvidenceBackfill({ status: 'published' }, 'submitted_verified'),
    /requires an evidence directory/,
  );
  assert.throws(
    () => applyCommentEvidenceBackfill({ status: 'uncertain' }, 'submitted_unverified'),
    /only to published/,
  );
});

test('automation permalink result preserves recovered status after manager validation', () => {
  const execution = { status: 'published' };
  applyAutomationPermalinkResult(execution, {
    post_url: 'https://www.facebook.com/example/posts/pfbidRecovered?ref=tracking',
    post_url_verified_at: '2026-09-26T01:00:00.000Z',
    post_match_confidence: 0.82,
    permalink_status: 'recovered',
    permalink_recovery_attempted: true,
  });

  assert.equal(execution.post_url, 'https://www.facebook.com/example/posts/pfbidRecovered');
  assert.equal(execution.permalink_status, 'recovered');
  assert.equal(execution.permalink_source, 'automation_recovery');
  assert.equal(execution.permalink_recovery_attempted, true);
  assert.equal(execution.permalink_missing, false);
  assert.equal(execution.post_match_confidence, 0.82);
});

test('automation permalink result records bounded recovery exhaustion without failing publication', () => {
  const execution = { status: 'published' };
  applyAutomationPermalinkResult(execution, {
    post_url: null,
    permalink_status: 'missing',
    permalink_recovery_attempted: true,
    permalink_missing: true,
  });

  assert.equal(execution.status, 'published');
  assert.equal(execution.post_url, undefined);
  assert.equal(execution.permalink_status, 'missing_after_recovery');
  assert.equal(execution.permalink_missing, true);
});

test('automation permalink result rejects an invalid URL without attaching it', () => {
  const execution = { status: 'published' };
  applyAutomationPermalinkResult(execution, {
    post_url: 'https://example.com/user/posts/123',
    permalink_status: 'recovered',
    permalink_recovery_attempted: true,
  }, 'post');

  assert.equal(execution.post_url, undefined);
  assert.equal(execution.permalink_status, 'rejected_invalid');
  assert.equal(execution.permalink_missing, true);
});

test('comment-only retry is allowed only after a definite pre-submission failure', () => {
  assert.equal(canRetryFirstComment({
    status: 'published',
    first_comment_status: 'failed_input_not_found',
  }, 'https://example.com'), true);
  assert.equal(canRetryFirstComment({
    status: 'published',
    first_comment_status: 'submitted_unverified',
  }, 'https://example.com'), false);
  assert.equal(canRetryFirstComment({
    status: 'published',
    first_comment_status: 'submission_pending',
  }, 'https://example.com'), false);
  assert.equal(canRetryFirstComment({
    status: 'published',
    first_comment_status: 'failed_input_not_found',
    comment_retry_status: 'interrupted',
  }, 'https://example.com'), false);
});

test('comment-only retry result preserves publication and records verification', () => {
  const execution = {
    status: 'published',
    first_comment_status: 'failed_input_not_found',
  };
  applyCommentRetryResult(execution, {
    first_comment: 'submitted_verified',
    first_comment_method: 'profile_first_post',
    evidence_dir: '/evidence/comment-retry',
  }, '2026-09-26T02:00:00.000Z');

  assert.equal(execution.status, 'published');
  assert.equal(execution.first_comment_status, 'submitted_verified');
  assert.equal(execution.comment_retry_status, 'completed_verified');
  assert.equal(execution.first_comment_retry_count, 1);
  assert.equal(execution.first_comment_source, 'comment_only_retry');
});

test('warming result preserves requested and actual surfaces with bounded metrics', () => {
  const execution = { status: 'ready' };
  applyWarmingResult(execution, {
    warming_surface_requested: 'news_feed',
    warming_surface: 'profile',
    warming_surface_fallback: true,
    duration_seconds: 51.25,
    scroll_actions: 9,
  });

  assert.equal(execution.warming_surface_requested, 'news_feed');
  assert.equal(execution.warming_surface, 'profile');
  assert.equal(execution.warming_surface_fallback, true);
  assert.equal(execution.warming_duration_seconds, 51.25);
  assert.equal(execution.warming_scroll_actions, 9);
});

test('warming result rejects unknown surface labels instead of displaying them', () => {
  const execution = {};
  applyWarmingResult(execution, {
    warming_surface_requested: 'marketplace',
    warming_surface: 'groups',
    scroll_count: 4,
  });

  assert.equal(execution.warming_surface_requested, null);
  assert.equal(execution.warming_surface, null);
  assert.equal(execution.warming_scroll_actions, 4);
});

test('batch deletion is locked when any execution outcome is unresolved', () => {
  assert.equal(batchContainsUnresolvedExecution({
    posts: [{ executions: [{ status: 'published' }, { status: 'uncertain' }] }],
  }), true);
  assert.equal(batchContainsUnresolvedExecution({
    posts: [{ executions: [{ status: 'published' }, { status: 'failed_before_publish' }] }],
  }), false);
});

test('deletion remains locked for active and legacy post-click executions', () => {
  assert.equal(isExecutionDeletionLocked({ status: 'running', stage: 'composing' }), true);
  assert.equal(isExecutionDeletionLocked({ status: 'preparing', stage: 'preparing' }), true);
  assert.equal(isExecutionDeletionLocked({ status: 'stopped', stage: 'publish_clicked' }), true);
  assert.equal(isExecutionDeletionLocked({
    status: 'failed_before_publish',
    stage: 'failed_before_publish',
    review_status: 'resolved_not_published',
  }), false);
});

test('restart recovery fails closed after a final click', () => {
  assert.equal(classifyInterruptedExecution({ status: 'running', stage: 'publish_clicked' }).status, 'uncertain');
  assert.equal(classifyInterruptedExecution({ status: 'running', stage: 'verifying' }).status, 'uncertain');
  assert.equal(classifyInterruptedExecution({ status: 'running' }).status, 'uncertain');
});

test('restart recovery permits retry only for a known pre-publish stage', () => {
  const recovered = classifyInterruptedExecution({
    status: 'running',
    stage_history: [{ stage: 'preparing' }, { stage: 'composing' }],
  });
  assert.equal(recovered.status, 'failed_before_publish');
  assert.equal(recovered.interruptedStage, 'composing');
});

test('manual permalink validation accepts recognized Facebook post URLs and removes tracking', () => {
  assert.equal(
    validateFacebookPermalink('https://www.facebook.com/example/posts/pfbid123?utm_source=test#comments'),
    'https://www.facebook.com/example/posts/pfbid123'
  );
  assert.equal(
    validateFacebookPermalink('https://www.facebook.com/permalink.php?story_fbid=123&id=456&ref=share'),
    'https://www.facebook.com/permalink.php?story_fbid=123&id=456'
  );
});

test('manual permalink validation rejects unrelated, profile, and mismatched URLs', () => {
  assert.equal(validateFacebookPermalink('https://example.com/user/posts/123'), null);
  assert.equal(validateFacebookPermalink('https://www.facebook.com/me'), null);
  assert.equal(validateFacebookPermalink('https://www.facebook.com/example/posts/123', 'reel'), null);
  assert.equal(validateFacebookPermalink('not a URL'), null);
});
