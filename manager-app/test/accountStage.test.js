import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/accounts/accountStage.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { stageView } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const profile = { id: 'profile_005', account: {}, status: 'stopped' };
const health = { stage: 'warming_up', reason: 'Building account history.' };

test('job and infrastructure problems do not override the account stage', () => {
  const profiles = [
    profile,
    { ...profile, status: 'error' },
    { ...profile, restart_required: true },
    { ...profile, effective_environment: { network_preflight: 'failed: proxy timeout' } },
  ];
  for (const p of profiles) {
    assert.equal(stageView(p, health).stage, 'warming_up');
    assert.equal(stageView(p, health).reason, health.reason);
  }
});

test('confirmed authentication problems retain their account warning and evidence', () => {
  const auth = { stage: 'attention', reason: 'Facebook asked to log in or verify this account.',
    evidence: [{ execution_id: 'auth1', at: '2026-11-03T06:00:00Z', kind: 'warming', status: 'skipped_auth_required' }] };
  const result = stageView(profile, auth);
  assert.equal(result.stage, 'attention');
  assert.equal(result.evidence[0].executionId, 'auth1');
  assert.match(result.evidence[0].label, /Facebook asked to log in/);
});

test('a wrong Page or profile keeps the identity warning', () => {
  const identity = { stage: 'attention', reason: 'Facebook was showing the personal profile instead of the Page.' };
  assert.equal(stageView(profile, identity).stage, 'attention');
  assert.equal(stageView(profile, identity).reason, identity.reason);
});

test('a deliberate account hold takes priority', () => {
  const result = stageView({ ...profile, account: { on_hold: { note: 'Changing proxy' } } },
    { stage: 'attention', reason: 'Login required' });
  assert.equal(result.stage, 'on_hold');
  assert.match(result.reason, /Changing proxy/);
});
