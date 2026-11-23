import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/aiSettings.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const ai = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function storage(t, initial) {
  const previous = globalThis.localStorage;
  t.after(() => { if (previous === undefined) delete globalThis.localStorage; else globalThis.localStorage = previous; });
  const box = { saved: initial };
  globalThis.localStorage = { getItem: key => { assert.equal(key, 'app_api_keys'); return box.saved; }, setItem: (_key, value) => { box.saved = value; } };
  return box;
}

test('a single key saved by an older version becomes the first of the list', t => {
  storage(t, JSON.stringify({ gemini: ' old-test-key ', gemini_model: 'gemini-3.7-flash', claude: 'gone' }));
  const settings = ai.loadAISettings();
  assert.deepEqual(settings.gemini_keys.map(({ label, key }) => ({ label, key })), [{ label: 'Key 1', key: 'old-test-key' }]);
  assert.equal(settings.gemini_model, 'gemini-3.7-flash');
});

test('every key is sent, in order, without blanks or repeats', t => {
  storage(t, '{}');
  assert.throws(() => ai.configuredAI(), /Gemini API key/);
  const settings = { gemini_model: 'gemini-3.8-flash', gemini_keys: [ai.newKeyRow('A', ' one '), ai.newKeyRow('B', ''), ai.newKeyRow('C', 'two'), ai.newKeyRow('D', 'one')] };
  assert.deepEqual(ai.providerConfig(settings), { provider: 'gemini', api_key: 'one', api_keys: ['one', 'two'], model: 'gemini-3.8-flash' });
  assert.deepEqual(ai.providerConfig(settings, 'two').api_keys, ['two']);  // a Test sends only that key
  assert.deepEqual(ai.geminiFields({ gemini_keys: [], gemini_model: 'gemini-3.8-flash' }), { api_key: '', api_keys: [], model: 'gemini-3.8-flash' });
});

test('saving drops blank rows, names unnamed ones and keeps no other provider', t => {
  const box = storage(t, '{}');
  ai.saveAISettings({ gemini_model: 'gemini-3.8-flash', gemini_keys: [ai.newKeyRow(' Work ', ' k1 '), ai.newKeyRow('Spare', ' '), ai.newKeyRow('', 'k2')] });
  assert.deepEqual(JSON.parse(box.saved), { gemini_keys: [{ label: 'Work', key: 'k1' }, { label: 'Key 2', key: 'k2' }], gemini_model: 'gemini-3.8-flash' });
  assert.deepEqual(ai.loadAISettings().gemini_keys.map(row => row.key), ['k1', 'k2']);
});
