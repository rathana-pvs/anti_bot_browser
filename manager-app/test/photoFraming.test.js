import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../src/services/photoFraming.ts',import.meta.url),'utf8'),{
  compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext},
}).outputText;
const {photoBounds,framingSettings,resetFraming,clamp,dragCrop,fullCrop,cropSourceStyle}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const spec={treatment:'fit',headline:'Framing test',show_subtitle:true,supporting:'',category:''};
const photo={image_id:'original',focal_x:.5,focal_y:.5,movement:'auto'};
const close=(actual,expected)=>{
  assert.equal(actual.treatment,expected.treatment);
  for(const key of ['x','y','width','height'])assert.ok(Math.abs(actual[key]-expected[key])<1e-8,`${key}: ${actual[key]}`);
};

test('saved photo defaults match canonical renderer fit and fill geometry',()=>{
  close(photoBounds(spec,photo,600,800),{x:172.5,y:230,width:735,height:980,treatment:'fit'});
  close(photoBounds({...spec,treatment:'fill'},photo,600,800),{x:-180,y:0,width:1440,height:1920,treatment:'fill'});
});
test('photo-specific fill, zoom and drag use the same canonical coordinates',()=>{
  close(photoBounds(spec,{...photo,framing:'fill',zoom:1.5,offset_x:.1,offset_y:-.1},600,800),
    {x:-432,y:-672,width:2160,height:2880,treatment:'fill'});
  assert.equal(clamp(4,.25,3),3);
});
test('apply-to-all copies framing without replacing media or movement',()=>{
  const settings=framingSettings({...photo,framing:'fit',zoom:1.25,offset_x:.1});
  assert.equal(settings.zoom,1.25);assert.equal(settings.offset_y,0);
  assert.equal('image_id' in settings,false);assert.equal('movement' in settings,false);
  assert.deepEqual(resetFraming,{zoom:1,offset_x:0,offset_y:0,focal_x:.5,focal_y:.5});
});

test('crop handles stay within the original image and retain a usable area',()=>{
  assert.deepEqual(dragCrop(fullCrop,'s',0,-.3),{left:0,top:0,right:1,bottom:.7});
  assert.equal(dragCrop(fullCrop,'n',0,2).top,.95);
  assert.equal(dragCrop(fullCrop,'w',-2,0).left,0);
  const moved=dragCrop({left:.1,top:.2,right:.8,bottom:.7},'move',2,-2);
  for(const [key,expected] of Object.entries({left:.3,top:0,right:1,bottom:.5}))assert.ok(Math.abs(moved[key]-expected)<1e-8);
});
test('framing uses cropped dimensions while apply-to-all keeps each photo crop',()=>{
  const cropped={...photo,crop:{left:0,top:0,right:1,bottom:.5}};
  close(photoBounds(spec,cropped,600,800),{x:100,y:426.6666666666667,width:880,height:586.6666666666666,treatment:'fit'});
  assert.equal('crop' in framingSettings(cropped),false);
  assert.deepEqual(cropSourceStyle(cropped.crop),{left:'0%',top:'0%',width:'100%',height:'200%'});
});

test('portrait and square framing retain source proportions and use full canvas height',()=>{
  for(const [aspect_ratio,height] of [['4:5',1350],['1:1',1080]]){
    const value={...spec,aspect_ratio,resolution:720};
    const bounds=photoBounds(value,photo,600,800);
    assert.ok(Math.abs(bounds.width/bounds.height-.75)<1e-9);
    assert.ok(bounds.y+bounds.height<=Math.round(1210*height/1920)+1);
    const fill=photoBounds({...value,treatment:'fill'},photo,1080,height);
    assert.equal(fill.width,1080);assert.equal(fill.height,height);
  }
});

test('switching ratios restores independent photo framing through serialization',async()=>{
  const {switchReelRatio,reelDimensions}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  let value={...spec,aspect_ratio:'9:16',version:2,photos:[{...photo,zoom:1.7,offset_x:.2}],image_id:'original'};
  value={...value,...switchReelRatio(value,'1:1')};
  assert.equal(value.photos[0].zoom,1);
  value.photos[0].zoom=.8;value.photos[0].offset_y=.15;
  value=JSON.parse(JSON.stringify(value));
  value={...value,...switchReelRatio(value,'9:16')};
  assert.equal(value.photos[0].zoom,1.7);assert.equal(value.photos[0].offset_x,.2);
  value={...value,...switchReelRatio(value,'1:1')};
  assert.equal(value.photos[0].zoom,.8);assert.equal(value.photos[0].offset_y,.15);
  assert.deepEqual(reelDimensions({aspect_ratio:'4:5',resolution:720}),[720,900]);
  assert.deepEqual(reelDimensions({resolution:1080},true),[360,640]);
});


test('Lens Report ratio changes keep version 3, theme and independent framing',async()=>{
  const {switchReelRatio,reelDimensions}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  let value={...spec,template:'lens_report',layout_revision:2,version:3,aspect_ratio:'9:16',lens_theme:'dark',show_progress:true,
    photos:[{...photo,headline:'Saved title',caption:'Saved caption',zoom:1.4}],image_id:'original'};
  const original=structuredClone(value);
  value={...value,...switchReelRatio(value,'4:5')};
  assert.equal(value.version,3);assert.equal(value.aspect_ratio,'4:5');
  assert.equal(value.lens_theme,'dark');assert.equal(value.show_progress,true);
  assert.equal(value.photos[0].caption,'Saved caption');
  assert.deepEqual(reelDimensions({...value,resolution:1080}),[1080,1350]);
  value=JSON.parse(JSON.stringify(value));
  value={...value,...switchReelRatio(value,'9:16')};
  assert.equal(value.version,3);assert.equal(value.photos[0].zoom,1.4);
  assert.deepEqual(original.photos[0].framing_by_ratio,undefined);
});

test('flexible Remotion framing uses the full canvas and adjustable boundary',async()=>{
  const {photoArea}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  for(const template of ['lens_report','daily_brief']){
    const value={...spec,template,layout_revision:template==='lens_report'?2:1,aspect_ratio:'4:5',photo_layout:'full_bleed'};
    assert.deepEqual(photoArea(value),{x:0,y:0,width:1080,height:1350});
    close(photoBounds(value,{...photo,zoom:1.2,offset_x:.05,offset_y:.02},600,900),{x:54,y:-108,width:1080,height:1620,treatment:'fit'});
    const framed={...value,photo_layout:'adjustable',photo_panel_end:.5,brand:'NEWS'};
    assert.deepEqual(photoArea(framed),{x:0,y:128,width:1080,height:547});
  }
});
