import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/restChoices.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { DEFAULT_REST_SECONDS, restLabel, restOptions, savedRest } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('rest times read naturally', () => {
  assert.equal(restLabel(0), 'No rest');
  assert.equal(restLabel(30), '30 s');
  assert.equal(restLabel(120), '2 min');
  assert.equal(restLabel(1600), '26 min 40 s');
  assert.equal(restLabel(3600), '1 hour');
});

test('the default is 30 seconds, and an old untouched 60 becomes it', () => {
  assert.equal(DEFAULT_REST_SECONDS, 30);
  assert.equal(savedRest(null, false), 30);
  assert.equal(savedRest('60', false), 30);
  assert.equal(savedRest('60', true), 60);
  assert.equal(savedRest('120', false), 120);
  assert.equal(savedRest('junk', true), 30);
  assert.equal(savedRest('99999', true), 3600);
});

test('a saved value outside the list is kept as a choice', () => {
  const values = restOptions(1600).map(option => Number(option.value));
  assert.ok(values.includes(1600) && values.includes(30));
  assert.deepEqual(values, [...values].sort((a, b) => a - b));
});
