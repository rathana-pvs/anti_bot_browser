import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/compositionDocument.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const doc = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const scene = (kind, headline, highlight = '') => ({ kind, headline, highlight });

test('a reel made before text motion gets the composer defaults', () => {
  const old = { scenes: [scene('story', 'Metro Line 4 gets the  Green light', 'green light'), scene('stat', 'Shorter trip'), scene('end', 'Would you ride it?', 'ride')] };
  assert.deepEqual(doc.withTextMotion(old).scenes.map(s => s.emphasis), ['highlight_sweep', 'underline', null]);
  assert.equal(doc.withTextMotion(old.scenes.length ? { scenes: [scene('story', 'Red lightning', 'light')] } : old).scenes[0].emphasis, 'underline');
});

test('a reel that already chose its motion is left alone', () => {
  const chosen = { scenes: [{ ...scene('story', 'Metro Line 4', 'Metro'), emphasis: null }, scene('story', 'Next')] };
  assert.equal(doc.withTextMotion(chosen), chosen);
});
