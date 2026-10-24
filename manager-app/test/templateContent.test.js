import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const load=async path=>import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')}`);
const {contentSettingsKey,contentFitKey,contentReviewKey,applyContentProposal,moreReadingTime}=await load('../src/services/templateContent.ts');
const {emptyComposition,emptyEditorial}=await load('../src/types/reelCreator.ts');
const composition=()=>({...emptyComposition(),version:3,template:'lens_report',layout_revision:2,aspect_ratio:'4:5',lens_theme:'dark',
  photos:[{image_id:'a'.repeat(32),headline:'Manual headline',caption:'Manual caption',movement:'pan_left',zoom:1.3,offset_y:.2}],image_id:'a'.repeat(32)});

test('appearance changes do not invalidate content or trigger fit requests',()=>{
  const spec=composition(),editorial=emptyEditorial();
  const next={...spec,lens_theme:'light',accent:'#ff0000',show_progress:false,brand:'New brand',resolution:720,
    photos:spec.photos.map(photo=>({...photo,zoom:1.8,offset_x:.2}))};
  assert.equal(contentSettingsKey(spec),contentSettingsKey(next));
  assert.equal(contentFitKey(spec),contentFitKey(next));
  assert.equal(contentReviewKey(editorial,spec),contentReviewKey(editorial,next));
});
test('template, ratio, timing, tone, visibility and photo changes require rechecking',()=>{
  const spec=composition(),key=contentSettingsKey(spec);
  for(const change of [{template:'newsroom'},{aspect_ratio:'9:16'},{duration:15},{show_subtitle:false},{slide_seconds:3},
    {photos:[]},{photos:[{...spec.photos[0],duration_seconds:7}]}]){
    assert.notEqual(contentSettingsKey({...spec,...change}),key,JSON.stringify(change));
  }
  assert.notEqual(contentSettingsKey(spec,'explainer'),key);
});
test('pending suggestions cannot silently replace concurrent manual edits',()=>{
  const spec=composition(),editorial=emptyEditorial(),key=contentReviewKey(editorial,spec);
  for(const next of [{...editorial,text:'Changed source'},{...editorial,post_caption:'My edited caption'},
    {...editorial,publisher:'Correct attribution'},{...editorial,tone:'concise'}])assert.notEqual(contentReviewKey(next,spec),key);
  for(const next of [{...spec,headline:'Changed fallback'}, {...spec,photos:[{...spec.photos[0],headline:'New scene title'}]},
    {...spec,photos:[{...spec.photos[0],caption:'Edited scene caption'}]}])assert.notEqual(contentReviewKey(editorial,next),key);
});
test('applying accepted content keeps current design and photo framing',()=>{
  const spec=composition(),before=structuredClone(spec);
  const proposal={...spec,lens_theme:'light',accent:'#ffffff',photos:[{...spec.photos[0],headline:'Suggested headline',caption:'Suggested caption',zoom:1,offset_y:0}],duration:15};
  const result={proposal};
  const changes=applyContentProposal(spec,result),next={...spec,...changes};
  assert.equal(next.lens_theme,'dark');assert.equal(next.accent,spec.accent);
  assert.equal(next.photos[0].zoom,1.3);assert.equal(next.photos[0].offset_y,.2);
  assert.equal(next.photos[0].headline,'Suggested headline');assert.equal(next.duration,15);
  assert.deepEqual(spec,before);
});


test('giving scenes more time keeps wording and never shortens a selected video',()=>{
  const spec={...composition(),duration:6,auto_timing:true,slide_seconds:3};
  const fit={scenes:[{recommended_seconds:5},{recommended_seconds:5}]};
  const allowed=[5,10,15,20,25,30,45,60];
  assert.deepEqual(moreReadingTime(spec,fit,allowed),{duration:10,auto_timing:false,slide_seconds:null});
  const next={...spec,...moreReadingTime({...spec,duration:30},fit,allowed)};
  assert.equal(next.duration,30);assert.equal(next.photos[0].headline,spec.photos[0].headline);
  assert.equal(moreReadingTime(spec,{scenes:[{recommended_seconds:31},{recommended_seconds:31}]},allowed),null);
});
