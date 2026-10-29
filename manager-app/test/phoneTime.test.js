import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/devices/phoneTime.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { sameTime, formatUtcOffset, phoneTimeReport } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

// Side by side: every account runs at once, so one phone time must fit them all.
const phone = (zone, auto = false, current = null) => ({ serial: 'S1', one_account_at_a_time: false, health: { current_user: current, timezone: { zone, auto } } });
const isolatedPhone = (zone, current) => ({ ...phone(zone, false, current), one_account_at_a_time: true });
const account = (id, proxyId, serial = 'S1', user = 11) => ({ id, name: id, network: { proxy_id: proxyId }, runtime: { device_serial: serial, android_user_id: user } });
const proxies = [
  { id: 'vn', timezone: 'Asia/Ho_Chi_Minh' }, { id: 'kh', timezone: 'Asia/Phnom_Penh' },
  { id: 'ny', timezone: 'America/New_York' }, { id: 'none', timezone: null },
];

test('zones with the same clock time match even with different names', () => {
  assert.equal(sameTime('Asia/Phnom_Penh', 'Asia/Ho_Chi_Minh'), true);
  assert.equal(sameTime('Asia/Phnom_Penh', 'America/New_York'), false);
  assert.equal(sameTime('Not/AZone', 'UTC'), false);
  assert.equal(formatUtcOffset('Asia/Kolkata'), 'UTC+5:30');
  assert.equal(formatUtcOffset('UTC'), 'UTC+0');
});

test('a phone matching all its accounts needs nothing', () => {
  const report = phoneTimeReport(phone('Asia/Phnom_Penh'), [account('a', 'vn'), account('b', 'kh'), account('c', 'none')], proxies);
  assert.equal(report.accounts.length, 2);
  assert.deepEqual([report.mixed, report.mismatched.length, report.suggested], [false, 0, null]);
});

test('a phone on the wrong time suggests the accounts’ time', () => {
  const report = phoneTimeReport(phone('America/New_York'), [account('a', 'vn'), account('other', 'ny', 'S2')], proxies);
  assert.deepEqual(report.mismatched.map(entry => entry.profile.id), ['a']);
  assert.equal(report.suggested, 'Asia/Ho_Chi_Minh');
});

test('automatic time zone is locked to the accounts’ time', () => {
  assert.equal(phoneTimeReport(phone('Asia/Phnom_Penh', true), [account('a', 'vn')], proxies).suggested, 'Asia/Ho_Chi_Minh');
});

test('accounts needing different times are mixed and get no single suggestion', () => {
  const report = phoneTimeReport(phone('Asia/Phnom_Penh'), [account('a', 'vn'), account('b', 'ny')], proxies);
  assert.equal(report.mixed, true);
  assert.equal(report.suggested, null);
});

test('one account at a time: mixed times are fine and only the account on screen must match', () => {
  const accounts = [account('la', 'vn', 'S1', 11), account('ny', 'ny', 'S1', 12)];
  const onNy = phoneTimeReport(isolatedPhone('America/New_York', 12), accounts, proxies);
  assert.deepEqual([onNy.isolated, onNy.mixed, onNy.mismatched.length, onNy.suggested], [true, false, 0, null]);
  const stale = phoneTimeReport(isolatedPhone('America/New_York', 11), accounts, proxies);
  assert.deepEqual(stale.mismatched.map(entry => entry.profile.id), ['la']);
  assert.equal(stale.suggested, 'Asia/Ho_Chi_Minh');
});
