import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/visibleInterval.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { setVisibleInterval } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

function fakeDocument() {
  const listeners = new Set();
  return {
    hidden: false,
    addEventListener: (_type, listener) => listeners.add(listener),
    removeEventListener: (_type, listener) => listeners.delete(listener),
    setHidden(value) { this.hidden = value; listeners.forEach(listener => listener()); },
    listeners,
  };
}

test('polls while visible, pauses while hidden, catches up once on return', (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  const doc = fakeDocument();
  let ticks = 0;
  const stop = setVisibleInterval(() => { ticks += 1; }, 1000, doc);

  t.mock.timers.tick(2000);
  assert.equal(ticks, 2);

  doc.setHidden(true);
  t.mock.timers.tick(5000);
  assert.equal(ticks, 2, 'no polling while hidden');

  doc.setHidden(false);
  assert.equal(ticks, 3, 'one refresh straight away on return');

  t.mock.timers.tick(1000);
  assert.equal(ticks, 4);

  stop();
  t.mock.timers.tick(5000);
  assert.equal(ticks, 4);
  assert.equal(doc.listeners.size, 0);
});

test('returning before a tick was skipped does not add an extra refresh', (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] });
  const doc = fakeDocument();
  let ticks = 0;
  const stop = setVisibleInterval(() => { ticks += 1; }, 1000, doc);
  doc.setHidden(true);
  doc.setHidden(false);
  assert.equal(ticks, 0);
  stop();
});
