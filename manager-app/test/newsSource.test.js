import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/newsSource.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const { newsSourceFingerprint, configuredGemini } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('source identity matches backend Unicode JSON hashing and changes with language or source', async () => {
  globalThis.crypto ??= webcrypto;
  const value = { text: 'ព័ត៌មានថ្មីៗ\nUS politics', source_url: 'https://example.com/news', language: 'Khmer' };
  const expected = createHash('sha256').update(JSON.stringify([value.text, value.source_url, value.language])).digest('hex');
  assert.equal(await newsSourceFingerprint(value), expected);
  assert.notEqual(await newsSourceFingerprint({ ...value, language: 'English' }), expected);
  assert.notEqual(await newsSourceFingerprint({ ...value, text: 'Changed article text' }), expected);
});

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
