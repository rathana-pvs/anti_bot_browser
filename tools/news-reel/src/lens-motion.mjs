const clamp = value => Math.max(0, Math.min(1, value));
/** Deterministic editorial entrances; all offsets use the actual output width. */
export function lensTextState({frame, fps, kind, index, titleCount, mode='auto', enabled=true, width=1080, strength=1}) {
  if (!enabled || mode === 'none') return {opacity:1,x:0,y:0};
  const start = kind === 'title' ? .10+index*.12 : .50+titleCount*.08+index*.10;
  const duration = kind === 'title' ? .75 : .60;
  const progress = clamp((frame/fps-start)/duration);
  const eased = 1-(1-progress)**3;
  const offset=(1-eased)*width/1080*strength;
  return {opacity:eased,x:mode==='slide_left'?-64*offset:mode==='slide_right'?64*offset:0,
    y:mode==='fade'||mode==='slide_left'||mode==='slide_right'?0:(mode==='slide_down'?-1:1)*(kind==='title'?64:36)*offset};
}
