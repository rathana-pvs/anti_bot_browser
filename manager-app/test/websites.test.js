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
  const value = { ...draft(), uniqueCaptions: false };
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
  const value = { ...draft(), uniqueCaptions: false };
  value.preparation = { ready: true };
  value.preparedFingerprint = web.readMoreFingerprint(value, 'live', 'movie.mp4', 'Caption', ['a']);
  value.captions = { a: 'Edited hook' };
  assert.equal(web.readMoreReady(value, 'live', 'movie.mp4', 'Caption', ['a']), true);
  value.connectionId = 'other-site';
  assert.equal(web.readMoreReady(value, 'live', 'movie.mp4', 'Caption', ['a']), false);
  assert.equal(web.readMoreReady({ ...value, enabled: false, uniqueCaptions: false }, 'live', '', '', []), true);
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
test('the supplied caption only matters without unique captions', () => {
  const unique = draft(), shared = { ...draft(), uniqueCaptions: false };
  assert.equal(web.readMoreFingerprint(unique, 'reel', 'm', 'Caption', ['a']), web.readMoreFingerprint(unique, 'reel', 'm', 'Edited', ['a']));
  assert.notEqual(web.readMoreFingerprint(shared, 'reel', 'm', 'Caption', ['a']), web.readMoreFingerprint(shared, 'reel', 'm', 'Edited', ['a']));
});
test('saved captions are reused only for the same text and selected accounts', () => {
  const value = { ...draft(), captions: { a: 'Hook A', gone: 'Hook gone' } };
  value.captionSource = web.captionSourceOf(value.article, 'Caption');
  assert.deepEqual(web.knownCaptions(value, 'Caption', ['a', 'new']), { a: 'Hook A' });
  assert.deepEqual(web.knownCaptions(value, 'Edited', ['a']), {});
  assert.deepEqual(web.knownCaptions({ ...value, uniqueCaptions: false }, 'Caption', ['a']), {});
  assert.deepEqual(web.knownCaptions({ ...value, article: { ...value.article, content: 'Changed' } }, 'Caption', ['a']), {});
});
test('unique captions work without a website and block posting until generated', () => {
  const value = { ...draft(), enabled: false };
  assert.equal(web.readMoreReady(value, 'reel', 'm', 'Caption', ['a']), false);
  assert.deepEqual(web.readMoreSelection(value), {});
  value.captionsPreparationId = 'captions-1'; value.captions = { a: 'Hook A' };
  value.captionsFor = web.captionsFingerprint(value, 'reel', 'm', 'Caption', ['a']);
  assert.equal(web.readMoreReady(value, 'reel', 'm', 'Caption', ['a']), true);
  assert.deepEqual(web.readMoreSelection(value), { read_more: { preparation_id: 'captions-1', captions: { a: 'Hook A' } } });
  assert.equal(web.readMoreReady(value, 'reel', 'm', 'Caption', ['a', 'b']), false);
  assert.equal(web.readMoreReady(value, 'reel', 'other', 'Caption', ['a']), false);
});
test('with Read more on, captions come first and the website preparation is what posts', () => {
  const value = { ...draft(), preparation: { id: 'site-1', ready: true }, captionsPreparationId: 'captions-1', captions: { a: 'Hook A' } };
  value.preparedFingerprint = web.readMoreFingerprint(value, 'reel', 'm', 'Caption', ['a']);
  assert.equal(web.readMoreReady(value, 'reel', 'm', 'Caption', ['a']), false);
  value.captionsFor = web.captionsFingerprint(value, 'reel', 'm', 'Caption', ['a']);
  assert.equal(web.readMoreReady(value, 'reel', 'm', 'Caption', ['a']), true);
  assert.equal(web.readMoreSelection(value).read_more.preparation_id, 'site-1');
});
test('nothing is sent to the queue when both options are off', () => {
  assert.deepEqual(web.readMoreSelection({ ...draft(), enabled: false, uniqueCaptions: false }), {});
  assert.deepEqual(web.readMoreSelection(undefined), {});
});
test('drafts saved under the old default do not start asking for unique captions', () => {
  assert.equal(web.savedReadMore(draft()).uniqueCaptions, false);
  assert.equal(web.savedReadMore({ ...draft(), captionsPreparationId: 'c' }).uniqueCaptions, true);
  assert.equal(web.savedReadMore({ ...draft(), preparation: { ready: true } }).uniqueCaptions, true);
  assert.equal(web.savedReadMore(undefined), undefined);
});
test('the posting message names only the step that is missing', () => {
  const value = { ...draft(), enabled: false, article: { ...web.emptyArticle(), content: 'Article' } };
  assert.equal(web.readMoreProblem(undefined, 'reel', 'm', 'Caption', ['a']), '');
  assert.equal(web.readMoreProblem({ ...value, uniqueCaptions: false }, 'reel', 'm', 'Caption', ['a']), '');
  assert.equal(web.readMoreProblem(value, 'reel', 'm', 'Caption', ['a']), 'Generate unique captions for every selected account.');
  assert.match(web.readMoreProblem({ ...value, enabled: true }, 'reel', 'm', 'Caption', ['a']), /^Generate unique captions, then publish/);
  assert.match(web.readMoreProblem({ ...value, enabled: true, uniqueCaptions: false }, 'reel', 'm', 'Caption', ['a']), /^Publish the article/);
  const generated = { ...value, enabled: true, captionsPreparationId: 'c', captions: { a: 'Written' } };
  generated.captionsFor = web.captionsFingerprint(generated, 'reel', 'm', 'Caption', ['a']);
  assert.match(web.readMoreProblem(generated, 'reel', 'm', 'Caption', ['a']), /^Publish the article/);
});
test('a published article missing from the loaded list still shows in the dropdown', () => {
  const label = id => `listed ${id}`;
  const values = (articles, article) => web.articleOptions(articles, article, label).map(item => item.value);
  assert.deepEqual(values([], { id: '', title: '' }), ['new']);
  assert.deepEqual(values([{ id: 'x', title: 'X' }], { id: 'x', title: 'X' }), ['new', 'x']);
  assert.deepEqual(web.articleOptions([], { id: 'a1', title: 'Bridge' }, label)[1], { value: 'a1', label: 'Bridge' });
});
test('a linked reel keeps the published article unless the story changed', () => {
  const article = { ...web.emptyArticle(), id: '7', title: 'Edited title', content: 'Old text', slug: 'story', tags: 'news', cover_file: 'cover.jpg' };
  const link = { connection_id: 'site', url: 'https://news.example/story', article, source_title: 'Studio title', source_text: 'Old text' };
  assert.deepEqual(web.linkedArticle(link, { title: 'Studio title', text: 'Old text' }), article);
  const changed = web.linkedArticle(link, { title: 'New title', text: 'New text' });
  assert.deepEqual([changed.id, changed.title, changed.content, changed.slug, changed.tags, changed.cover_file], ['7', 'New title', 'New text', 'story', 'news', 'cover.jpg']);
  assert.equal(web.linkedArticle(link, { title: '', text: 'New text' }).title, 'Edited title');
  assert.deepEqual(web.linkedArticle(link, { title: 'New title', text: '' }), article);
});
test('excerpts end at a sentence or word', () => {
  assert.equal(web.excerptOf('Short  text.'), 'Short text.');
  const sentence = 'The old town bridge reopened today after months of work. ';
  assert.equal(web.excerptOf(sentence + 'Residents came out to walk across it again and many stayed all afternoon by the water to celebrate the opening with family.', 100), sentence.trim());
  assert.equal(web.excerptOf('one two three four five six seven eight nine ten', 20), 'one two three four');
  assert.equal(web.excerptOf('x'.repeat(50), 20), 'x'.repeat(20));
});
test('the website starts as the last used, or the only one', () => {
  const sites = [{ id: 'a' }, { id: 'b' }];
  assert.equal(web.defaultWebsite(sites, 'b'), 'b');
  assert.equal(web.defaultWebsite(sites, 'gone'), '');
  assert.equal(web.defaultWebsite([{ id: 'a' }], 'gone'), 'a');
  assert.equal(web.defaultWebsite([], ''), '');
});
test('the article summary names what is missing', () => {
  const article = { ...web.emptyArticle(), title: 'T', content: 'C', cover_id: '1' };
  assert.deepEqual(web.articleMissing(web.emptyArticle()), ['title', 'article text', 'cover image']);
  assert.deepEqual(web.articleMissing(article), []);
  assert.deepEqual(web.articleMissing(article, { 'custom_fields.topic': { required: true, label: 'Topic' }, excerpt: { required: true }, tags: {} }), ['Topic', 'excerpt']);
  assert.deepEqual(web.articleMissing(article, { 'custom_fields.topic': { required: true, default: 'news' }, title: { required: true } }), []);
});
