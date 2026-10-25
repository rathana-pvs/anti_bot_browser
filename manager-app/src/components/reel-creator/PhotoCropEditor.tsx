import { useEffect, useRef, useState } from 'react';
import type { PhotoCrop } from '../../types/reelCreator';
import { reelApi } from '../../services/reelCreator';
import { dragCrop, fullCrop, type CropHandle } from '../../services/photoFraming';
import { Button } from '../ui/Button';

const handles: {id:CropHandle;label:string;left:string;top:string;cursor:string}[] = [
  {id:'nw',label:'top left corner',left:'0%',top:'0%',cursor:'nwse-resize'},
  {id:'n',label:'top edge',left:'50%',top:'0%',cursor:'ns-resize'},
  {id:'ne',label:'top right corner',left:'100%',top:'0%',cursor:'nesw-resize'},
  {id:'e',label:'right edge',left:'100%',top:'50%',cursor:'ew-resize'},
  {id:'se',label:'bottom right corner',left:'100%',top:'100%',cursor:'nwse-resize'},
  {id:'s',label:'bottom edge',left:'50%',top:'100%',cursor:'ns-resize'},
  {id:'sw',label:'bottom left corner',left:'0%',top:'100%',cursor:'nesw-resize'},
  {id:'w',label:'left edge',left:'0%',top:'50%',cursor:'ew-resize'},
];

export function PhotoCropEditor({imageId, size, crop, onChange, onDone}: {
  imageId:string; size:{width:number;height:number}; crop?:PhotoCrop|null;
  onChange:(crop:PhotoCrop|null)=>void; onDone:()=>void;
}) {
  const value = crop || fullCrop;
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const drag = useRef<{pointer:number;x:number;y:number;width:number;height:number;start:PhotoCrop;handle:CropHandle}|null>(null);
  useEffect(() => {
    const controller = new AbortController(); let created = '';
    void reelApi.cropPreview(imageId, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      created = URL.createObjectURL(blob); setUrl(created);
    }).catch(() => { if (!controller.signal.aborted) setError('Could not load the crop preview. Close and reopen Crop photo to retry.'); });
    return () => { controller.abort(); if (created) URL.revokeObjectURL(created); };
  }, [imageId]);
  const keyboardMove = (event: React.KeyboardEvent, handle:CropHandle) => {
    const step = event.shiftKey ? .05 : .01;
    const delta = ({ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]} as Record<string,number[]>)[event.key];
    if (!delta) return;
    event.preventDefault(); event.stopPropagation(); onChange(dragCrop(value,handle,delta[0],delta[1]));
  };
  return <section aria-label="Crop selected photo" className="space-y-3 rounded-lg border border-zinc-700 bg-zinc-900/60 p-3">
    <div className="flex items-center justify-between gap-2"><h3 className="text-xs font-semibold text-zinc-200">Crop photo</h3><Button size="sm" variant="outline" onClick={onDone}>Done cropping</Button></div>
    <p className="text-[11px] leading-relaxed text-zinc-400">Drag an edge or corner to remove unwanted areas. Drag inside to move the crop. The original stays saved.</p>
    {!url ? <p role="status" className="py-5 text-xs text-zinc-400">{error || 'Loading original photo…'}</p> : <div className="p-3">
      <div role="group" tabIndex={0} aria-label="Photo crop canvas. Arrow keys move the crop; Shift moves faster."
        className="relative mx-auto w-full touch-none select-none outline-none focus:ring-2 focus:ring-blue-400"
        style={{maxWidth:Math.min(520, 320*size.width/size.height),aspectRatio:`${size.width}/${size.height}`}}
        onKeyDown={event=>keyboardMove(event,'move')}
        onPointerDown={event=>{
          if (event.button!==0) return;
          const handle=(event.target as HTMLElement).closest<HTMLElement>('[data-crop-handle]')?.dataset.cropHandle as CropHandle|undefined;
          if (!handle) return;
          event.preventDefault(); (event.target as HTMLElement).focus();
          const rect=event.currentTarget.getBoundingClientRect();
          drag.current={pointer:event.pointerId,x:event.clientX,y:event.clientY,width:rect.width,height:rect.height,start:{...value},handle};
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={event=>{const start=drag.current;if(!start || event.pointerId!==start.pointer)return;onChange(dragCrop(start.start,start.handle,(event.clientX-start.x)/start.width,(event.clientY-start.y)/start.height));}}
        onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
        <img alt="Original photo for cropping" src={url} draggable={false} className="pointer-events-none block h-full w-full"/>
        <div className="pointer-events-none absolute inset-0 overflow-hidden"><div className="absolute" style={{left:`${value.left*100}%`,top:`${value.top*100}%`,width:`${(value.right-value.left)*100}%`,height:`${(value.bottom-value.top)*100}%`,boxShadow:'0 0 0 2000px rgb(0 0 0 / 65%)'}}/></div>
        <div data-crop-handle="move" role="group" tabIndex={0} aria-label="Move crop area" onKeyDown={event=>keyboardMove(event,'move')}
          className="absolute cursor-move border-2 border-blue-300 outline-none focus:ring-2 focus:ring-white"
          style={{left:`${value.left*100}%`,top:`${value.top*100}%`,width:`${(value.right-value.left)*100}%`,height:`${(value.bottom-value.top)*100}%`}}>
          <div className="pointer-events-none grid h-full grid-cols-3 grid-rows-3 opacity-50">{Array.from({length:9},(_,i)=><span key={i} className="border border-white/30"/>)}</div>
          {handles.map(handle=><button key={handle.id} type="button" data-crop-handle={handle.id} aria-label={`Crop ${handle.label}`} onKeyDown={event=>keyboardMove(event,handle.id)} className="absolute z-10 flex h-5 w-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded outline-none focus:ring-2 focus:ring-white" style={{left:handle.left,top:handle.top,cursor:handle.cursor}}><span className="pointer-events-none h-3 w-3 rounded-sm border border-blue-500 bg-white shadow"/></button>)}
        </div>
      </div>
    </div>}
    <div className="flex items-center justify-between gap-3"><p role="status" className="text-[11px] text-zinc-400">Keep {Math.round((value.right-value.left)*100)}% width × {Math.round((value.bottom-value.top)*100)}% height · preview updates live</p><Button size="sm" variant="ghost" onClick={()=>onChange(null)}>Reset crop</Button></div>
    <p className="text-[10px] leading-relaxed text-zinc-500">Keep photo credits visible, or carry them into the Source line.</p>
  </section>;
}
