import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
// These tests exercise browser requests without the desktop IPC runtime.
const environmentSource = readFileSync(new URL('../src/services/desktopEnvironment.ts', import.meta.url), 'utf8');
const environmentJs = ts.transpileModule(environmentSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const environmentUrl = `data:text/javascript;base64,${Buffer.from(environmentJs).toString('base64')}`;
const browserModule = outputText
  .replace(/import \{ invoke \} from '@tauri-apps\/api\/core';/, 'const invoke = () => { throw new Error("Unexpected desktop call"); };')
  .replace(/import \{ isDesktopApp \} from '\.\/desktopEnvironment';/, `import { isDesktopApp } from '${environmentUrl}';`);
const { detectNetworkTimezone } = await import(`data:text/javascript;base64,${Buffer.from(browserModule).toString('base64')}`);

test('direct profiles use the existing defaults endpoint', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(url, '/api/profiles/defaults');
    assert.equal(init, undefined);
    return Response.json({ host_timezone_detected: true,
      default_environment: { timezone: 'Asia/Phnom_Penh' }, timezone_options: ['Asia/Phnom_Penh'] });
  });
  assert.deepEqual(await detectNetworkTimezone({ mode: 'direct' }), {
    detected: true, timezone: 'Asia/Phnom_Penh', source: 'host', timezone_options: ['Asia/Phnom_Penh'],
  });
});

test('an older backend requires confirmation with Guatemala preselected', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ default_environment: { timezone: 'UTC' } }));
  const result = await detectNetworkTimezone({ mode: 'direct' });
  assert.equal(result.detected, false);
  assert.equal(result.timezone, 'America/Guatemala');
  assert.ok(result.timezone_options.includes('Asia/Phnom_Penh'));
  assert.ok(result.timezone_options.includes('Pacific/Honolulu'));
  assert.ok(result.timezone_options.includes('America/Phoenix'));
});

test('proxy detection explains when the running backend needs restarting', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(url, '/api/profiles/detect-timezone');
    assert.equal(init.method, 'POST');
    return Response.json({ detail: 'Method Not Allowed' }, { status: 405 });
  });
  await assert.rejects(detectNetworkTimezone({ mode: 'pool', proxy_id: 'p1' }), /Restart the manager/);
});
