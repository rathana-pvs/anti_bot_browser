import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/devices/phoneReadiness.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { facebookReadiness } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

const account = (steps, onHold = null) => ({ id: 'p', account: { on_hold: onHold }, android_setup: { steps } });

test('an account through Get Facebook ready is ready', () => {
  assert.deepEqual(facebookReadiness(account({ login: { status: 'passed' }, facebook_ready: { status: 'passed' } })),
    { label: 'Ready', tone: 'ready' });
});

test('each earlier stage says what is missing', () => {
  assert.equal(facebookReadiness(account({ facebook_ready: { status: 'running' } })).label, 'Getting ready…');
  assert.equal(facebookReadiness(account({ save_details: { status: 'passed' } })).label, 'Not logged in');
  assert.equal(facebookReadiness(account({ login: { status: 'passed' } })).label, 'Logged in, not ready');
  assert.equal(facebookReadiness(account({})).label, 'Setup not finished');
  assert.equal(facebookReadiness({ id: 'p' }).label, 'Setup not finished');
});

test('a failed Get Facebook ready shows its reason', () => {
  const state = facebookReadiness(account({ login: { status: 'passed' }, facebook_ready: { status: 'failed', reason: 'picker: no Allow' } }));
  assert.deepEqual(state, { label: 'Not ready', tone: 'warn', detail: 'picker: no Allow' });
});

test('an account on hold is not shown as ready', () => {
  const state = facebookReadiness(account({ facebook_ready: { status: 'passed' } }, { since: 'x', note: 'logged out' }));
  assert.deepEqual(state, { label: 'On hold', tone: 'warn', detail: 'logged out' });
});

test('a user with no linked account only shows the app is installed', () => {
  assert.deepEqual(facebookReadiness(undefined), { label: 'Installed', tone: 'muted' });
});
