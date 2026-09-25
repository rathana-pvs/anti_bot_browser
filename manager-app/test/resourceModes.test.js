import test from 'node:test';
import assert from 'node:assert/strict';

import {
  recommendedResourceMode,
  resolveResourceMode,
  resourceAdmissionDecision,
} from '../resourceModes.js';

test('auto selects the lower tier supported by RAM and CPU', () => {
  assert.equal(recommendedResourceMode(16, 20), 'low');
  assert.equal(recommendedResourceMode(32, 16), 'medium');
  assert.equal(recommendedResourceMode(64, 20), 'high');
  assert.equal(recommendedResourceMode(64, 8), 'low');
});

test('explicit modes expose limits and hardware support', () => {
  const medium = resolveResourceMode('medium', 32, 16);
  assert.equal(medium.effective, 'medium');
  assert.equal(medium.supported, true);
  assert.deepEqual(medium.limits, {
    max_publishers: 2,
    max_preparers: 2,
    max_total_automation_tasks: 4,
    max_active_profile_containers: 4,
  });
  assert.equal(resolveResourceMode('high', 32, 16).supported, false);
});

test('resource admission applies memory hysteresis, CPU pressure, and start spacing', () => {
  assert.equal(resourceAdmissionDecision({ memoryUsedPercent: 81, cpuPercent: 20 }).reason, 'memory_pressure');
  assert.equal(resourceAdmissionDecision({ memoryUsedPercent: 72, cpuPercent: 20, pausedForMemory: true }).reason, 'memory_pressure');
  assert.equal(resourceAdmissionDecision({ memoryUsedPercent: 69, cpuPercent: 86, pausedForMemory: true }).reason, 'cpu_pressure');
  assert.equal(resourceAdmissionDecision({ memoryUsedPercent: 50, cpuPercent: 20, millisecondsSinceLastStart: 5_000 }).reason, 'container_start_spacing');
  assert.equal(resourceAdmissionDecision({ memoryUsedPercent: 50, cpuPercent: 20, millisecondsSinceLastStart: 10_000 }).allowed, true);
});
