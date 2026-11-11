import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/components/BatchSchedule.tsx', import.meta.url), 'utf8');

test('All at once is offered first and sends -1, which the backend accepts', () => {
  assert.match(source, /export const ALL_AT_ONCE = -1;/);
  const options = source.slice(source.indexOf('export const SPREAD_OPTIONS'), source.indexOf('];', source.indexOf('export const SPREAD_OPTIONS')));
  assert.match(options.split('\n')[1], /value: String\(ALL_AT_ONCE\), label: 'All at once/);
  const backend = readFileSync(new URL('../../backend/services/queue_scheduler.py', import.meta.url), 'utf8');
  assert.match(backend, /ALL_AT_ONCE = -1\nSPREAD_CHOICES_MINUTES = \(ALL_AT_ONCE, 0, 15, 30, 60, 120, 240\)/);
});
