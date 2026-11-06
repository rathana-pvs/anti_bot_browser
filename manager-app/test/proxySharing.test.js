import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/types/proxy.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { ANY_LOCATION, filterByLocation, freeProxies, hasRoom, proxyLocations, proxyHolders, proxyOptionLabel, proxyUsageLabel } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

const proxy = (id, holders, limit, extra = {}) => ({ id, host: '203.0.113.1', port: 1080, profile_ids: holders,
  profile_id: holders[0] ?? null, assigned: holders.length > 0, account_limit: limit, speed_quality: 'good', geo_source: 'unknown', ...extra });

test('a proxy has room until it reaches its account limit', () => {
  assert.equal(hasRoom(proxy('a', ['p1'], 2)), true);
  assert.equal(hasRoom(proxy('a', ['p1', 'p2'], 2)), false);
  // An account already on the proxy can keep it, even when it is full.
  assert.equal(hasRoom(proxy('a', ['p1', 'p2'], 2), 'p2'), true);
});

test('without a limit each proxy serves one account, as before', () => {
  assert.equal(hasRoom({ id: 'a', profile_id: 'p1', assigned: true }), false);
  assert.deepEqual(proxyHolders({ id: 'a', profile_id: 'p1' }), ['p1']);
});

test('free proxies list the least shared first and skip full ones', () => {
  const list = freeProxies([proxy('half', ['p1'], 2), proxy('full', ['p2', 'p3'], 2), proxy('empty', [], 2)]);
  assert.deepEqual(list.map(p => p.id), ['empty', 'half']);
});

test('pickers show usage only when proxies can be shared', () => {
  assert.equal(proxyUsageLabel(proxy('a', ['p1'], 3)), '1/3 accounts');
  assert.match(proxyOptionLabel(proxy('a', ['p1'], 3)), /· 1\/3 accounts$/);
  assert.doesNotMatch(proxyOptionLabel(proxy('a', [], 1)), /account/);
});

const at = (id, region, city, geo_source = 'proxy_exit_lookup') => ({ id, region, city, geo_source });
const pool = [at('a', 'California', 'Los Angeles'), at('b', 'California', 'Palo Alto'), at('c', 'New York', 'New York'),
  at('d', 'California', 'Los Angeles', 'unknown')];

test('location lists count states and narrow cities to the chosen state', () => {
  const all = proxyLocations(pool);
  assert.deepEqual(all.states, [{ name: 'California', count: 2 }, { name: 'New York', count: 1 }]);
  assert.deepEqual(proxyLocations(pool, 'California').cities.map(c => c.name), ['Los Angeles', 'Palo Alto']);
});

test('filtering by state or city skips unknown locations but keeps the current choice', () => {
  assert.deepEqual(filterByLocation(pool, 'California', ANY_LOCATION).map(p => p.id), ['a', 'b']);
  assert.deepEqual(filterByLocation(pool, ANY_LOCATION, 'Los Angeles').map(p => p.id), ['a']);
  assert.deepEqual(filterByLocation(pool, 'New York', ANY_LOCATION, 'a').map(p => p.id), ['a', 'c']);
  assert.equal(filterByLocation(pool, ANY_LOCATION, ANY_LOCATION).length, 4);
});
