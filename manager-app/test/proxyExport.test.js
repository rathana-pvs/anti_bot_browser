import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/types/proxy.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { proxyIpListText } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

test('exports one IP per line with no port or credentials', () => {
  const text = proxyIpListText([
    { host: '203.0.113.10', port: 5859, username: 'user', password: 'secret' },
    { host: '198.51.100.20', port: 6416, username: '', password: '' },
  ]);
  assert.equal(text, '203.0.113.10\n198.51.100.20\n');
});

test('proxies sharing an IP appear once', () => {
  assert.equal(proxyIpListText([{ host: '203.0.113.10', port: 1 }, { host: '203.0.113.10', port: 2 }]), '203.0.113.10\n');
});
