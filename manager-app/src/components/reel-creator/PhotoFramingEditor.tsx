import { useEffect, useRef, useState } from 'react';
import { Minus, Plus, RotateCcw, Move, Crop } from 'lucide-react';
import type { ReelComposition, ReelPhoto } from '../../types/reelCreator';
import { reelApi } from '../../services/reelCreator';
import { clamp, photoBounds, resetFraming, cropSourceStyle, reelDimensions, photoArea } from '../../services/photoFraming';
import { Button } from '../ui/Button';
import { PhotoCropEditor } from './PhotoCropEditor';

export function PhotoFramingEditor({ spec, photo, url, onChange, onApplyAll }: {
  spec: ReelComposition; photo: ReelPhoto; url?: string;
  onChange: (changes: Partial<ReelPhoto>) => void; onApplyAll?: () => void;
}) {
  const [size, setSize] = useState<{width:number;height:number} | null>(null);
  const [error, setError] = useState('');
  const [autoBusy, setAutoBusy] = useState(false);
  const [autoNotice, setAutoNotice] = useState('');
  const [cropping, setCropping] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const autoFrame = async () => {
    setAutoBusy(true); setAutoNotice('');
    try {
      const result = await reelApi.autoFraming(photo.image_id, photo.crop, spec.aspect_ratio);
      if (!alive.current) return;
      onChange(result.settings);
      setAutoNotice(result.status === 'detected' ? `${result.faces} face${result.faces === 1 ? '' : 's'} detected. Photo motion is off. Review the face placement against the text in the main preview.` : result.status === 'none' ? 'No faces detected. Showing the whole photo; adjust manually if needed.' : 'Face detection is unavailable. Showing the whole photo.');
    } catch (err) { if (alive.current) setAutoNotice(err instanceof Error ? err.message : 'Could not suggest framing.'); }
    finally { if (alive.current) setAutoBusy(false); }
  };
  const drag = useRef<{pointer:number;x:number;y:number;left:number;top:number;width:number;height:number} | null>(null);
  useEffect(() => {
    let alive = true;
    setSize(null); setError('');
    void reelApi.asset(photo.image_id).then(asset => {
      if (alive && asset.width && asset.height) setSize({width:asset.width,height:asset.height});
    }).catch(() => { if (alive) setError('Could not load photo dimensions. Reopen Adjust framing to try again.'); });
    return () => { alive = false; };
  }, [photo.image_id]);
  const [canvasWidth, canvasHeight] = reelDimensions({...spec, resolution:1080});
  const area=photoArea(spec);
  const flexible=['lens_report','daily_brief'].includes(spec.template) && !!spec.photo_layout && spec.photo_layout!=='template';
  const textStart=flexible?(spec.photo_panel_end ?? (spec.aspect_ratio==='4:5'?740/1350:1180/1920)):spec.template==='lens_report'&&spec.layout_revision===2?(spec.aspect_ratio==='4:5'?740/1350:1180/1920):.65;
  const bounds = size ? photoBounds(spec, photo, size.width, size.height) : null;
  const zoom = Math.round((photo.zoom ?? 1) * 100);
  const changeZoom = (percent: number) => onChange({zoom:clamp(percent,25,300)/100});
  return <div className="space-y-3 rounded-xl border border-blue-500/25 bg-zinc-950/60 p-4" aria-label="Photo framing editor">
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="outline" disabled={!size || autoBusy} aria-expanded={cropping} onClick={()=>setCropping(open=>!open)}><Crop className="h-3.5 w-3.5"/>{cropping?'Close crop':'Crop photo'}{photo.crop?' · cropped':''}</Button>
      <Button size="sm" variant="outline" disabled={autoBusy || cropping} onClick={() => void autoFrame()}>{autoBusy ? 'Checking photo…' : 'Auto frame faces'}</Button>
      {(['fit','fill'] as const).map(mode => <button type="button" key={mode} aria-pressed={bounds?.treatment===mode} onClick={()=>onChange({...resetFraming,framing:mode})} className={`rounded-lg border px-3 py-2 text-xs ${bounds?.treatment===mode?'border-blue-500 bg-blue-500/10 text-blue-200':'border-zinc-700 text-zinc-400'}`}>{mode==='fit'?'Fit whole photo':'Fill video'}</button>)}
      <Button size="sm" variant="ghost" onClick={()=>onChange({...resetFraming})}><RotateCcw className="h-3.5 w-3.5"/>Reset</Button>
    </div>
    {cropping && size && <PhotoCropEditor imageId={photo.image_id} size={size} crop={photo.crop} onChange={crop=>{setAutoNotice('');onChange({crop,...resetFraming});}} onDone={()=>setCropping(false)}/>}
    {autoNotice && <p role="status" className="text-[11px] leading-relaxed text-blue-200">{autoNotice}</p>}
    <div className="grid gap-4 sm:grid-cols-[minmax(0,200px)_1fr]">
      <div role="group" tabIndex={0} aria-label="Photo framing canvas. Drag to reposition. Arrow keys move the image; hold Shift for larger steps."
        className="relative w-full max-w-[200px] cursor-grab touch-none select-none overflow-hidden rounded-lg border border-zinc-700 bg-zinc-900 outline-none focus:ring-2 focus:ring-blue-500 active:cursor-grabbing"
        style={{aspectRatio:`${canvasWidth}/${canvasHeight}`}}
        onPointerDown={event=>{if(!bounds || event.button!==0)return;event.preventDefault();event.currentTarget.focus();const rect=event.currentTarget.getBoundingClientRect();drag.current={pointer:event.pointerId,x:event.clientX,y:event.clientY,left:photo.offset_x??0,top:photo.offset_y??0,width:rect.width,height:rect.height};event.currentTarget.setPointerCapture(event.pointerId);}}
        onPointerMove={event=>{const start=drag.current;if(!start||event.pointerId!==start.pointer)return;onChange({offset_x:clamp(start.left+(event.clientX-start.x)/start.width,-1,1),offset_y:clamp(start.top+(event.clientY-start.y)/start.height,-1,1)});}}
        onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}
        onKeyDown={event=>{const step=event.shiftKey ? .05 : .01;const delta=({ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]} as Record<string,number[]>)[event.key];if(!delta)return;event.preventDefault();onChange({offset_x:clamp((photo.offset_x??0)+delta[0],-1,1),offset_y:clamp((photo.offset_y??0)+delta[1],-1,1)});}}>
        {url&&<div className="pointer-events-none absolute inset-0 overflow-hidden opacity-25 blur-lg"><img src={url} alt="" draggable={false} className="absolute max-w-none" style={cropSourceStyle(photo.crop || undefined)}/></div>}
        {url&&bounds&&<div className="pointer-events-none absolute overflow-hidden" style={{left:`${area.x/canvasWidth*100}%`,top:`${area.y/canvasHeight*100}%`,width:`${area.width/canvasWidth*100}%`,height:`${area.height/canvasHeight*100}%`}}><div className="absolute overflow-hidden" style={{left:`${(bounds.x-area.x)/area.width*100}%`,top:`${(bounds.y-area.y)/area.height*100}%`,width:`${bounds.width/area.width*100}%`,height:`${bounds.height/area.height*100}%`}}><img src={url} alt="Selected photo framing" draggable={false} className="absolute max-w-none" style={cropSourceStyle(photo.crop || undefined)}/></div></div>}
        <div className="pointer-events-none absolute inset-0 grid grid-cols-3 grid-rows-3 opacity-20">{Array.from({length:9},(_,i)=><span key={i} className="border border-white/25"/>)}</div>
        {!!(spec.headline || (spec.show_subtitle&&spec.supporting) || spec.category)&&<div style={{height:`${(1-textStart)*100}%`}} className="pointer-events-none absolute inset-x-0 bottom-0 border-t border-dashed border-white/40 bg-black/40 px-2 pt-2 text-[9px] text-white/70">Text area</div>}
        {(!bounds||!url)&&<div className="absolute inset-0 flex items-center justify-center p-3 text-center text-[11px] text-zinc-400">{error||'Loading photo…'}</div>}
      </div>
      <div className="space-y-4 self-center">
        <p className="flex items-center gap-2 text-xs text-zinc-300"><Move className="h-4 w-4 text-blue-400"/>Drag the photo to position it</p>
        <label className="block space-y-2 text-xs text-zinc-400">Photo size · {zoom}%<input aria-label="Photo zoom" type="range" className="w-full accent-blue-500" min="25" max="300" step="5" value={zoom} onChange={event=>changeZoom(Number(event.target.value))}/></label>
        <div className="flex items-center gap-2"><Button size="sm" variant="outline" aria-label="Make photo smaller" disabled={zoom<=25} onClick={()=>changeZoom(zoom-5)}><Minus className="h-3.5 w-3.5"/></Button><span className="min-w-12 text-center text-xs tabular-nums text-zinc-300">{zoom}%</span><Button size="sm" variant="outline" aria-label="Make photo larger" disabled={zoom>=300} onClick={()=>changeZoom(zoom+5)}><Plus className="h-3.5 w-3.5"/></Button></div>
        <p className="text-[11px] leading-relaxed text-zinc-500">Adjusts this photo. The main preview shows your text and final layout. Arrow keys move it precisely.</p>
        {onApplyAll&&<Button size="sm" variant="outline" onClick={onApplyAll}>Apply framing to all photos</Button>}
      </div>
    </div>
  </div>;
}
