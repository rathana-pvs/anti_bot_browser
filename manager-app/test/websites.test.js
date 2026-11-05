import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/websites.ts', import.meta.url), 'utf8').replace("import { API_BASE } from './api';", "const API_BASE = '/api';"), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const web = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const draft = () => ({ enabled: true, connectionId: 'site', article: web.emptyArticle(), uniqueCaptions: true });
test('read more requires a reviewed preparation and ignores account ordering', () => {
  const value = draft();
  assert.equal(web.readMoreReady(value, 'reel', 'movie.mp4', 'Caption', ['a', 'b']), false);
  value.preparation = { ready: true };
  value.preparedFingerprint = web.readMoreFingerprint(value, 'reel', 'movie.mp4', 'Caption', ['a', 'b']);
  assert.equal(web.readMoreReady(value, 'reel', 'movie.mp4', 'Caption', ['b', 'a']), true);
  for (const args of [['photo', 'movie.mp4', 'Caption', ['a', 'b']], ['reel', 'changed.mp4', 'Caption', ['a', 'b']], ['reel', 'movie.mp4', 'Edited', ['a', 'b']], ['reel', 'movie.mp4', 'Caption', ['a']]]) {
    assert.equal(web.readMoreReady(value, ...args), false);
  }
  value.article.seo_title = 'Edited SEO';
  assert.equal(web.readMoreReady(value, 'reel', 'movie.mp4', 'Caption', ['a', 'b']), false);
});
test('reviewed caption edits preserve links while article and site changes invalidate preparation', () => {
  const value = draft();
  value.preparation = { ready: true };
  value.preparedFingerprint = web.readMoreFingerprint(value, 'live', 'movie.mp4', 'Caption', ['a']);
  value.captions = { a: 'Edited hook' };
  assert.equal(web.readMoreReady(value, 'live', 'movie.mp4', 'Caption', ['a']), true);
  value.connectionId = 'other-site';
  assert.equal(web.readMoreReady(value, 'live', 'movie.mp4', 'Caption', ['a']), false);
  assert.equal(web.readMoreReady({ ...value, enabled: false }, 'live', '', '', []), true);
});

test('connection imports separate login secrets from reusable website configuration', () => {
  const imported = web.parseWebsiteConnectionJson(JSON.stringify({ schemaVersion: 1, name: 'News', baseUrl: 'https://news.example', adapter: 'payload-news-v1',
    username: 'editor', password: 'private-password', api_token: 'private-token' }));
  assert.equal(imported.email, 'editor');
  assert.equal(imported.password, 'private-password');
  assert.equal(imported.apiToken, 'private-token');
  assert.equal(imported.configuration.baseUrl, 'https://news.example');
  assert.equal('password' in imported.configuration, false);
  assert.equal('username' in imported.configuration, false);
  assert.equal('api_token' in imported.configuration, false);
});

test('connection imports reject post lists and malformed credentials with safe errors', () => {
  for (const value of [[], null, 1, { title: 'Post', content: 'Article' }, { name: 'News', baseUrl: 'https://news.example', password: { secret: 'private-password' } }]) {
    assert.throws(() => web.parseWebsiteConnectionJson(JSON.stringify(value)), error => !error.message.includes('private-password'));
  }
});

test('management shortcuts and saved connection changes notify separate subscribers', () => {
  const previous = globalThis.window;
  globalThis.window = new EventTarget();
  try {
    let open = 0, changed = 0;
    window.addEventListener(web.MANAGE_WEBSITE_CONNECTIONS, () => open++);
    window.addEventListener(web.WEBSITE_CONNECTIONS_CHANGED, () => changed++);
    web.openWebsiteManagement();
    assert.equal(open, 1); assert.equal(changed, 0);
    web.notifyWebsiteConnectionsChanged();
    assert.equal(open, 1); assert.equal(changed, 1);
  } finally { globalThis.window = previous; }
});
