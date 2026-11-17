import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/accounts/accountDevices.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } });
const exports = {};
// The real helpers, reduced to what accountDevices needs.
const accountModel = {
  isAndroid: profile => profile.runtime?.kind === 'android_user',
  phoneOf: (profile, devices) => devices.find(d => d.serial === profile.runtime.device_serial || d.aliases?.includes(profile.runtime.device_serial)),
};
vm.runInNewContext(outputText, { exports, require: name => name === './accountModel' ? accountModel : {} });
const { PC_DEVICE, deviceEntries, deviceKeyOf, deviceSummary } = exports;

const phone = (id, serial, user) => ({ id, runtime: { kind: 'android_user', device_serial: serial, android_user_id: user } });
const devices = [
  { serial: 'PIXEL6', aliases: ['192.168.1.5:5555'], label: 'Pixel 6', state: 'device', connection: 'usb' },
  { serial: 'PIXEL7', label: 'Pixel 7 Pro', state: 'offline', connection: 'wifi' },
];
const profiles = [{ id: 'pc1' }, { id: 'pc2' }, phone('a', 'PIXEL6', 10), phone('b', '192.168.1.5:5555', 11), phone('c', 'GONE', 10)];

test('an account belongs to This PC or its phone, through the phone’s other addresses too', () => {
  assert.equal(deviceKeyOf(profiles[0], devices), PC_DEVICE);
  assert.equal(deviceKeyOf(profiles[3], devices), 'PIXEL6');
  assert.equal(deviceKeyOf(profiles[4], devices), 'GONE');
});

test('devices list This PC first, then every phone with its account count', () => {
  const entries = deviceEntries(profiles, devices);
  assert.deepEqual(JSON.parse(JSON.stringify(entries.map(e => [e.key, e.label, e.count, e.online]))), [
    ['pc', 'This PC', 2, true],
    ['GONE', 'GONE', 1, null],
    ['PIXEL6', 'Pixel 6', 2, true],
    ['PIXEL7', 'Pixel 7 Pro', 0, false],
  ]);
  assert.equal(deviceSummary(entries[2]), 'USB · 2 accounts');
  assert.equal(deviceSummary(entries[3]), 'Offline · 0 accounts');
  assert.equal(deviceSummary(entries[1]), 'Not connected · 1 account');
});
