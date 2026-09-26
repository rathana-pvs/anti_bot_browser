import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_SCHEDULER_CONFIG,
  canAcquireSchedulerSlot,
  claimExecutionLease,
  computeExecutionSchedule,
  findStaleRunningExecutions,
  refreshExecutionLease,
  releaseExecutionLease,
  schedulerSnapshot,
  workerSilenceTimeoutMs,
} from '../queueScheduler.js';

const NOW = Date.parse('2026-09-25T14:00:00.000Z');
const queueWith = (...executions) => ({
  daily_batches: [{ posts: [{ executions }] }],
});

test('medium mode permits two publisher lanes but fences a third', () => {
  const medium = {
    ...DEFAULT_SCHEDULER_CONFIG,
    max_publishers: 2,
    max_preparers: 2,
    max_total_automation_tasks: 4,
    max_active_profile_containers: 4,
  };
  const first = { execution_id: 'e1', profile_id: 'p1', status: 'pending' };
  const second = { execution_id: 'e2', profile_id: 'p2', status: 'pending' };
  const third = { execution_id: 'e3', profile_id: 'p3', status: 'pending' };
  const queue = queueWith(first, second, third);
  assert.equal(claimExecutionLease(queue, first, { leaseId: 'l1', ownerId: 'm', config: medium, nowMs: NOW }).claimed, true);
  first.status = 'running';
  assert.equal(claimExecutionLease(queue, second, { leaseId: 'l2', ownerId: 'm', config: medium, nowMs: NOW }).claimed, true);
  second.status = 'running';
  assert.equal(canAcquireSchedulerSlot(queue, [], 'publisher', third.profile_id, medium, NOW).reason, 'publisher_capacity_reached');
});

test('start-now makes every profile first slot immediately eligible', () => {
  const base = {
    nowMs: NOW,
    isStartNow: true,
    slotIndex: 0,
    staggerMs: 15 * 60_000,
    postSlotMs: 60 * 60_000,
    windowStartMs: NOW,
  };
  assert.equal(computeExecutionSchedule({ ...base, profileIndex: 0 }), NOW);
  assert.equal(computeExecutionSchedule({ ...base, profileIndex: 4 }), NOW);
});

test('scheduled batches retain profile staggering', () => {
  assert.equal(computeExecutionSchedule({
    nowMs: NOW,
    isStartNow: false,
    slotIndex: 0,
    profileIndex: 2,
    staggerMs: 15 * 60_000,
    postSlotMs: 60 * 60_000,
    windowStartMs: NOW + 60 * 60_000,
  }), NOW + 90 * 60_000);
});

test('publisher capacity is global and profile locks are exclusive', () => {
  const first = { execution_id: 'e1', profile_id: 'profile_001', status: 'pending' };
  const second = { execution_id: 'e2', profile_id: 'profile_002', status: 'pending' };
  const sameProfile = { execution_id: 'e3', profile_id: 'profile_001', status: 'pending' };
  const queue = queueWith(first, second, sameProfile);
  const claim = claimExecutionLease(queue, first, {
    leaseId: 'lease-1', ownerId: 'manager-1', nowMs: NOW,
  });
  first.status = 'running';

  assert.equal(claim.claimed, true);
  assert.equal(canAcquireSchedulerSlot(queue, [], 'publisher', second.profile_id, DEFAULT_SCHEDULER_CONFIG, NOW).reason, 'publisher_capacity_reached');
  assert.equal(canAcquireSchedulerSlot(queue, [], 'publisher', sameProfile.profile_id, DEFAULT_SCHEDULER_CONFIG, NOW).reason, 'profile_busy');
});

test('publisher and preparer can coexist up to total capacity', () => {
  const publisher = {
    execution_id: 'e1', profile_id: 'profile_001', status: 'running',
    scheduler_lease: {
      lease_id: 'pub', kind: 'publisher', profile_id: 'profile_001',
      expires_at: new Date(NOW + 60_000).toISOString(),
    },
  };
  const queue = queueWith(publisher);
  const result = canAcquireSchedulerSlot(queue, [], 'preparer', 'profile_002', DEFAULT_SCHEDULER_CONFIG, NOW);

  assert.equal(result.allowed, true);
  assert.deepEqual(schedulerSnapshot(queue, [], DEFAULT_SCHEDULER_CONFIG, NOW).active, {
    publishers: 1, preparers: 0, total: 1,
  });
});

test('persisted preparing leases consume the preparer slot', () => {
  const preparation = {
    execution_id: 'prep', profile_id: 'profile_001', status: 'preparing',
    scheduler_lease: {
      lease_id: 'prep-token', kind: 'preparer', profile_id: 'profile_001',
      expires_at: new Date(NOW + 60_000).toISOString(),
    },
  };
  const snapshot = schedulerSnapshot(queueWith(preparation), [], DEFAULT_SCHEDULER_CONFIG, NOW);

  assert.deepEqual(snapshot.active, { publishers: 0, preparers: 1, total: 1 });
  assert.equal(
    canAcquireSchedulerSlot(queueWith(preparation), [], 'preparer', 'profile_002', DEFAULT_SCHEDULER_CONFIG, NOW).reason,
    'preparer_capacity_reached',
  );
});

test('lease heartbeat uses fencing token and release preserves audit data', () => {
  const execution = {
    scheduler_lease: {
      lease_id: 'lease-1', kind: 'publisher', profile_id: 'profile_001',
      expires_at: new Date(NOW + 10_000).toISOString(),
    },
  };
  assert.equal(refreshExecutionLease(execution, 'wrong-token', DEFAULT_SCHEDULER_CONFIG, NOW), false);
  assert.equal(refreshExecutionLease(execution, 'lease-1', DEFAULT_SCHEDULER_CONFIG, NOW), true);
  assert.equal(Date.parse(execution.scheduler_lease.expires_at), NOW + DEFAULT_SCHEDULER_CONFIG.lease_ttl_ms);
  assert.equal(releaseExecutionLease(execution, 'wrong-token'), false);
  assert.equal(releaseExecutionLease(execution, 'lease-1'), true);
  assert.equal(execution.scheduler_lease, null);
  assert.equal(execution.last_scheduler_lease.lease_id, 'lease-1');
});

test('only expired or missing leases are recovered as stale', () => {
  const live = {
    execution_id: 'live', profile_id: 'profile_001', status: 'running',
    scheduler_lease: { lease_id: 'live-token', kind: 'publisher', expires_at: new Date(NOW + 1).toISOString() },
  };
  const expired = {
    execution_id: 'expired', profile_id: 'profile_002', status: 'running',
    scheduler_lease: { lease_id: 'expired-token', kind: 'publisher', expires_at: new Date(NOW - 1).toISOString() },
  };
  const legacy = { execution_id: 'legacy', profile_id: 'profile_003', status: 'running' };

  assert.deepEqual(
    findStaleRunningExecutions(queueWith(live, expired, legacy), new Set(), NOW).map((item) => item.execution_id),
    ['expired', 'legacy'],
  );
  assert.deepEqual(
    findStaleRunningExecutions(queueWith(live), new Set(['live-token']), NOW),
    [],
  );
});

test('live-worker silence limits are stage-specific and longer than scheduler leases', () => {
  assert.equal(workerSilenceTimeoutMs('preparing'), 6 * 60_000);
  assert.equal(workerSilenceTimeoutMs('composing'), 12 * 60_000);
  assert.equal(workerSilenceTimeoutMs('verifying'), 12 * 60_000);
  assert.equal(workerSilenceTimeoutMs('unknown'), 6 * 60_000);
  assert.equal(workerSilenceTimeoutMs('composing', 5 * 60_000), 15 * 60_000);
});

test('published and unresolved executions cannot be claimed', () => {
  for (const status of ['published', 'uncertain', 'needs_review', 'running']) {
    const execution = { execution_id: status, profile_id: 'profile_001', status };
    const result = claimExecutionLease(queueWith(execution), execution, {
      leaseId: `lease-${status}`, ownerId: 'manager-1', nowMs: NOW,
    });
    assert.equal(result.claimed, false);
  }
});
