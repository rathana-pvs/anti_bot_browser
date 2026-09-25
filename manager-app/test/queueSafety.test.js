import test from 'node:test';
import assert from 'node:assert/strict';

import {
  applyCommentEvidenceBackfill,
  applyVerifiedPermalinkBackfill,
  batchContainsUnresolvedExecution,
  classifyInterruptedExecution,
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
