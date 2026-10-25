import type { ReelComposition } from '../../types/reelCreator';
import { reelPhotos, storyUpdate } from '../../services/storyComposition';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';
import { Checkbox } from '../ui/Checkbox';

export function ReelStyleEditor({ value, onChange }: {
  value: ReelComposition;
  onChange: (changes: Partial<ReelComposition>) => void;
}) {
  const photos = reelPhotos(value);
  if (value.template === 'lens_report' || value.template === 'daily_brief') return <section aria-label="Photo layout and motion" className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
    <h2 className="text-sm font-semibold text-zinc-100">Image layout & motion</h2>
    {value.template==='lens_report' && (value.layout_revision ?? 1) === 1 && <div className="space-y-2"><p className="text-xs text-zinc-400">This saved layout uses the original text fade.</p><Button variant="outline" onClick={()=>onChange({layout_revision:2,text_animation:'auto',motion:true,accent:'#e02f2f'})}>Enable animated Lens Report</Button></div>}
    {((value.layout_revision ?? 1) === 2 || value.photo_layout && value.photo_layout!=='template') && <>
      <label className="block space-y-2 text-xs text-zinc-400">Color theme<Select ariaLabel="News color theme" value={value.lens_theme ?? 'light'} options={[
        {value:'light',label:'Light · cream & ink'},{value:'dark',label:'Dark · black & cream'},
      ]} onValueChange={theme=>onChange({lens_theme:theme as ReelComposition['lens_theme']})}/></label>
      <label className="flex items-center gap-2 text-xs text-zinc-300"><Checkbox ariaLabel="News progress line" checked={value.show_progress ?? true} onCheckedChange={show_progress=>onChange({show_progress})}/>Show playback progress line</label>
      {(value.show_progress ?? true) && <label className="block space-y-2 text-xs text-zinc-400">Progress line position<Select ariaLabel="Progress line position" value={value.progress_position ?? 'top'} options={[
        {value:'top',label:'Top edge · story style (recommended)'},
        {value:'bottom',label:'Bottom edge · classic'},
      ]} onValueChange={progress_position=>onChange({progress_position:progress_position as ReelComposition['progress_position']})}/></label>}
    </>}
    <label className="block space-y-2 text-xs text-zinc-400">Image layout<Select ariaLabel="Image layout" value={value.photo_layout ?? 'template'} options={[
      {value:'full_bleed',label:'Full frame · photo behind text'},{value:'adjustable',label:'Adjustable photo area'},{value:'template',label:'Original fixed template'},
    ]} onValueChange={layout=>onChange({photo_layout:layout as ReelComposition['photo_layout'],...(value.template==='lens_report'&&layout!=='template'?{layout_revision:2 as const}:{}),motion:true})}/></label>
    {(value.photo_layout ?? 'template')==='template' ? <Button variant="outline" onClick={()=>onChange({photo_layout:'full_bleed',...(value.template==='lens_report'?{layout_revision:2 as const}:{}),motion_intensity:'balanced',text_animation:'auto',motion:true})}>Enable flexible scene & motion</Button> : <>
      <label className="block space-y-2 text-xs text-zinc-400">{value.photo_layout==='full_bleed'?'Text starts':'Photo area ends'} · {Math.round((value.photo_panel_end ?? (value.aspect_ratio==='4:5'?740/1350:1180/1920))*100)}%<input aria-label="Photo area and text position" type="range" className="w-full accent-blue-500" min="40" max="72" step="1" value={Math.round((value.photo_panel_end ?? (value.aspect_ratio==='4:5'?740/1350:1180/1920))*100)} onChange={event=>onChange({photo_panel_end:Number(event.target.value)/100})}/></label>
      <p className="text-[11px] text-zinc-500">Move the boundary to give the photo or text more room. Lower values leave more space for headlines and captions.</p>
      <label className="block space-y-2 text-xs text-zinc-400">Motion strength<Select ariaLabel="Photo motion strength" value={value.motion_intensity ?? 'balanced'} options={[
        {value:'subtle',label:'Subtle'},{value:'balanced',label:'Balanced · visible movement'},{value:'dramatic',label:'Strong · larger movement'},
      ]} onValueChange={motion_intensity=>onChange({motion_intensity:motion_intensity as ReelComposition['motion_intensity'],motion:true})}/></label>
    </>}
    {!value.motion && <Button variant="outline" onClick={()=>onChange({motion:true})}>Enable animation</Button>}
    {((value.layout_revision ?? 1) === 2 || value.photo_layout && value.photo_layout!=='template') && <label className="block space-y-2 text-xs text-zinc-400">Text movement<Select ariaLabel="News text movement" value={value.text_animation} options={[
      {value:'auto',label:'Editorial reveal · staggered lines'},{value:'slide',label:'Slide up · staggered lines'},
      {value:'slide_left',label:'From left'},{value:'slide_right',label:'From right'},{value:'fade',label:'Soft staggered fade'},{value:'none',label:'None'},
    ]} onValueChange={mode=>onChange({text_animation:mode as ReelComposition['text_animation'],motion:true})}/></label>}
    <label className="block space-y-2 text-xs text-zinc-400">Photo movement<Select ariaLabel="News photo movement" value={value.slideshow_effect} options={[
      {value:'auto',label:'Gentle zoom'},{value:'zoom_in',label:'Zoom in'},{value:'zoom_out',label:'Zoom out'},
      {value:'pan_left',label:'Pan left'},{value:'pan_right',label:'Pan right'},{value:'still',label:'Still photos'},
    ]} onValueChange={effect=>onChange({...storyUpdate(value,photos.map(photo=>({...photo,movement:'auto'}))),slideshow_effect:effect as ReelComposition['slideshow_effect']})}/></label>
    <p className="text-xs text-zinc-500">Flexible scenes animate the selected zoom or pan from your saved crop, size and position. Fit keeps the whole photo at its starting position; animation may move its edges. Choose Still photos or disable Animate for fixed framing. Original templates retain their previous motion behavior.</p>
  </section>;
  return <section aria-label="Slideshow and text style" className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
    <h2 className="text-sm font-semibold text-zinc-100">Motion & text style</h2>
    <label className="block space-y-2 text-xs text-zinc-400">News layout<Select
      ariaLabel="News layout" value={value.news_design ?? 'classic'}
      options={[{ value: 'clean', label: 'Clean' }, { value: 'classic', label: 'Classic' }]}
      onValueChange={news_design => onChange({ news_design: news_design as ReelComposition['news_design'] })}
    /></label>
    {value.version===1?<div className="space-y-2"><p className="text-xs text-zinc-400">This draft uses the original motion.</p><Button variant="outline" onClick={()=>onChange({...storyUpdate(value),motion:true})}>Enable story motion</Button></div>:<div className="space-y-2"><label className="block space-y-2 text-xs font-medium text-zinc-300">Slideshow style<Select ariaLabel="Slideshow style" value={value.slideshow_effect} options={[
      {value:'auto',label:'Follow motion preset'},{value:'zoom_in',label:'Zoom in'},{value:'zoom_out',label:'Zoom out'},
      {value:'fade',label:'Fade between photos'},{value:'pan_left',label:'Pan left'},{value:'pan_right',label:'Pan right'},{value:'still',label:'Still photos'},
    ]} onValueChange={effect=>onChange({...storyUpdate(value,photos.map(photo=>({...photo,movement:'auto'}))),slideshow_effect:effect as ReelComposition['slideshow_effect'],motion:true,...(effect==='fade'?{transition:'fade' as const}:{})})}/></label></div>}

      <label className="block space-y-2 text-xs text-zinc-400">Motion preset<Select ariaLabel="Motion preset" value={value.motion_style} options={[
        {value:'bulletin',label:'Breaking Bulletin · punchy'},
        {value:'politics',label:'Politics Briefing · calm'},
        {value:'story',label:'Photo Story · sweeping'},
      ]} onValueChange={style=>onChange({
        ...storyUpdate(value, photos.map(photo => ({ ...photo, movement: 'auto' }))),
        motion_style: style as ReelComposition['motion_style'], motion: true,
        slideshow_effect: 'auto', text_animation: 'auto', transition: 'fade',
      })}/></label>
      <p className="text-[11px] text-zinc-500">{{
        bulletin: 'Quick push-in, sharp text slides and short crossfades.',
        politics: 'Gentle pull-back, slow text fades and soft crossfades.',
        story: 'Wide alternating pans, soft headline fade and highlights on photo changes.',
      }[value.motion_style]}</p>
      <label className="block space-y-2 text-xs text-zinc-400">Text entrance<Select ariaLabel="Text entrance animation" value={value.text_animation} options={[
        {value:'auto',label:'Follow motion preset'},
        {value:'fade',label:'Fade in'},
        {value:'slide',label:'Slide up'}, {value:'slide_down',label:'Slide down'},
        {value:'slide_left',label:'From left'}, {value:'slide_right',label:'From right'},
        {value:'none',label:'None'},
      ]} onValueChange={text_animation=>onChange({...storyUpdate(value),text_animation:text_animation as ReelComposition['text_animation']})}/></label>
      <label className="flex items-center gap-2 text-xs text-zinc-300"><Checkbox ariaLabel="News progress line" checked={value.show_progress ?? true} onCheckedChange={show_progress=>onChange({show_progress})}/>Show playback progress line</label>
      {(value.show_progress ?? true) && <label className="block space-y-2 text-xs text-zinc-400">Progress line position<Select ariaLabel="Progress line position" value={value.progress_position ?? 'top'} options={[
        {value:'top',label:'Top edge · story style (recommended)'},
        {value:'bottom',label:'Bottom edge · classic'},
      ]} onValueChange={progress_position=>onChange({progress_position:progress_position as ReelComposition['progress_position']})}/></label>}
  </section>;
}
