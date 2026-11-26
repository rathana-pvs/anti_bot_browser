import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const transpile = (path) => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const queueModelUrl = moduleUrl(transpile('../src/components/queue/queueModel.ts'));
const modelSource = transpile('../src/components/mini/miniStatusModel.ts')
  .replace(/from ['"]\.\.\/queue\/queueModel['"]/, `from '${queueModelUrl}'`);
const { summarize, startsIn, liveProgress, shortDuration } = await import(moduleUrl(modelSource));

const now = new Date('2026-11-26T10:00:00');
const job = (id, status, extra = {}) => ({ execution_id: id, profile_id: `p_${id}`, status, spun_caption: '', scheduled_at: '', retry_count: 0, ...extra });

test('sorts jobs into running, needs checking, waiting and done today', () => {
  const summary = summarize({
    batches: [],
    executions: [
      job('a', 'running'),
      job('b', 'uncertain'),
      job('c', 'failed'),
      job('d', 'pending', { expected_start_at: '2026-11-26T10:20:00' }),
      job('e', 'pending', { expected_start_at: '2026-11-26T10:05:00' }),
      job('f', 'published', { published_at: '2026-11-26T08:00:00' }),
      job('g', 'published', { published_at: '2026-11-25T08:00:00' }),
    ],
  }, now);
  assert.deepEqual(summary.running.map(item => item.execution_id), ['a']);
  assert.deepEqual(summary.attention.map(item => item.execution_id), ['b', 'c']);
  assert.equal(summary.waiting, 2);
  assert.equal(summary.doneToday, 1);
  assert.equal(summary.next.execution_id, 'e');
});

test('only batches with waiting jobs can be paused or resumed', () => {
  const summary = summarize({
    batches: [
      { batch_id: 'live', paused: false },
      { batch_id: 'held', paused: true },
      { batch_id: 'finished', paused: false },
    ],
    executions: [
      job('a', 'pending', { batch_id: 'live' }),
      job('b', 'pending', { batch_id: 'held' }),
      job('c', 'published', { batch_id: 'finished' }),
    ],
  }, now);
  assert.deepEqual(summary.activeBatchIds, ['live']);
  assert.deepEqual(summary.pausedBatchIds, ['held']);
});

test('start time, live progress and durations read plainly', () => {
  const at = now.getTime();
  assert.equal(startsIn(job('a', 'pending', { expected_start_at: '2026-11-26T10:04:00' }), at), 'in 4 min');
  assert.equal(startsIn(job('a', 'pending', { expected_start_at: '2026-11-26T09:00:00' }), at), 'now');
  assert.equal(startsIn(job('a', 'pending'), at), null);
  assert.equal(liveProgress(job('a', 'running', { post_type: 'live', elapsed_seconds: 30, duration_seconds: 120 })), 25);
  assert.equal(liveProgress(job('a', 'running', { post_type: 'reel' })), null);
  assert.equal(shortDuration(45_000), '45s');
  assert.equal(shortDuration(65 * 60_000), '1 h 05 min');
});
