export const UNRESOLVED_EXECUTION_STATUSES = new Set(['uncertain', 'needs_review']);

export const FINAL_CLICK_STAGES = new Set(['publish_clicked', 'verifying']);

export const PRE_PUBLISH_STAGES = new Set([
  'pending',
  'preparing',
  'ready',
  'composing',
  'ready_to_publish',
]);

export function isUnresolvedExecution(execution) {
  return UNRESOLVED_EXECUTION_STATUSES.has(execution?.status);
}

export function isExecutionDeletionLocked(execution) {
  if (isUnresolvedExecution(execution) || ['running', 'preparing'].includes(execution?.status)) return true;
  const stage = latestPersistedStage(execution);
  const wasExplicitlyResolved = ['resolved_published', 'resolved_not_published'].includes(execution?.review_status);
  return FINAL_CLICK_STAGES.has(stage) && execution?.status !== 'published' && !wasExplicitlyResolved;
}

export function batchContainsUnresolvedExecution(batch) {
  return (batch?.posts || []).some((post) =>
    (post.executions || []).some(isExecutionDeletionLocked)
  );
}

export function latestPersistedStage(execution) {
  if (typeof execution?.stage === 'string' && execution.stage.trim()) {
    return execution.stage.trim();
  }
  const history = Array.isArray(execution?.stage_history) ? execution.stage_history : [];
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const stage = history[index]?.stage;
    if (typeof stage === 'string' && stage.trim()) return stage.trim();
  }
  return null;
}

export function classifyInterruptedExecution(execution) {
  const interruptedStage = latestPersistedStage(execution);
  if (interruptedStage && PRE_PUBLISH_STAGES.has(interruptedStage)) {
    return {
      status: 'failed_before_publish',
      stage: 'failed_before_publish',
      interruptedStage,
      error: `Execution interrupted during '${interruptedStage}' before the publish action`,
    };
  }

  return {
    status: 'uncertain',
    stage: 'uncertain',
    interruptedStage: interruptedStage || 'unknown',
    error: interruptedStage && FINAL_CLICK_STAGES.has(interruptedStage)
      ? `Execution interrupted after reaching '${interruptedStage}'; publication outcome requires review`
      : 'Execution interrupted without reliable evidence that the publish action was not sent; publication outcome requires review',
  };
}

export function validateFacebookPermalink(rawUrl, postType = 'post') {
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) return null;

  let parsed;
  try {
    parsed = new URL(rawUrl.trim());
  } catch (_) {
    return null;
  }

  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    return null;
  }

  const host = parsed.hostname.toLowerCase();
  if (host !== 'facebook.com' && !host.endsWith('.facebook.com')) return null;

  const path = parsed.pathname || '/';
  if (['/', '/me', '/home.php', '/login.php', '/checkpoint'].includes(path)) return null;

  const isReel = (
    /^\/reel\/\d+/.test(path)
    || /^\/[^/]+\/videos\/\d+/.test(path)
    || (path.startsWith('/watch') && parsed.searchParams.has('v'))
  );
  const isPost = (
    /^\/[^/]+\/posts\/[a-zA-Z0-9_-]+/.test(path)
    || (path === '/permalink.php' && parsed.searchParams.has('story_fbid'))
    || (path.startsWith('/photo') && parsed.searchParams.has('fbid'))
    || (path.startsWith('/story.php') && parsed.searchParams.has('story_fbid'))
    || /^\/groups\/[^/]+\/permalink\/\d+/.test(path)
    || isReel
  );

  if ((postType === 'reel' && !isReel) || (postType !== 'reel' && !isPost)) return null;

  const allowedQueryKeys = new Set(['story_fbid', 'id', 'fbid', 'v', 'set']);
  for (const key of [...parsed.searchParams.keys()]) {
    if (!allowedQueryKeys.has(key)) parsed.searchParams.delete(key);
  }
  parsed.hash = '';
  return parsed.toString();
}

export function applyVerifiedPermalinkBackfill(execution, rawUrl, postType = 'post', metadata = {}) {
  if (!execution || execution.status !== 'published') {
    throw new Error('Permalink backfill is allowed only for published executions');
  }
  const validatedUrl = validateFacebookPermalink(rawUrl, postType);
  if (!validatedUrl) {
    throw new Error('The supplied URL is not a recognized Facebook post or reel permalink');
  }

  const verifiedAt = metadata.verifiedAt || new Date().toISOString();
  const numericConfidence = Number(metadata.matchConfidence);
  const matchConfidence = Number.isFinite(numericConfidence)
    ? Math.max(0, Math.min(1, numericConfidence))
    : 1.0;
  execution.post_url = validatedUrl;
  execution.post_url_verified_at = verifiedAt;
  execution.post_match_confidence = matchConfidence;
  execution.permalink_status = 'verified';
  execution.permalink_source = metadata.source || 'manual_backfill';
  execution.permalink_note = metadata.note || null;
  execution.stage_history = Array.isArray(execution.stage_history) ? execution.stage_history : [];
  execution.stage_history.push({
    stage: execution.stage || 'published',
    timestamp: verifiedAt,
    reason: 'verified_permalink_backfill',
  });
  return execution;
}

export function applyAutomationPermalinkResult(
  execution,
  result,
  postType = 'post',
  recordedAt = new Date().toISOString(),
) {
  if (!execution || !result || typeof result !== 'object') return execution;

  const recoveryAttempted = result.permalink_recovery_attempted === true;
  execution.permalink_recovery_attempted = recoveryAttempted;

  if (result.post_url) {
    const validatedUrl = validateFacebookPermalink(result.post_url, postType);
    if (!validatedUrl) {
      execution.permalink_status = 'rejected_invalid';
      execution.permalink_missing = true;
      execution.permalink_source = recoveryAttempted ? 'automation_recovery' : 'automation_correlation';
      execution.permalink_note = 'Automation returned a URL that failed manager-side Facebook permalink validation.';
      return execution;
    }

    const numericConfidence = Number(result.post_match_confidence);
    execution.post_url = validatedUrl;
    execution.post_url_verified_at = result.post_url_verified_at || recordedAt;
    execution.post_match_confidence = Number.isFinite(numericConfidence)
      ? Math.max(0, Math.min(1, numericConfidence))
      : null;
    execution.permalink_status = result.permalink_status === 'recovered' ? 'recovered' : 'captured';
    execution.permalink_missing = false;
    execution.permalink_source = execution.permalink_status === 'recovered'
      ? 'automation_recovery'
      : 'automation_correlation';
    execution.permalink_note = null;
    return execution;
  }

  // Never erase a URL that was already validated and attached to this execution.
  if (execution.post_url) return execution;

  execution.permalink_missing = true;
  execution.permalink_status = recoveryAttempted ? 'missing_after_recovery' : 'unresolved';
  execution.permalink_source = recoveryAttempted ? 'automation_recovery' : 'automation_correlation';
  execution.permalink_note = recoveryAttempted
    ? 'No confident permalink was found within the bounded recovery pass.'
    : 'No confident permalink was returned by automation.';
  return execution;
}

export function applyCommentEvidenceBackfill(execution, status, metadata = {}) {
  const allowedStatuses = new Set(['submitted_verified', 'submitted_unverified', 'submission_pending']);
  if (!execution || execution.status !== 'published') {
    throw new Error('Comment evidence can be attached only to published executions');
  }
  if (!allowedStatuses.has(status)) {
    throw new Error('Invalid comment evidence status');
  }
  if (status === 'submitted_verified' && !metadata.evidenceDir) {
    throw new Error('Verified comment evidence requires an evidence directory');
  }

  const recordedAt = metadata.recordedAt || new Date().toISOString();
  execution.first_comment_status = status;
  execution.first_comment_evidence_dir = metadata.evidenceDir || null;
  execution.first_comment_source = metadata.source || 'manual_backfill';
  execution.first_comment_note = metadata.note || null;
  if (status === 'submitted_verified') {
    execution.first_comment_verified_at = recordedAt;
  }
  execution.stage_history = Array.isArray(execution.stage_history) ? execution.stage_history : [];
  execution.stage_history.push({
    stage: execution.stage || 'published',
    timestamp: recordedAt,
    reason: `comment_evidence_${status}`,
  });
  return execution;
}

export function canRetryFirstComment(execution, commentText) {
  const safeRetryStates = new Set([null, undefined, 'failed_to_start', 'failed_before_submission']);
  return Boolean(
    execution?.status === 'published'
    && execution?.first_comment_status === 'failed_input_not_found'
    && typeof commentText === 'string'
    && commentText.trim()
    && safeRetryStates.has(execution?.comment_retry_status)
  );
}

export function applyCommentRetryResult(execution, result, recordedAt = new Date().toISOString()) {
  if (!execution || execution.status !== 'published') {
    throw new Error('Comment retry results can be attached only to published executions');
  }
  const reported = result?.first_comment;
  const allowed = new Set([
    'submitted_verified',
    'submitted_unverified',
    'submission_pending',
    'failed_input_not_found',
  ]);
  const status = allowed.has(reported)
    ? reported
    : (result?.error === 'comment_input_not_found' ? 'failed_input_not_found' : 'submission_pending');

  execution.first_comment_status = status;
  execution.first_comment_method = result?.first_comment_method || null;
  execution.first_comment_evidence_dir = result?.evidence_dir || null;
  execution.first_comment_source = 'comment_only_retry';
  execution.first_comment_retry_count = (execution.first_comment_retry_count || 0) + 1;
  execution.comment_retry_ended_at = recordedAt;
  execution.comment_retry_status = status === 'submitted_verified'
    ? 'completed_verified'
    : status === 'failed_input_not_found'
      ? 'failed_before_submission'
      : 'needs_review';
  if (status === 'submitted_verified') execution.first_comment_verified_at = recordedAt;
  return execution;
}

export function applyWarmingResult(execution, result) {
  if (!execution || !result || typeof result !== 'object') return execution;
  const allowedSurfaces = new Set(['news_feed', 'profile']);
  const requested = allowedSurfaces.has(result.warming_surface_requested)
    ? result.warming_surface_requested
    : null;
  const actual = allowedSurfaces.has(result.warming_surface)
    ? result.warming_surface
    : null;
  const duration = Number(result.duration_seconds);
  const scrollActions = Number(result.scroll_actions ?? result.scroll_count);

  execution.warming_surface_requested = requested;
  execution.warming_surface = actual;
  execution.warming_surface_fallback = result.warming_surface_fallback === true;
  execution.warming_duration_seconds = Number.isFinite(duration) && duration >= 0 ? duration : null;
  execution.warming_scroll_actions = Number.isFinite(scrollActions) && scrollActions >= 0
    ? Math.trunc(scrollActions)
    : null;
  return execution;
}
