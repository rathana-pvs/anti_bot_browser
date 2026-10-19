import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/backendStartup.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const { waitForBackend, abortableDelay } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
function options(patch = {}) {
  let clock = 0;
  return { signal: new AbortController().signal, timeoutMs: 3000, retryMs: 750,
    now: () => clock, delay: async ms => { clock += ms; }, ...patch };
}
test('cold startup retries connection refusal and temporary server errors without reporting an error', async () => {
  const results = [new TypeError('Load failed'), 503, 502, 200];
  let calls = 0;
  await waitForBackend(async () => {
    const result = results[calls++];
    if (result instanceof Error) throw result;
    return result;
  }, options());
  assert.equal(calls, 4);
});
test('responsive backend does not need a grace delay or repeated probe', async () => {
  let calls = 0;
  await waitForBackend(async () => { calls++; return 200; }, options({ delay: () => assert.fail('Unexpected delay') }));
  assert.equal(calls, 1);
});
test('persistent startup failure produces one actionable failure after its bounded grace period', async () => {
  let calls = 0;
  await assert.rejects(waitForBackend(async () => { calls++; throw new TypeError('Load failed'); }, options()), /backend has not responded yet/);
  assert.equal(calls, 4);
});
test('authentication failures remain visible immediately', async () => {
  for (const status of [401, 403]) {
    await assert.rejects(waitForBackend(async () => status, options({ delay: () => assert.fail('Authentication must not be hidden') })), /authenticate/);
  }
});
test('cleanup cancels an in-flight probe without mounting panels or reporting a startup error', async () => {
  const controller = new AbortController();
  const promise = waitForBackend(async () => { controller.abort(); return 200; }, options({ signal: controller.signal }));
  await assert.rejects(promise, error => error.name === 'AbortError');
});
test('a hung probe cannot block startup retry indefinitely', async () => {
  let calls = 0;
  await waitForBackend(async () => { calls++; return calls === 1 ? new Promise(() => {}) : 200; }, options({ requestTimeoutMs: 5 }));
  assert.equal(calls, 2);
});
test('retry sleep is interruptible on unmount', async () => {
  const controller = new AbortController();
  const sleep = abortableDelay(10000, controller.signal);
  controller.abort();
  await assert.rejects(sleep, error => error.name === 'AbortError');
});
