import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = ts.transpileModule(readFileSync(new URL('../src/services/requestTimeout.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;
const exports = {};
new Function('exports', source)(exports);
const { withRequestTimeout } = exports;

test('a stalled request stops loading even if fetch ignores cancellation', async () => {
  let signal;
  await assert.rejects(withRequestTimeout(value => {
    signal = value;
    return new Promise(() => {});
  }, undefined, 10), /timed out/);
  assert.equal(signal.aborted, true);
});

test('the timeout also covers a stalled response body', async () => {
  await assert.rejects(withRequestTimeout(async () => {
    const response = { json: () => new Promise(() => {}) };
    return response.json();
  }, undefined, 10), /timed out/);
});

test('switching attempts cancels the previous request', async () => {
  const controller = new AbortController();
  let signal;
  const request = withRequestTimeout(value => {
    signal = value;
    return new Promise(() => {});
  }, controller.signal);
  controller.abort();
  await assert.rejects(request, /cancelled/);
  assert.equal(signal.aborted, true);
});

test('an already cancelled request never starts', async () => {
  const controller = new AbortController();
  controller.abort();
  let started = false;
  await assert.rejects(withRequestTimeout(async () => {
    started = true;
    return [];
  }, controller.signal), /cancelled/);
  assert.equal(started, false);
});

test('a retry can succeed after the previous request times out', async () => {
  await assert.rejects(withRequestTimeout(() => new Promise(() => {}), undefined, 10), /timed out/);
  assert.deepEqual(await withRequestTimeout(async () => ['01_composer.png']), ['01_composer.png']);
});
