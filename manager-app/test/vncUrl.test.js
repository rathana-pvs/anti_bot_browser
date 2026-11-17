import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/vncUrl.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { vncWebSocketUrl } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('LAN viewers use their authenticated origin, without exposing a container port', () => {
  assert.equal(vncWebSocketUrl('profile_001', 6081, new URL('http://192.168.1.20:5173')),
    'ws://192.168.1.20:5173/_lan/vnc/profile_001');
  assert.equal(vncWebSocketUrl('profile_002', 6082, new URL('https://192.168.1.20:8443')),
    'wss://192.168.1.20:8443/_lan/vnc/profile_002');
});

test('desktop and loopback viewers keep their existing local display connections', () => {
  for (const url of ['http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost']) {
    assert.equal(vncWebSocketUrl('profile_001', 6081, new URL(url)), 'ws://127.0.0.1:6081');
  }
  for (const hostname of ['127.0.0.1', 'localhost', '[::1]']) {
    assert.equal(vncWebSocketUrl('profile_001', 6081, new URL(`http://${hostname}:5173`)), `ws://${hostname}:6081`);
  }
});
