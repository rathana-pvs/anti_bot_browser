const clamp=value=>Math.min(1,Math.max(0,value));
export function photoMotionState({frame,frames,movement='still',intensity='balanced',enabled=true}) {
  if(!enabled || movement==='still')return {scale:1,x:0};
  const raw=clamp(frame/Math.max(1,frames-1));
  const progress=(1-Math.cos(Math.PI*raw))/2;
  const amount={subtle:.04,balanced:.12,dramatic:.20}[intensity] ?? .12;
  if(movement==='zoom_in')return {scale:1+amount*progress,x:0};
  if(movement==='zoom_out')return {scale:1+amount*(1-progress),x:0};
  if(movement==='pan_left' || movement==='pan_right')return {scale:1+amount,x:(movement==='pan_left'?1-2*progress:2*progress-1)*amount*.35};
  return {scale:1,x:0};
}
