import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/devices/phoneSwipe.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } });
const { planSwipe, wheelPixels } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

const W = 576, H = 1280;
const seeded = (seed) => () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };

const centre = { x: W / 2, y: H / 2 };

test('scrolling down swipes the finger up, starting a little way from the cursor', () => {
  const cursor = { x: W * 0.4, y: H * 0.7 };
  for (let seed = 1; seed < 200; seed += 1) {
    const points = planSwipe(120, W, H, seeded(seed), cursor);
    const [down, up] = [points[0], points.at(-1)];
    assert.equal(down.action, 0);
    assert.equal(up.action, 1);
    assert.ok(points.slice(1, -1).every(point => point.action === 2));
    assert.ok(Math.abs(down.x - cursor.x) <= W * 0.04 + 1, 'starts near the cursor across');
    assert.ok(Math.abs(down.y - cursor.y) <= H * 0.03 + 1, 'starts near the cursor down');
    assert.ok(up.y < down.y, 'finger moves up');
    assert.ok(up.at >= 140 && up.at <= 260, 'takes a finger-like time');
    for (let i = 1; i < points.length; i += 1) assert.ok(points[i].at >= points[i - 1].at);
  }
});

test('a cursor near an edge never makes the swipe touch the edge', () => {
  const corners = [{ x: 0, y: 0 }, { x: W - 1, y: H - 1 }, { x: 0, y: H - 1 }, { x: W - 1, y: 0 }];
  for (const cursor of corners) {
    for (const wheel of [5000, -5000]) {
      for (const point of planSwipe(wheel, W, H, seeded(5), cursor)) {
        assert.ok(point.x >= W * 0.08 - 1 && point.x <= W * 0.92 + 1, `x ${point.x} stays off the side edges`);
        assert.ok(point.y >= H * 0.1 - 1 && point.y <= H * 0.9 + 1, `y ${point.y} stays off the top and bottom`);
      }
    }
  }
});

test('scrolling up swipes the finger down, and distance follows the wheel within limits', () => {
  const small = planSwipe(-10, W, H, seeded(7), centre);
  const large = planSwipe(-5000, W, H, seeded(7), { x: W / 2, y: H * 0.3 });
  assert.ok(small.at(-1).y > small[0].y, 'finger moves down');
  const travel = (points) => Math.abs(points.at(-1).y - points[0].y);
  assert.ok(Math.abs(travel(small) - H * 0.15) <= 2, 'short scrolls still move 15% of the screen');
  assert.ok(Math.abs(travel(large) - H * 0.45) <= 2, 'long scrolls are capped at 45%');
});

test('two swipes for the same scroll do not repeat the same path', () => {
  const a = planSwipe(120, W, H, seeded(3), centre);
  const b = planSwipe(120, W, H, seeded(11), centre);
  assert.notDeepEqual(a.map(p => [p.x, p.y]), b.map(p => [p.x, p.y]));
});

test('nothing to do without scrolling or a known screen size', () => {
  assert.deepEqual(planSwipe(0, W, H), []);
  assert.deepEqual(planSwipe(100, 0, 0), []);
});

test('wheel distance is normalised to pixels', () => {
  assert.equal(wheelPixels(3, 1, 800), 48);
  assert.equal(wheelPixels(1, 2, 800), 800);
  assert.equal(wheelPixels(100, 0, 800), 100);
});
