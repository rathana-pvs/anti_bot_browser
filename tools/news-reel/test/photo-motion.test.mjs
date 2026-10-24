import test from 'node:test';
import assert from 'node:assert/strict';
import {photoMotionState} from '../src/photo-motion.mjs';
const state=(frame,extra={})=>photoMotionState({frame,frames:121,movement:'zoom_in',...extra});
test('native photo camera has visible bounded zoom and pan',()=>{
  assert.equal(state(0).scale,1);assert.equal(state(120).scale,1.12);
  assert.ok(state(120,{intensity:'dramatic'}).scale>state(120,{intensity:'subtle'}).scale);
  assert.ok(state(0,{movement:'pan_left'}).x>0);assert.ok(state(120,{movement:'pan_left'}).x<0);
  assert.deepEqual(state(0,{enabled:false}),{scale:1,x:0});
  assert.deepEqual(state(80,{movement:'still'}),{scale:1,x:0});
  assert.equal(state(120,{movement:'zoom_out'}).scale,1);
});
