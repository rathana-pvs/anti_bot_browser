import test from 'node:test';
import assert from 'node:assert/strict';

import {
  countBufferedPreparations,
  hasBufferedPreparation,
  orderedDueExecutions,
  shuffledProfileOrder,
} from '../profilePipeline.js';

test('profile order is shuffled once and avoids previous batch tail at the front', () => {
  const randomValues = [0.8, 0.1, 0.5, 0.0];
  const order = shuffledProfileOrder(['p1', 'p2', 'p3', 'p4'], 'p1', () => randomValues.shift() ?? 0);
  assert.deepEqual(new Set(order), new Set(['p1', 'p2', 'p3', 'p4']));
  assert.notEqual(order[0], 'p1');
});

test('due work follows persisted profile order rather than profile id order', () => {
  const execution = (id, profileId, status = 'pending') => ({
    execution_id: id,
    profile_id: profileId,
    status,
    scheduled_at: '2026-09-25T10:00:00.000Z',
  });
  const queue = {
    daily_batches: [{
      profile_execution_order: ['p4', 'p2', 'p1'],
      posts: [{ executions: [execution('one', 'p1'), execution('four', 'p4'), execution('two', 'p2')] }],
    }],
  };
  assert.deepEqual(
    orderedDueExecutions(queue, Date.parse('2026-09-25T11:00:00.000Z')).map((item) => item.execution_id),
    ['four', 'two', 'one'],
  );
});

test('ready profiles are counted toward configurable preparation buffers', () => {
  const queue = { daily_batches: [{ posts: [{ executions: [
    { execution_id: 'ready', status: 'ready' },
    { execution_id: 'next', status: 'pending' },
  ] }] }] };
  assert.equal(hasBufferedPreparation(queue, 'next'), true);
  assert.equal(countBufferedPreparations(queue, 'next'), 1);
  assert.equal(hasBufferedPreparation({ daily_batches: [] }), false);
});
