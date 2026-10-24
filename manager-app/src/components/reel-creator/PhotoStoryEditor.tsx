import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, ImagePlus, Loader2, X, Move } from 'lucide-react';
import { reelApi } from '../../services/reelCreator';
import { isDesktopApp } from '../../services/desktopEnvironment';
import { reelPhotos, storyUpdate, photoSeconds } from '../../services/storyComposition';
import type { ReelComposition, ReelPhoto } from '../../types/reelCreator';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';
import { Checkbox } from '../ui/Checkbox';
import { PhotoFramingEditor } from './PhotoFramingEditor';
import { framingSettings } from '../../services/photoFraming';
import { useFileDrop } from '../../hooks/useFileDrop';

export function PhotoStoryEditor({ value, names, selected, onSelect, onChange, onImport, uploading, rendering, framingEnabled }: {
  value: ReelComposition; names: Record<string,string>; selected: number; onSelect: (index: number) => void;
  onChange: (value: Partial<ReelComposition>) => void;
  onImport: (files?: File[], replace?: number) => void;
  uploading: boolean; rendering: boolean; framingEnabled: boolean;
}) {
  const photos = reelPhotos(value), ids = photos.map(p => p.image_id).join(',');
  const [framingOpen,setFramingOpen] = useState(false);
  const { dragging, handlers } = useFileDrop(files => onImport(files), uploading || photos.length >= 5);
  const [urls,setUrls] = useState<Record<string,string>>({});
  useEffect(() => {
    let alive=true;const created: string[]=[];
    setUrls({});
    for(const id of ids.split(',').filter(Boolean)) void reelApi.thumbnail(id).then(blob=>{
      if(!alive)return;const url=URL.createObjectURL(blob);created.push(url);setUrls(old=>({...old,[id]:url}));
    }).catch(()=>{});
    return ()=>{alive=false;created.forEach(url=>URL.revokeObjectURL(url));};
  },[ids,rendering]);
  const current=photos[selected] || photos[0];
  const updatePhoto=(changes: Partial<ReelPhoto>)=>onChange(storyUpdate(value,photos.map((p,i)=>i===selected?{...p,...changes}:p)));
  const reorder=(direction:number)=>{const next=selected+direction;if(next<0||next>=photos.length)return;const ordered=[...photos];[ordered[selected],ordered[next]]=[ordered[next],ordered[selected]];onChange(storyUpdate(value,ordered));onSelect(next);};
  const picker=(replace?:number)=>isDesktopApp()
    ? <Button variant="outline" className="w-full justify-center" disabled={uploading || (replace===undefined&&photos.length>=5)} onClick={()=>onImport(undefined,replace)}><ImagePlus className="h-4 w-4" />{replace!==undefined?'Replace selected photo':photos.length?'Add photos':'Add 1–5 photos'}</Button>
    : <label className={`flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-zinc-700 px-3 py-3 text-xs text-zinc-300 ${uploading || (replace===undefined&&photos.length>=5)?'pointer-events-none opacity-50':''}`}><ImagePlus className="h-4 w-4" />{replace!==undefined?'Replace selected photo':photos.length?'Add photos':'Add 1–5 photos'}<input type="file" className="sr-only" accept="image/jpeg,image/png,image/webp,image/avif,image/gif,image/bmp,image/tiff,.jpg,.jpeg,.jpe,.jfif,.png,.webp,.avif,.gif,.bmp,.tif,.tiff" multiple={replace===undefined} aria-label={replace!==undefined?'Replace selected photo':'Add news photos'} disabled={uploading || (replace===undefined&&photos.length>=5)} onChange={event=>{const files=Array.from(event.currentTarget.files || []);event.currentTarget.value='';if(files.length)onImport(files,replace);}}/></label>;
  return <section {...handlers} className={`space-y-4 rounded-2xl border p-5 transition-colors ${dragging ? 'border-blue-400 bg-blue-500/10 ring-1 ring-blue-400/30' : 'border-zinc-800 bg-zinc-900/40'}`} aria-label="Story photos">
    <div className="flex items-center justify-between gap-3"><h2 className="text-sm font-semibold text-zinc-100">Your photos</h2><span className="text-xs text-zinc-500">{photos.length} / 5</span></div>
    {photos.length>0&&<div className="flex gap-2 overflow-x-auto p-1">{photos.map((photo,index)=><button key={`${index}-${photo.image_id}`} type="button" aria-label={`Select photo ${index+1}`} aria-pressed={selected===index} onClick={()=>onSelect(index)} className={`relative h-14 w-20 shrink-0 overflow-hidden rounded-lg border bg-zinc-950 ${selected===index?'border-blue-400 ring-1 ring-blue-500/30':'border-zinc-800'}`}>
      {urls[photo.image_id]&&<img className="h-full w-full object-cover" src={urls[photo.image_id]} alt={names[photo.image_id]||`Photo ${index+1}`}/>}<span className="absolute bottom-1 left-1 rounded bg-black/80 px-1.5 py-0.5 text-[11px] text-white">{index+1}</span></button>)}</div>}
    <p className={`text-[10px] ${dragging ? 'text-blue-300' : 'text-zinc-500'}`}>{dragging ? 'Drop photos to add to your slideshow' : 'Drop photos here or use Add photos · JPG, PNG, WebP, AVIF, GIF, BMP or TIFF'}</p>
    {uploading?<p role="status" className="flex items-center gap-2 text-xs text-blue-300"><Loader2 className="h-4 w-4 animate-spin"/>Choosing / importing photos…</p>:picker()}
    <p className="text-[11px] leading-relaxed text-zinc-500">{photos.length ? value.auto_timing ? `${photoSeconds(value).join(' + ')} seconds · each photo plays once.` : value.slide_seconds ? `${value.slide_seconds} seconds per photo · ${photos.length === 1 ? 'The same photo repeats' : 'Photos repeat in this order'} until the video ends.` : 'Saved timing · photos fill the video in this order.' : 'Add photos, choose a style, then render. Five seconds per photo by default.'}</p>
    {current&&framingEnabled&&<div className="space-y-3"><Button variant="outline" className="w-full justify-center" aria-expanded={framingOpen} disabled={uploading} onClick={()=>setFramingOpen(open=>!open)}><Move className="h-4 w-4"/>{framingOpen?'Close framing':'Adjust framing'}<span className="text-[10px] text-zinc-500">Photo {selected+1} · drag & zoom</span></Button>
      {framingOpen&&<PhotoFramingEditor key={`${selected}-${current.image_id}`} spec={value} photo={current} url={urls[current.image_id]} onChange={updatePhoto} onApplyAll={photos.length>1?()=>onChange(storyUpdate(value,photos.map(photo=>({...photo,...framingSettings(current)})))):undefined}/>}
    </div>}

    {current && value.template === 'lens_report' && <div className="space-y-3 border-t border-zinc-800 pt-4">
      <label className="block space-y-2 text-xs text-zinc-400">Photo {selected+1} title<textarea aria-label="Lens Report photo title" className="min-h-20 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100" maxLength={240} value={current.headline ?? value.headline} onChange={event=>updatePhoto({headline:event.target.value})}/></label>
      <label className="block space-y-2 text-xs text-zinc-400">Photo {selected+1} caption<textarea aria-label="Lens Report photo caption" className="min-h-20 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100" maxLength={200} value={current.caption ?? value.supporting} onChange={event=>updatePhoto({caption:event.target.value})}/></label>
      <Button variant="ghost" size="sm" onClick={()=>updatePhoto({headline:null,caption:null})}>Use shared title and subtitle</Button>
    </div>}
    {current&&<details className="border-t border-zinc-800 pt-3"><summary className="cursor-pointer text-xs text-zinc-500">Photo {selected+1} details</summary><div className="mt-4 space-y-4">
      <p className="truncate text-xs text-zinc-400">{names[current.image_id]||`Photo ${selected+1}`}</p>
      <div className="flex items-center gap-2"><Button size="sm" variant="outline" aria-label="Move photo earlier" disabled={uploading||selected===0} onClick={()=>reorder(-1)}><ArrowLeft className="h-3.5 w-3.5"/></Button><Button size="sm" variant="outline" aria-label="Move photo later" disabled={uploading||selected===photos.length-1} onClick={()=>reorder(1)}><ArrowRight className="h-3.5 w-3.5"/></Button><Button size="sm" variant="ghost" disabled={uploading} onClick={()=>{onChange(storyUpdate(value,photos.filter((_,i)=>i!==selected)));onSelect(Math.max(0,selected-1));}}><X className="h-3.5 w-3.5"/>Remove</Button></div>
      {picker(selected)}
      <label className="flex items-center gap-2 text-xs text-zinc-300"><Checkbox checked={current.headline != null} ariaLabel="Use custom headline for selected photo" onCheckedChange={enabled=>updatePhoto({headline:enabled ? value.headline : null})}/>Custom headline</label>
      {current.headline != null && <label className="block space-y-2 text-xs text-zinc-400">Photo {selected+1} headline<textarea aria-label="Selected photo headline" className="min-h-20 w-full resize-y rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-xs text-zinc-100 outline-none focus:border-blue-500" value={current.headline} maxLength={240} onChange={event=>updatePhoto({headline:event.target.value})}/></label>}
      {value.auto_timing && <label className="block space-y-2 text-xs text-zinc-400">Photo duration<Select ariaLabel="Selected photo duration" value={String(current.duration_seconds ?? 'auto')} options={[{value:'auto',label:`Auto · ${photoSeconds(value)[selected]} seconds`}, ...[3,4,5,6,7,8,9,10].map(seconds=>({value:String(seconds),label:`${seconds} seconds`}))]} onValueChange={seconds=>updatePhoto({duration_seconds:seconds==='auto'?null:Number(seconds)})}/></label>}

      <label className="block space-y-2 text-xs text-zinc-400">Photo movement<Select ariaLabel="Selected photo movement" value={current.movement} options={[
        {value:'auto',label:'Automatic'},{value:'zoom_in',label:'Zoom in'},{value:'zoom_out',label:'Zoom out'},{value:'pan_left',label:'Pan left'},{value:'pan_right',label:'Pan right'},{value:'still',label:'Still photo'},
      ]} onValueChange={movement=>updatePhoto({movement:movement as ReelPhoto['movement']})}/></label>

    </div></details>}
  </section>;
}
