const ALWAYS_REDACT_KEY = /(password|passwd|proxy_pass|proxy_user|cookie|authorization|access[_-]?token|refresh[_-]?token|secret|email)/i;
const CONTENT_KEY = /^(base_caption|spun_caption|caption|first_comment|comment_link)$/i;

function sanitizeDiagnosticString(value, { includeContent = false, rootDir = '' } = {}) {
  let sanitized = String(value);
  if (rootDir) sanitized = sanitized.split(rootDir).join('[APP_ROOT]');

  // Credentials embedded in proxy or web URLs must never leave the machine.
  sanitized = sanitized.replace(/:\/\/[^\s/@:]+:[^\s/@]+@/g, '://[REDACTED_CREDENTIALS]@');
  sanitized = sanitized.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED_TOKEN]');
  sanitized = sanitized.replace(
    /\b(password|passwd|proxy_pass|proxy_user|access_token|refresh_token|token|secret)\s*[:=]\s*[^\s,;]+/gi,
    '$1=[REDACTED]',
  );
  sanitized = sanitized.replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[REDACTED_EMAIL]');

  if (!includeContent) {
    sanitized = sanitized.replace(/https?:\/\/[^\s"'<>]+/gi, '[REDACTED_URL]');
  }
  return sanitized;
}

export function sanitizeDiagnosticValue(value, options = {}, key = '') {
  if (ALWAYS_REDACT_KEY.test(key)) return '[REDACTED]';
  if (!options.includeContent && CONTENT_KEY.test(key)) return '[CONTENT_NOT_INCLUDED]';
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeDiagnosticValue(item, options));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([childKey, childValue]) => [
        childKey,
        sanitizeDiagnosticValue(childValue, options, childKey),
      ]),
    );
  }
  if (typeof value === 'string') return sanitizeDiagnosticString(value, options);
  return value;
}

export function buildExecutionDiagnostic(match, options = {}) {
  const { execution, post, batch } = match;
  const report = {
    schema_version: '1.0',
    generated_at: options.generatedAt || new Date().toISOString(),
    report_type: 'execution',
    user_description: sanitizeDiagnosticString(options.description || '', {
      ...options,
      includeContent: true,
    }).slice(0, 4000),
    execution: {
      execution_id: execution.execution_id,
      profile_id: execution.profile_id,
      batch_id: batch.batch_id,
      batch_name: batch.name,
      post_id: post.post_id,
      post_type: post.type,
      scheduled_at: execution.scheduled_at,
      started_at: execution.started_at || null,
      ended_at: execution.ended_at || null,
      status: execution.status,
      stage: execution.stage || null,
      error: execution.error || null,
      retry_count: execution.retry_count || 0,
      preparation_mode: execution.preparation_mode || 'off',
      preparation_status: execution.preparation_status || null,
      stage_history: execution.stage_history || [],
      permalink_status: execution.permalink_status || null,
      permalink_recovery_attempted: execution.permalink_recovery_attempted || false,
      post_match_confidence: execution.post_match_confidence ?? null,
      first_comment_status: execution.first_comment_status || null,
      first_comment_method: execution.first_comment_method || null,
      container_stopped_at: execution.container_stopped_at || null,
      container_cleanup: execution.container_cleanup || null,
      container_cleanup_error: execution.container_cleanup_error || null,
      logs: execution.logs || [],
      telemetry: execution.telemetry || null,
      preparation_telemetry: execution.preparation_telemetry || null,
    },
    privacy: {
      content_included: options.includeContent === true,
      screenshots_included: options.includeEvidence === true,
      browser_profile_included: false,
      credentials_included: false,
    },
  };

  if (options.includeContent) {
    report.execution.base_caption = post.base_caption || '';
    report.execution.spun_caption = execution.spun_caption || '';
    report.execution.first_comment = post.first_comment || null;
    report.execution.post_url = execution.post_url || null;
    report.execution.media_file = post.media_file || '';
  }
  return sanitizeDiagnosticValue(report, options);
}

export function buildQueueDiagnosticSummary(queue, options = {}) {
  const executions = [];
  for (const batch of queue.daily_batches || []) {
    for (const post of batch.posts || []) {
      for (const execution of post.executions || []) {
        executions.push({
          execution_id: execution.execution_id,
          profile_id: execution.profile_id,
          batch_id: batch.batch_id,
          post_type: post.type,
          status: execution.status,
          stage: execution.stage || null,
          scheduled_at: execution.scheduled_at,
          started_at: execution.started_at || null,
          ended_at: execution.ended_at || null,
          error: execution.error || null,
          permalink_status: execution.permalink_status || null,
          first_comment_status: execution.first_comment_status || null,
          container_stopped_at: execution.container_stopped_at || null,
        });
      }
    }
  }
  executions.sort((a, b) => String(b.scheduled_at).localeCompare(String(a.scheduled_at)));
  return sanitizeDiagnosticValue({
    schema_version: '1.0',
    generated_at: options.generatedAt || new Date().toISOString(),
    report_type: 'global',
    user_description: sanitizeDiagnosticString(options.description || '', {
      ...options,
      includeContent: true,
    }).slice(0, 4000),
    counts: {
      total: executions.length,
      published: executions.filter((item) => item.status === 'published').length,
      failed: executions.filter((item) => ['failed', 'failed_before_publish'].includes(item.status)).length,
      unresolved: executions.filter((item) => ['uncertain', 'needs_review'].includes(item.status)).length,
      active: executions.filter((item) => ['running', 'preparing'].includes(item.status)).length,
    },
    recent_executions: executions.slice(0, 50),
    privacy: {
      content_included: false,
      screenshots_included: false,
      browser_profile_included: false,
      credentials_included: false,
    },
  }, { ...options, includeContent: false });
}

