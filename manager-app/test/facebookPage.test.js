import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/accounts/facebookPage.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { pageCells, seenAgo, shortCount } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

const NOW = Date.parse('2026-11-11T09:00:00Z');

test('follower counts are shown short', () => {
  assert.equal(shortCount(950), '950');
  assert.equal(shortCount(3400), '3.4K');
  assert.equal(shortCount(12_399), '12.3K');
  assert.equal(shortCount(250_000), '250K');
  assert.equal(shortCount(1_500_000), '1.5M');
});

test('a page shows its name and followers with when warming saw them', () => {
  const cells = pageCells({ name: 'News 429M', kind: 'page', followers: 3400, seen_at: '2026-11-11T07:00:00Z' }, false, NOW);
  assert.equal(cells.page, 'News 429M');
  assert.equal(cells.followers, '3.4K');
  assert.match(cells.pageHint, /^Page: News 429M · seen 2 h ago while warming$/);
  assert.match(cells.followersHint, /^3,400 followers · seen 2 h ago/);
  assert.equal(cells.sortFollowers, 3400);
});

test('a personal profile is marked and an unread count is a dash', () => {
  const cells = pageCells({ name: 'Dara Sok', kind: 'personal', seen_at: '2026-11-11T08:59:00Z' }, false, NOW);
  assert.equal(cells.page, 'Dara Sok (personal)');
  assert.equal(cells.followers, '—');
  assert.equal(cells.sortFollowers, null);
});

test('no reading yet shows dashes', () => {
  assert.equal(pageCells(null, false, NOW).page, '—');
  assert.match(pageCells(undefined, true, NOW).pageHint, /phone/);
});

test('seen times read naturally', () => {
  assert.equal(seenAgo('2026-11-11T08:59:40Z', NOW), 'just now');
  assert.equal(seenAgo('2026-11-11T08:15:00Z', NOW), '45 min ago');
  assert.equal(seenAgo('2026-11-08T09:00:00Z', NOW), '3 days ago');
  assert.equal(seenAgo(undefined, NOW), '');
});
