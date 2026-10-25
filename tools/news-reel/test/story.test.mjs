import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {validateStory, timeline} from '../src/story.mjs';
const sample=JSON.parse(readFileSync(new URL('../sample.json',import.meta.url)));
test('story has contiguous frame-exact timing and a 30-second finish',()=>{
  const scenes=timeline(sample);
  assert.equal(scenes[0].from,0);
  for(let i=1;i<scenes.length;i++) assert.equal(scenes[i].from,scenes[i-1].from+scenes[i-1].durationInFrames);
  assert.equal(scenes.at(-1).from+scenes.at(-1).durationInFrames,900);
});
test('requires attribution and bounds copy for the template',()=>{
  assert.throws(()=>validateStory({...sample,source:''}),/source/);
  const scenes=structuredClone(sample.scenes);scenes[0].title='word '.repeat(25);
  assert.throws(()=>validateStory({...sample,scenes}),/title/);
});
test('blocks external media and excessive reading speed',()=>{
  const scenes=structuredClone(sample.scenes);scenes[1].image='https://example.com/p.jpg';
  assert.throws(()=>validateStory({...sample,scenes}),/local/);
  delete scenes[1].image;scenes[1].seconds=3;scenes[1].body='One two three four five six seven eight nine ten eleven twelve thirteen.';
  assert.throws(()=>validateStory({...sample,scenes}),/words per second/);
});
