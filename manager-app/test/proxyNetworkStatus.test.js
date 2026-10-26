import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/accounts/networkSignal.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { networkSignal } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const profile = { id: 'p1', network: { mode: 'pool', proxy_id: 'proxy1', proxy_host: 'proxy.example', proxy_port: 1080 },
  effective_environment: { network_preflight: 'passed:proxy' } };
const sample = value => ({ success: value !== null, mbps: value, error: value === null ? 'Timed out' : undefined });
const proxy = (download, upload, extra = {}) => ({ id: 'proxy1', host: 'proxy.example', port: 1080,
  speed_test: { download: sample(download), upload: sample(upload), last_checked: '2026-10-26T12:00:00Z' }, ...extra });

test('saved passing test includes real speeds and tooltip timestamp', () => {
  const signal = networkSignal(profile, [proxy(3.456, 1)]);
  assert.equal(signal.label, 'Proxy good');
  assert.equal(signal.tone, 'ok');
  assert.equal(signal.detail, '↓3.46 ↑1 Mbps');
  assert.match(signal.hint, /Speed checked:/);
});

test('either slow direction overrides old Proxy OK preflight', () => {
  for (const [down, up] of [[.5, 3], [3, .5], [0, 3]]) {
    assert.equal(networkSignal(profile, [proxy(down, up)]).label, 'Proxy slow');
  }
});

test('partial and failed checks are never marked good', () => {
  assert.equal(networkSignal(profile, [proxy(3, null)]).label, 'Proxy test failed');
  assert.equal(networkSignal(profile, [proxy(3, null, { last_speed_quality: 'poor' })]).label, 'Proxy test failed');
  assert.equal(networkSignal(profile, [proxy(NaN, 3)]).tone, 'warn');
});

test('untested pool proxies do not inherit a speed claim from startup', () => {
  assert.equal(networkSignal(profile, [{ id: 'proxy1' }]).label, 'Speed untested');
});

test('newer connection failure overrides a passing speed check', () => {
  assert.equal(networkSignal(profile, [proxy(3, 3, { reachable: false, last_checked: '2026-10-26T13:00:00Z' })]).label, 'Proxy offline');
  assert.equal(networkSignal(profile, [proxy(3, 3, { reachable: false, last_checked: '2026-10-26T11:00:00Z' })]).label, 'Proxy good');
});

test('only the selected proxy contributes measurements', () => {
  assert.equal(networkSignal(profile, [proxy(.1, .1, { id: 'other' })]).label, 'Proxy online');
  assert.equal(networkSignal({ ...profile, network: { mode: 'direct' } }, [proxy(.1, .1)]).label, 'Direct OK');
  assert.equal(networkSignal({ ...profile, network: { ...profile.network, mode: 'custom' } }, [proxy(.1, .1)]).label, 'Proxy online');
});
