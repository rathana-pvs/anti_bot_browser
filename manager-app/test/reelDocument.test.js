import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = ts.transpileModule(readFileSync(new URL('../src/services/reelDocument.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const doc = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const reel = (...headlines) => ({ ...doc.emptyReel(), scenes: headlines.map(headline => ({ ...doc.emptyScene(), headline })) });
const asset = (id, x = .5, y = .4) => ({ id, kind: 'image', name: id, focus: { x, y, faces: 0 } });

test('scene timing matches the renderer: reading time within 3.5 to 8 seconds', () => {
  assert.equal(doc.sceneSeconds({ ...doc.emptyScene(), headline: 'Short' }), 3.5);
  assert.equal(doc.sceneSeconds({ ...doc.emptyScene(), headline: 'word '.repeat(20), detail: 'word '.repeat(25) }), 8);
  assert.equal(doc.sceneSeconds({ ...doc.emptyScene(), headline: 'one two three four five six seven eight' }), 5);
  assert.equal(doc.sceneSeconds({ ...doc.emptyScene(), headline: 'x', seconds: 6 }), 6);
  assert.equal(doc.reelSeconds(reel('a', 'b')), 7);
});

test('photos fill empty scenes in order and never overwrite a chosen photo', () => {
  const base = reel('a', 'b', 'c');
  base.scenes[1].image_id = 'kept';
  const filled = doc.fillPhotos(base, [asset('p1', .2, .3), asset('p2'), asset('p3')]);
  assert.deepEqual(filled.scenes.map(s => s.image_id), ['p1', 'kept', 'p2']);
  assert.equal(filled.scenes[0].focal_x, .2);
  assert.equal(base.scenes[0].image_id, null);
});

test('AI writing replaces words but keeps photos and the look the user chose', () => {
  const base = reel('old a', 'old b');
  base.scenes[0].image_id = 'p1';
  base.look = 'hot';
  const written = { look: 'clean', hooks: ['h'], source_label: 'Wire', caption: 'c',
    scenes: [{ headline: 'new a', detail: 'd' }, { headline: 'new b', detail: '' }, { headline: 'new c', detail: '' }] };
  const kept = doc.applyWriting(base, written, true);
  assert.equal(kept.look, 'hot');
  assert.deepEqual(kept.scenes.map(s => s.headline), ['new a', 'new b', 'new c']);
  assert.equal(kept.scenes[0].image_id, 'p1');
  assert.equal(kept.source, 'Wire');
  assert.equal(doc.applyWriting(base, written, false).look, 'clean');
});

test('scenes can be added, moved and removed within limits', () => {
  let value = reel('a');
  for (let i = 0; i < 10; i++) value = doc.addScene(value);
  assert.equal(value.scenes.length, doc.MAX_SCENES);
  value = doc.moveScene(reel('a', 'b', 'c'), 0, 2);
  assert.deepEqual(value.scenes.map(s => s.headline), ['b', 'c', 'a']);
  assert.equal(doc.moveScene(value, 0, -1), value);
  assert.equal(doc.removeScene(reel('only'), 0).scenes.length, 1);
  assert.equal(doc.removeScene(reel('a', 'b'), 0).scenes[0].headline, 'b');
});

test('choosing a hook changes only the first scene', () => {
  assert.deepEqual(doc.withHook(reel('a', 'b'), 'hook').scenes.map(s => s.headline), ['hook', 'b']);
});

test('source input is a link only when it is a single web address', () => {
  assert.equal(doc.sourceKind(' https://example.com/a?b=1 '), 'url');
  assert.equal(doc.sourceKind('https://example.com and more words'), 'text');
  assert.equal(doc.sourceKind('Plain article text'), 'text');
  assert.equal(doc.sourceKind('   '), 'empty');
});

test('titles and content checks ignore blank scenes', () => {
  assert.equal(doc.draftTitle(reel('', 'Second')), 'Second');
  assert.equal(doc.draftTitle(doc.emptyReel()), 'Untitled reel');
  assert.equal(doc.draftTitle(reel('x'.repeat(60))).length, 48);
  assert.equal(doc.hasContent(doc.emptyReel()), false);
  assert.equal(doc.hasContent(reel('a')), true);
});

test('extra photos become new scenes, and only those scenes need text', () => {
  const base = reel('a', 'b');
  base.scenes[0].image_id = 'p0';
  const { reel: placed, created } = doc.placePhotos(base, [asset('n1'), asset('n2'), asset('n3')]);
  assert.deepEqual(placed.scenes.map(s => s.image_id), ['p0', 'n1', 'n2', 'n3']);
  assert.deepEqual(created, [2, 3]);                       // scene b took the first photo; two more were created
  assert.equal(placed.scenes[2].headline, '');
  assert.equal(placed.scenes[1].headline, 'b');
});

test('photos beyond the scene limit are not added', () => {
  const photos = Array.from({ length: 12 }, (_, i) => asset(`p${i}`));
  const { reel: placed, created } = doc.placePhotos(doc.emptyReel(), photos);
  assert.equal(placed.scenes.length, doc.MAX_SCENES);
  assert.equal(created.length, doc.MAX_SCENES - 1);
});

test('writing into new scenes leaves everything else untouched', () => {
  const base = reel('keep', '', '');
  base.scenes[1].image_id = 'p1';
  const next = doc.fillScenes(base, [1, 2], [{ headline: 'x', detail: 'dx' }, { headline: 'y', detail: '' }]);
  assert.deepEqual(next.scenes.map(s => s.headline), ['keep', 'x', 'y']);
  assert.equal(next.scenes[1].image_id, 'p1');
  assert.equal(doc.fillScenes(base, [1], []).scenes[1].headline, '');  // a short AI answer is harmless
  assert.deepEqual(doc.headlines(next), ['keep', 'x', 'y']);
});

test('a new reel is a 9:16 reel with the look\'s own music', () => {
  const value = doc.emptyReel();
  assert.equal(value.ratio, '9:16');
  assert.equal(value.music, 'auto');
  assert.equal(value.music_id, null);
});

test('dateline uses the article date, else today', () => {
  const today = new Date(2026, 9, 25);
  assert.equal(doc.datelineFor('2026-03-09T08:00:00Z', today), 'Mar 9, 2026');
  assert.equal(doc.datelineFor('', today), 'Oct 25, 2026');
  assert.equal(doc.datelineFor(undefined, today), 'Oct 25, 2026');
  assert.equal(doc.datelineFor('not a date', today), 'Oct 25, 2026');
  assert.equal(doc.emptyReel().dateline, '');
});

test('AI writing carries scene kinds and adds the end card last', () => {
  const written = { look: 'clean', hooks: ['h'], source_label: 'Wire', caption: '', comment: '',
    scenes: [{ headline: 'a', detail: '', kind: 'story', stat: '', attribution: '', highlight: 'a' },
      { headline: 'votes', detail: '', kind: 'stat', stat: '7-2', attribution: '', highlight: '' }],
    end: { headline: 'Agree?', detail: '', highlight: '' } };
  const value = doc.applyWriting(reel('x'), written);
  assert.deepEqual(value.scenes.map(s => s.kind), ['story', 'stat', 'end']);
  assert.equal(value.scenes[1].stat, '7-2');
  assert.equal(value.scenes[0].highlight, 'a');
  assert.deepEqual(doc.headlines(value), ['a', 'votes']);
});

test('new scenes and photos go before the end card, which never gets a photo by itself', () => {
  const base = reel('a', 'end');
  base.scenes[1].kind = 'end';
  assert.deepEqual(doc.addScene(base).scenes.map(s => s.kind), ['story', 'story', 'end']);
  assert.equal(doc.insertIndex(base), 1);
  const filled = doc.fillPhotos(base, [asset('p1'), asset('p2')]);
  assert.deepEqual(filled.scenes.map(s => s.image_id), ['p1', null]);
  const placed = doc.placePhotos(base, [asset('p1'), asset('p2')]);
  assert.deepEqual(placed.reel.scenes.map(s => [s.kind, s.image_id]), [['story', 'p1'], ['story', 'p2'], ['end', null]]);
  assert.deepEqual(placed.created, [1]);
});

test('reading time counts a quote credit and a figure like the renderer', () => {
  const quote = { ...doc.emptyScene(), kind: 'quote', headline: 'a b', attribution: 'c d e' };
  assert.ok(doc.sceneSeconds(quote) > doc.sceneSeconds({ ...doc.emptyScene(), headline: 'a b' }));
  assert.equal(doc.sceneSeconds({ ...doc.emptyScene(), kind: 'stat', stat: '7-2', headline: 'a b c d' }), 4.1);
});

test('advanced styles drop cleared fields and empty roles, so a reset reel matches simple mode', () => {
  let styles = doc.patchStyle(undefined, 'headline', { color: '#ffee00', scale: 1.2 });
  assert.deepEqual(styles, { headline: { color: '#ffee00', scale: 1.2 } });
  styles = doc.patchStyle(styles, 'headline', { color: null });
  assert.deepEqual(styles, { headline: { scale: 1.2 } });
  assert.deepEqual(doc.patchStyle(styles, 'headline', { scale: null }), {});
  assert.deepEqual(doc.patchStyle(styles, 'headline', null), {});
  assert.deepEqual(doc.patchStyle({}, 'detail', { hidden: false }), { detail: { hidden: false } });   // a scene can show what the reel hides
});

test('a scene style lays over the reel style, and resetting a scene keeps its words and photo', () => {
  const base = reel('a');
  base.style = { headline: { color: '#111111', font: 'serif' } };
  base.scenes[0] = { ...base.scenes[0], image_id: 'p1', style: { headline: { color: '#222222' } }, transition: 'slide', shade: 1.5 };
  assert.deepEqual(doc.effectiveStyle(base, base.scenes[0], 'headline'), { color: '#222222', font: 'serif' });
  assert.ok(doc.sceneIsStyled(base.scenes[0]));
  const plain = doc.unstyledScene(base.scenes[0]);
  assert.ok(!doc.sceneIsStyled(plain));
  assert.equal(plain.headline, 'a');
  assert.equal(plain.image_id, 'p1');
  assert.ok(!doc.sceneIsStyled(doc.emptyScene()));
});

test('a moved text block counts as scene style and is cleared by a scene reset', () => {
  const scene = { ...doc.emptyScene(), headline: 'a', text_position: 0 };
  assert.ok(doc.sceneIsStyled(scene));
  assert.ok(!('text_position' in doc.unstyledScene(scene)));
});

test('a house style carries the look but never the words, photos or an uploaded track', () => {
  const base = { ...reel('Flood plan approved'), look: 'wire', tag: 'LATEST', accent: '#ff0000', motion: 'none', music: 'custom', music_id: 'm1',
    style: { headline: { color: '#ffee00' } } };
  base.scenes[0].image_id = 'p1';
  const house = doc.houseStyleOf(base, 'h1', '  Our wire ');
  assert.equal(house.name, 'Our wire');
  assert.equal(house.music, 'auto');
  assert.ok(!('scenes' in house) && !('brand' in house));
  assert.ok(doc.matchesHouseStyle(base, house));
  const other = { ...reel('Another story'), look: 'breaking', music: 'urgent_wire' };
  const applied = doc.applyHouseStyle(other, house);
  assert.equal(applied.look, 'wire');
  assert.equal(applied.scenes[0].headline, 'Another story');
  assert.equal(applied.music, 'auto');
  assert.ok(doc.matchesHouseStyle(applied, house));
  assert.equal(doc.applyHouseStyle({ ...other, music: 'custom', music_id: 'mine' }, house).music_id, 'mine');   // an uploaded track stays
  assert.ok(!doc.matchesHouseStyle({ ...applied, style: { headline: { color: '#000000' } } }, house));
  assert.ok(doc.matchesHouseStyle({ ...applied, style: { headline: { color: '#ffee00', font: undefined } } }, house));
});

test('an updated story gets new words but keeps photos, credits, styles and its end card', () => {
  const base = reel('Old headline', 'Old second');
  base.tag = null;
  base.scenes[0] = { ...base.scenes[0], image_id: 'p1', credit: 'Photo: AP', style: { headline: { color: '#ffee00' } }, transition: 'slide' };
  base.scenes.push({ ...doc.emptyScene(), kind: 'end', headline: 'Old end?', image_id: 'p9', backdrop: '#111111' });
  const written = { look: 'clean', hooks: [], caption: '', comment: '', source_label: 'Wire', source_fingerprint: 'x',
    scenes: [{ headline: 'New headline', detail: 'New detail', kind: 'story', stat: '', attribution: '', highlight: 'New' },
             { headline: 'New second', detail: '', kind: 'stat', stat: '9', attribution: '', highlight: '' }],
    end: { headline: 'What now?', detail: '', highlight: '' } };
  const next = doc.updatedStory(base, written, 'Oct 27, 2026', true);
  assert.deepEqual(next.scenes.map(s => s.headline), ['New headline', 'New second', 'What now?']);
  assert.equal(next.look, base.look);
  assert.equal(next.tag, 'UPDATE');
  assert.equal(next.dateline, 'Oct 27, 2026');
  assert.equal(next.scenes[0].image_id, 'p1');
  assert.equal(next.scenes[0].credit, 'Photo: AP');
  assert.deepEqual(next.scenes[0].style, { headline: { color: '#ffee00' } });
  assert.equal(next.scenes[2].image_id, 'p9');
  assert.equal(next.scenes[2].backdrop, '#111111');
  assert.equal(doc.updatedStory(base, written, '', false).tag, null);
});

test('a one-scene rewrite replaces only the words, and Undo has the old words to put back', () => {
  const scene = { ...doc.emptyScene(), headline: 'Old', detail: 'Old detail', kind: 'quote', attribution: 'Old speaker', image_id: 'p1', credit: 'Photo: AP', style: { headline: { color: '#ffee00' } } };
  const written = { look: 'clean', hooks: [], caption: '', comment: '', source_label: '', source_fingerprint: '', end: null,
    scenes: [{ headline: 'Families cannot wait', detail: '', kind: 'quote', stat: '', attribution: 'Mayor Lina Chhay', highlight: 'cannot wait' }] };
  const next = { ...scene, ...doc.rewrittenWords(written, 'quote') };
  assert.equal(next.headline, 'Families cannot wait');
  assert.equal(next.attribution, 'Mayor Lina Chhay');
  assert.equal(next.image_id, 'p1');
  assert.equal(next.credit, 'Photo: AP');
  assert.deepEqual(next.style, scene.style);
  assert.deepEqual({ ...next, ...doc.sceneWords(scene) }, scene);
  assert.deepEqual(doc.rewrittenWords({ ...written, scenes: [], end: { headline: 'Agree?', detail: '', highlight: '' } }, 'end'),
    { kind: 'end', headline: 'Agree?', detail: '', highlight: '', stat: '', attribution: '' });
  assert.equal(doc.rewrittenWords({ ...written, scenes: [] }, 'stat'), null);
  assert.equal(doc.rewrittenWords(written, 'end'), null);
});

test('the article decides which scene types a rewrite can give', () => {
  assert.deepEqual(doc.articleSupports('Trump said the ad was “hitting pay dirt” and Acton was “fair game.”'), { story: true, end: true, stat: false, quote: true });
  assert.deepEqual(doc.articleSupports('About 3,400 people left. The mayor said the bridge would stay closed.'), { story: true, end: true, stat: true, quote: false });
  assert.equal(doc.articleSupports('Officials moved three families to shelters.').stat, true);
  assert.equal(doc.articleSupports('Officials moved families to shelters someone said.').stat, false);   // not "one" inside "someone"
});

test('a cover crop shows the photo the way the renderer crops it', () => {
  const tall = { width: 1080, height: 1920 };
  assert.deepEqual(doc.photoView({ width: 1600, height: 900 }, tall), { x: (9 / 16) / (16 / 9), y: 1 });   // a wide photo loses its sides
  assert.deepEqual(doc.photoView({ width: 900, height: 1600 }, tall), { x: 1, y: 1 });                     // same shape: nothing lost
  assert.deepEqual(doc.photoView({ width: 900, height: 1600 }, tall, 2), { x: .5, y: .5 });               // zoomed in twice
});

test('dragging the photo moves its focal point the opposite way, within the photo', () => {
  const scene = { ...doc.emptyScene(), focal_x: .5, focal_y: .4 };
  const view = { x: .5, y: 1 };                            // half the width shows, all of the height
  assert.deepEqual(doc.dragFocal(scene, .25, .3, view), { focal_x: .25, focal_y: .4 });   // dragged right: more of the left; height has nothing to move
  assert.deepEqual(doc.dragFocal(scene, -2, 0, view), { focal_x: 1, focal_y: .4 });        // never past the edge
});

test('a new photo starts at the face the app found, unzoomed', () => {
  const zoomed = { ...reel('Vote'), scenes: [{ ...doc.emptyScene(), headline: 'Vote', image_id: 'old', zoom: 2, focal_x: .1 }] };
  const scene = doc.setPhoto(zoomed, 0, asset('new', .7, .2)).scenes[0];
  assert.deepEqual([scene.image_id, scene.focal_x, scene.focal_y, scene.zoom], ['new', .7, .2, 1]);
});
