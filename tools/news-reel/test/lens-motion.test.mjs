import test from 'node:test';
import assert from 'node:assert/strict';
import {lensTextState} from '../src/lens-motion.mjs';
const state=(seconds,extra={})=>lensTextState({frame:seconds*30,fps:30,kind:'title',index:0,titleCount:2,...extra});
test('headlines visibly rise before captions and settle exactly',()=>{
  assert.equal(state(0).opacity,0);
  assert.ok(state(.35).opacity>0);
  assert.ok(state(.35).y>10);
  assert.equal(state(.35,{kind:'body'}).opacity,0);
  assert.deepEqual(state(2),{opacity:1,x:0,y:0});
  assert.ok(state(.35,{index:1}).opacity<state(.35).opacity);
});
test('timing follows seconds and distances follow output size',()=>{
  assert.deepEqual(state(.35),state(.35,{frame:.35*60,fps:60}));
  assert.equal(state(.35,{width:360}).y,state(.35).y/3);
  assert.equal(state(.35,{mode:'fade'}).y,0);
  assert.ok(state(.35,{mode:'slide_left'}).x<0);
  for(const extra of [{enabled:false},{mode:'none'}])assert.deepEqual(state(0,extra),{opacity:1,x:0,y:0});
});
