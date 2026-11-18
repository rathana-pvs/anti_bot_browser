import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/smoothScreen.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { needsSmoothScreen } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('only WebKit engines get the smooth screen mirror', () => {
  // The desktop app on Linux (WebKitGTK) and Safari.
  assert.equal(needsSmoothScreen('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko)'), true);
  assert.equal(needsSmoothScreen('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'), true);
  // Chrome, Edge and Firefox already scale smoothly.
  assert.equal(needsSmoothScreen('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'), false);
  assert.equal(needsSmoothScreen('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0'), false);
  assert.equal(needsSmoothScreen('Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0'), false);
});
