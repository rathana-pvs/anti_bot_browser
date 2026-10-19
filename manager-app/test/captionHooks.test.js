import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/captionHooks.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const { hookFingerprint, hookIsApproved } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const caption = 'Original caption https://example.com?a=1';
function draft() {
  const value = { enabled: true, selected: 'An opening sentence', language: 'English' };
  value.sourceFingerprint = hookFingerprint(caption, 'photo.jpg', 'photo', value);
  value.approval = { approval_id: 'id', source_revision: 'revision', final_caption: value.selected + '\n\n' + caption };
  return value;
}
test('disabled generation preserves scheduling eligibility', () => {
  assert.equal(hookIsApproved(caption, '', 'photo'), true);
  assert.equal(hookIsApproved(caption, '', 'photo', { enabled: false }), true);
});
test('enabled hooks require a saved matching approval', () => {
  assert.equal(hookIsApproved(caption, 'photo.jpg', 'photo', { enabled: true }), false);
  assert.equal(hookIsApproved(caption, 'photo.jpg', 'photo', draft()), true);
});
test('changing source caption, media, type, context, or selected hook invalidates approval', () => {
  assert.equal(hookIsApproved(caption + ' changed', 'photo.jpg', 'photo', draft()), false);
  assert.equal(hookIsApproved(caption, 'other.jpg', 'photo', draft()), false);
  assert.equal(hookIsApproved(caption, 'photo.jpg', 'reel', draft()), false);
  assert.equal(hookIsApproved(caption, 'photo.jpg', 'photo', { ...draft(), tone: 'Formal' }), false);
  assert.equal(hookIsApproved(caption, 'photo.jpg', 'photo', { ...draft(), selected: 'Edited hook' }), false);
});
