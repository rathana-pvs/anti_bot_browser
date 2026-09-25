export const DEFAULT_SCHEDULER_CONFIG = Object.freeze({
  max_publishers: 1,
  max_preparers: 1,
  max_total_automation_tasks: 2,
  max_active_profile_containers: 2,
  lease_ttl_ms: 60_000,
  heartbeat_interval_ms: 15_000,
});

const RUNNABLE_STATUSES = new Set([
  'pending',
  'ready',
  'failed',
  'failed_before_publish',
  'skipped_stopped',
  'stopped',
]);

function asTime(value) {
  const parsed = Date.parse(value || '');
  return Number.isFinite(parsed) ? parsed : 0;
}

export function computeExecutionSchedule({
  nowMs,
  isStartNow,
  slotIndex,
  profileIndex,
  staggerMs,
  postSlotMs,
  windowStartMs,
  jitterMs = 0,
}) {
  if (isStartNow && slotIndex === 0) {
    // All profiles are eligible immediately. Publisher capacity still serializes
    // execution, so a profile is never held back by an artificial start delay.
    return nowMs;
  }
  if (isStartNow) {
    return nowMs + slotIndex * postSlotMs + profileIndex * staggerMs + jitterMs;
  }
  return Math.max(
    nowMs + 60_000,
    windowStartMs + slotIndex * postSlotMs + profileIndex * staggerMs + jitterMs,
  );
}

export function isLeaseActive(lease, nowMs = Date.now()) {
  return Boolean(
    lease?.lease_id
    && ['publisher', 'preparer'].includes(lease.kind)
    && asTime(lease.expires_at) > nowMs
  );
}

export function listQueueLeases(queue, nowMs = Date.now()) {
  const leases = [];
  for (const batch of queue?.daily_batches || []) {
    for (const post of batch.posts || []) {
      for (const execution of post.executions || []) {
        if (['running', 'preparing'].includes(execution.status) && isLeaseActive(execution.scheduler_lease, nowMs)) {
          leases.push({ ...execution.scheduler_lease, execution_id: execution.execution_id });
        }
      }
    }
  }
  return leases;
}

export function schedulerSnapshot(queue, additionalLeases = [], config = DEFAULT_SCHEDULER_CONFIG, nowMs = Date.now()) {
  const unique = new Map();
  for (const lease of [...listQueueLeases(queue, nowMs), ...additionalLeases]) {
    if (isLeaseActive(lease, nowMs)) unique.set(lease.lease_id, lease);
  }
  const leases = [...unique.values()];
  const publishers = leases.filter((lease) => lease.kind === 'publisher').length;
  const preparers = leases.filter((lease) => lease.kind === 'preparer').length;
  return {
    config: { ...config },
    active: { publishers, preparers, total: leases.length },
    available: {
      publishers: Math.max(0, config.max_publishers - publishers),
      preparers: Math.max(0, config.max_preparers - preparers),
      total: Math.max(0, config.max_total_automation_tasks - leases.length),
    },
    leases,
  };
}

export function canAcquireSchedulerSlot(
  queue,
  additionalLeases,
  kind,
  profileId,
  config = DEFAULT_SCHEDULER_CONFIG,
  nowMs = Date.now(),
) {
  if (!['publisher', 'preparer'].includes(kind)) {
    return { allowed: false, reason: `invalid_slot_kind:${kind}` };
  }
  const snapshot = schedulerSnapshot(queue, additionalLeases, config, nowMs);
  if (snapshot.leases.some((lease) => lease.profile_id === profileId)) {
    return { allowed: false, reason: 'profile_busy', snapshot };
  }
  if (snapshot.active.total >= config.max_total_automation_tasks) {
    return { allowed: false, reason: 'total_capacity_reached', snapshot };
  }
  if (kind === 'publisher' && snapshot.active.publishers >= config.max_publishers) {
    return { allowed: false, reason: 'publisher_capacity_reached', snapshot };
  }
  if (kind === 'preparer' && snapshot.active.preparers >= config.max_preparers) {
    return { allowed: false, reason: 'preparer_capacity_reached', snapshot };
  }
  return { allowed: true, reason: 'slot_available', snapshot };
}

export function claimExecutionLease(
  queue,
  execution,
  { leaseId, ownerId, kind = 'publisher', config = DEFAULT_SCHEDULER_CONFIG, additionalLeases = [], nowMs = Date.now() },
) {
  if (!execution || !RUNNABLE_STATUSES.has(execution.status)) {
    return { claimed: false, reason: `execution_not_runnable:${execution?.status || 'missing'}` };
  }
  const availability = canAcquireSchedulerSlot(
    queue, additionalLeases, kind, execution.profile_id, config, nowMs,
  );
  if (!availability.allowed) return { claimed: false, ...availability };
  const claimedAt = new Date(nowMs).toISOString();
  execution.scheduler_lease = {
    lease_id: leaseId,
    owner_id: ownerId,
    kind,
    profile_id: execution.profile_id,
    claimed_at: claimedAt,
    heartbeat_at: claimedAt,
    expires_at: new Date(nowMs + config.lease_ttl_ms).toISOString(),
  };
  return { claimed: true, reason: 'claimed', lease: execution.scheduler_lease };
}

export function refreshExecutionLease(execution, leaseId, config = DEFAULT_SCHEDULER_CONFIG, nowMs = Date.now()) {
  if (!execution?.scheduler_lease || execution.scheduler_lease.lease_id !== leaseId) return false;
  execution.scheduler_lease.heartbeat_at = new Date(nowMs).toISOString();
  execution.scheduler_lease.expires_at = new Date(nowMs + config.lease_ttl_ms).toISOString();
  return true;
}

export function releaseExecutionLease(execution, leaseId) {
  if (!execution?.scheduler_lease || execution.scheduler_lease.lease_id !== leaseId) return false;
  execution.last_scheduler_lease = execution.scheduler_lease;
  execution.scheduler_lease = null;
  return true;
}

export function findStaleRunningExecutions(queue, liveLeaseIds = new Set(), nowMs = Date.now()) {
  const stale = [];
  for (const batch of queue?.daily_batches || []) {
    for (const post of batch.posts || []) {
      for (const execution of post.executions || []) {
        if (!['running', 'preparing'].includes(execution.status)) continue;
        const leaseId = execution.scheduler_lease?.lease_id;
        if (leaseId && liveLeaseIds.has(leaseId)) continue;
        if (!isLeaseActive(execution.scheduler_lease, nowMs)) stale.push(execution);
      }
    }
  }
  return stale;
}
