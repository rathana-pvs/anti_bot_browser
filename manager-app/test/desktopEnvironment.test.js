import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const transpile = (path) => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const environmentUrl = moduleUrl(transpile('../src/services/desktopEnvironment.ts'));
const { isDesktopApp } = await import(environmentUrl);
const apiSource = transpile('../src/services/api.ts')
  .replace(/import \{ isDesktopApp \} from '\.\/desktopEnvironment';/, `import { isDesktopApp } from '${environmentUrl}';`)
  .replace(/import \{ invoke \} from '@tauri-apps\/api\/core';/,
    'const invoke = (...args) => globalThis.testDesktopInvoke(...args);');
const api = await import(moduleUrl(apiSource));

test('browser sessions on arbitrary hosts and ports never invoke desktop IPC', async (t) => {
  const originalWindow = globalThis.window;
  t.after(() => {
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
    delete globalThis.testDesktopInvoke;
  });
  globalThis.testDesktopInvoke = () => { assert.fail('Browser session invoked desktop IPC'); };
  for (const hostname of ['localhost', '127.0.0.1', '192.168.1.10', 'manager.example.com']) {
    for (const port of ['5173', '3001', '5174', '4173', '3002', '']) {
      const fetch = () => {};
      globalThis.window = { location: { protocol: 'http:', hostname, port }, fetch };
      assert.equal(isDesktopApp(), false, `${hostname}:${port}`);
      await api.initializeBackendSession();
      assert.equal(api.API_BASE, '/api');
      assert.equal(api.BACKEND_BASE, '');
      assert.equal(window.fetch, fetch);
    }
  }
});

test('missing window is safe and Tauri markers identify desktop sessions', (t) => {
  const originalWindow = globalThis.window;
  t.after(() => {
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  });
  delete globalThis.window;
  assert.equal(isDesktopApp(), false);
  const location = { protocol: 'http:', hostname: 'localhost', port: '5174' };
  for (const markers of [
    { __TAURI_INTERNALS__: {} }, { __TAURI__: {} },
    { location: { ...location, protocol: 'tauri:' } },
    { location: { ...location, hostname: 'tauri.localhost' } },
  ]) {
    globalThis.window = { location, ...markers };
    assert.equal(isDesktopApp(), true);
  }
});

test('desktop initialization still loads the API session and attaches its token', async (t) => {
  const originalWindow = globalThis.window;
  t.after(() => {
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
    delete globalThis.testDesktopInvoke;
  });
  const calls = [];
  globalThis.window = {
    __TAURI_INTERNALS__: {},
    location: { protocol: 'http:', hostname: 'localhost', port: '5174' },
    fetch: async (...args) => { calls.push(args); return Response.json({}); },
  };
  globalThis.testDesktopInvoke = async (command) => {
    assert.equal(command, 'get_api_session');
    return { baseUrl: 'http://127.0.0.1:45000', token: 'test-token' };
  };
  await api.initializeBackendSession();
  assert.equal(api.API_BASE, 'http://127.0.0.1:45000/api');
  await window.fetch(`${api.API_BASE}/profiles`);
  assert.equal(calls[0][1].headers.get('X-Manager-Token'), 'test-token');
});
