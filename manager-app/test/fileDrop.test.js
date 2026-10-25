import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const source = ts.transpileModule(readFileSync(new URL('../src/hooks/useFileDrop.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;
const require = createRequire(import.meta.url);
function setup(disabled = false) {
  let highlight = false;
  const received = [];
  const exports = {};
  new Function('require', 'exports', source)(name => name === 'react' ? {
    useRef: value => ({ current: value }),
    useState: () => [false, value => { highlight = value; }],
  } : require(name), exports);
  return { ...exports.useFileDrop(files => received.push(files), disabled), received, highlight: () => highlight };
}
function event(files = [], types = ['Files']) {
  return { dataTransfer: { files, types }, prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {} };
}
test('a mixed photo/video drop passes every file through and prevents navigation', () => {
  const drop = setup();
  const files = [{ name: 'photo.webp' }, { name: 'video.mp4' }];
  drop.handlers.onDragEnter(event());
  assert.equal(drop.highlight(), true);
  const release = event(files);
  drop.handlers.onDrop(release);
  assert.equal(release.prevented, true);
  assert.deepEqual(drop.received, [files]);
  assert.equal(drop.highlight(), false);
});
test('busy or full upload areas consume drops without importing', () => {
  const drop = setup(true);
  drop.handlers.onDragEnter(event());
  const release = event([{ name: 'video.mp4' }]);
  drop.handlers.onDrop(release);
  assert.equal(release.prevented, true);
  assert.equal(drop.highlight(), false);
  assert.deepEqual(drop.received, []);
});
test('nested drag boundaries retain highlighting and ordinary text drags are ignored', () => {
  const drop = setup();
  const text = event([], ['text/plain']);
  drop.handlers.onDragEnter(text);
  assert.equal(text.prevented, false);
  drop.handlers.onDragEnter(event());
  drop.handlers.onDragEnter(event());
  drop.handlers.onDragLeave(event());
  assert.equal(drop.highlight(), true);
  drop.handlers.onDragLeave(event());
  assert.equal(drop.highlight(), false);
});
