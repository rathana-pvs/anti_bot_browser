import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/newsSource.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const { configuredGemini } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('Gemini settings are read at click time without writing credentials', t => {
  const previous = globalThis.localStorage;
  t.after(() => { if (previous === undefined) delete globalThis.localStorage; else globalThis.localStorage = previous; });
  let saved = '{}';
  globalThis.localStorage = { getItem: key => { assert.equal(key, 'app_api_keys'); return saved; },
    setItem: () => assert.fail('Must never rewrite settings') };
  assert.throws(() => configuredGemini(), /Settings/);
  saved = JSON.stringify({ gemini: ' dummy-test-key ', gemini_model: 'gemini-test' });
  assert.deepEqual(configuredGemini(), { api_key: 'dummy-test-key', model: 'gemini-test' });
  saved = JSON.stringify({ gemini: 'changed-test-key', gemini_model: 'gemini-test-new' });
  assert.equal(configuredGemini().model, 'gemini-test-new');
});
