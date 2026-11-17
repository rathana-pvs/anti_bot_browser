import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/types/proxy.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } });
const exports = {};
vm.runInNewContext(outputText, { exports, require: () => ({}) });
const { usableOn, proxySourceCounts, freeProxies } = exports;

const proxy = (id, extra = {}) => ({ id, host: '10.0.0.1', port: 1080, profile_ids: [], ...extra });

test('a source set to PC only never serves a phone account', () => {
  const pcOnly = proxy('a', { source_use_on: 'pc' });
  assert.equal(usableOn(pcOnly, 'pc'), true);
  assert.equal(usableOn(pcOnly, 'phone'), false);
  assert.equal(usableOn(proxy('b', { source_use_on: 'both' }), 'phone'), true);
  // Proxies listed before sources existed have no source fields and stay usable everywhere.
  assert.equal(usableOn(proxy('c'), 'phone'), true);
});

test('sources are counted by name, and proxies without one belong to the first source', () => {
  const counts = proxySourceCounts([
    proxy('a', { source_id: 'src_2', source_name: 'Webshare datacenter' }),
    proxy('b', { source_id: 'src_2', source_name: 'Webshare datacenter' }),
    proxy('c'),
  ]);
  assert.equal(JSON.stringify(counts.map(c => [c.id, c.name, c.count])), JSON.stringify([['default', 'My proxies', 1], ['src_2', 'Webshare datacenter', 2]]));
});

test('each proxy is limited by its own source', () => {
  const shared = proxy('a', { account_limit: 2, profile_ids: ['x'] });
  const single = proxy('b', { account_limit: 1, profile_ids: ['y'] });
  assert.equal(freeProxies([shared, single]).map(p => p.id).join(), 'a');
});

test('picker labels are one short line without the timezone', () => {
  const { proxyOptionLabel } = exports;
  const base = { host: '130.180.238.133', port: 5515, city: 'New York City', country_code: 'US', timezone: 'America/New_York',
    geo_source: 'proxy_exit_lookup', profile_ids: ['a'], account_limit: 3 };
  assert.equal(proxyOptionLabel({ ...base, rating: { grade: 'good' } }), '130.180.238.133:5515 · New York City, US · 1/3 accounts');
  assert.equal(proxyOptionLabel({ ...base, rating: { grade: 'slow' }, account_limit: 1 }), 'Slow · 130.180.238.133:5515 · New York City, US');
});
