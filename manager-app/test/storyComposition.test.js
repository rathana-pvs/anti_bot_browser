import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const load = async path => {
  const source=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{
    compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext},
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
};
const {reelPhotos,storyUpdate}=await load('../src/services/storyComposition.ts');
const {emptyComposition}=await load('../src/types/reelCreator.ts');
const photo=id=>({image_id:id.repeat(32),movement:'auto',focal_x:.5,focal_y:.5});

test('new reels use one-photo-capable Breaking Bulletin defaults',()=>{
  const spec=emptyComposition();assert.equal(spec.version,2);assert.equal(spec.motion_style,'bulletin');
  assert.deepEqual(reelPhotos(spec),[]);
  const next={...spec,...storyUpdate(spec,[photo('a')])};
  assert.equal(reelPhotos(next).length,1);assert.equal(next.duration,10);
});
test('enabling story motion preserves legacy crop, copy and existing music',()=>{
  const legacy={...emptyComposition(),version:1,image_id:'b'.repeat(32),focal_x:.2,focal_y:.8,
    headline:'Keep the headline',audio_id:'c'.repeat(32)};
  const original=structuredClone(legacy),next={...legacy,...storyUpdate(legacy)};
  assert.equal(next.version,2);assert.equal(next.photos[0].focal_x,.2);assert.equal(next.photos[0].focal_y,.8);
  assert.equal(next.headline,legacy.headline);assert.equal(next.audio_id,legacy.audio_id);
  assert.deepEqual(legacy,original);
});
test('reordering and removing photos keeps the cover consistent without mutating the draft',()=>{
  const spec={...emptyComposition(),...storyUpdate(emptyComposition(),[photo('a'),photo('b')])};
  const next=storyUpdate(spec,[spec.photos[1],spec.photos[0]]);
  assert.equal(next.image_id,'b'.repeat(32));assert.equal(spec.photos[0].image_id,'a'.repeat(32));
  assert.equal(storyUpdate(spec,[]).image_id,null);
});
test('five-second slides set a minimum duration and never reduce a chosen longer duration',()=>{
  const photos=['a','b','c','d','e'].map(photo);
  assert.equal(storyUpdate(emptyComposition(),photos).duration,25);
  assert.equal(storyUpdate({...emptyComposition(),duration:30},photos).duration,30);
  assert.throws(()=>storyUpdate(emptyComposition(),[...photos,photo('f')]),/five/);
});

test('new reel defaults are ready for export with subtitles and five-second loops',()=>{
  const spec=emptyComposition();
  assert.equal(spec.slide_seconds,5);assert.equal(spec.fps,30);assert.equal(spec.resolution,1080);
  assert.equal(spec.show_subtitle,true);assert.equal(spec.transition,'fade');
});

test('legacy equal timing remains valid and timing changes admit every photo',()=>{
  const photos=['a','b','c','d','e'].map(photo);
  assert.equal(storyUpdate({...emptyComposition(),slide_seconds:null},photos).duration,15);
  assert.equal(storyUpdate({...emptyComposition(),slide_seconds:10},photos).duration,60);
});

const {readingSeconds,photoSeconds,normalizeTiming}=await load('../src/services/storyComposition.ts');
test('auto timing follows custom headlines, manual overrides and shared headline edits',()=>{
  assert.deepEqual([6,7,13,21].map(n=>readingSeconds('word '.repeat(n))),[3,5,7,10]);
  assert.equal(readingSeconds('ក'.repeat(70)),7);
  const spec={...emptyComposition(),auto_timing:true,headline:'Shared headline',photos:[photo('a'),{...photo('b'),headline:'word '.repeat(7)}]};
  assert.deepEqual(photoSeconds(spec),[3,5]);
  assert.equal(normalizeTiming(spec).duration,8);
  assert.equal(normalizeTiming({...spec,headline:'word '.repeat(13)}).duration,12);
  assert.equal(storyUpdate(spec,[{...spec.photos[0],duration_seconds:4},spec.photos[1]]).duration,9);
  assert.equal(normalizeTiming({...spec,auto_timing:false,duration:8}).duration,10);
});
test('recorded narration distributes seconds while retaining per-photo overrides',()=>{
  const spec={...emptyComposition(),auto_timing:true,narration_enabled:true,narration_id:'c'.repeat(32),narration_duration:12.2,
    photos:[photo('a'),photo('b')]};
  assert.deepEqual(photoSeconds(spec),[7,6]);
  assert.deepEqual(photoSeconds({...spec,photos:[{...photo('a'),duration_seconds:4},photo('b')]}),[4,9]);
  assert.equal(normalizeTiming(spec).duration,13);
});

test('Lens Report keeps version 3 and per-photo captions through reorder and timing',async()=>{
  const {photoSeconds}=await load('../src/services/storyComposition.ts');
  const first={...photo('a'),headline:'A quiet afternoon',caption:'Community spaces welcome visitors.'};
  const second={...photo('b'),headline:'More room to learn',caption:'Families gather at the local library.'};
  const lens={...emptyComposition(),version:3,template:'lens_report',auto_timing:true,slide_seconds:null,photos:[first,second],image_id:first.image_id};
  assert.deepEqual(photoSeconds(lens),[4,4]);
  const next={...lens,...storyUpdate(lens,[second,first])};
  assert.equal(next.version,3);assert.equal(next.photos[0].caption,second.caption);
  assert.equal(next.image_id,second.image_id);assert.equal(next.duration,8);
});


test('flexible Daily Brief auto timing budgets shared subtitle reading time',()=>{
  const spec={...emptyComposition(),template:'daily_brief',photo_layout:'full_bleed',auto_timing:true,
    photos:[{...photo('a'),headline:'Committee reviews proposal'}],image_id:'a'.repeat(32),
    supporting:'The committee continues reviewing proposals and no vote is scheduled.'};
  assert.deepEqual(photoSeconds(spec),[5]);
  assert.equal(normalizeTiming(spec).duration,5);
  assert.deepEqual(photoSeconds({...spec,show_subtitle:false}),[4]);
});
