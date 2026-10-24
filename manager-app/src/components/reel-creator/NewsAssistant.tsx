import { useEffect, useRef, useState } from 'react';
import { FileText, Link as LinkIcon, Loader2, Sparkles, WandSparkles } from 'lucide-react';
import type { ContentFit, EditorialDraft, ReelComposition, TemplateNewsGeneration } from '../../types/reelCreator';
import { reelApi } from '../../services/reelCreator';
import { configuredGemini, newsSourceFingerprint } from '../../services/newsSource';
import { applyContentProposal, contentReviewKey, contentSettingsKey, moreReadingTime } from '../../services/templateContent';
import { reelDurations } from '../../services/storyComposition';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';

const field = 'w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-xs text-zinc-100 outline-none focus:border-blue-500';
const names: Record<string, string> = {
  bbc: 'BBC News',
  economist: 'The Economist',
  reuters: 'Reuters Wire',
  ap_wire: 'AP News',
  lens_report: 'Lens Report',
  daily_brief: 'Daily Brief',
  newsroom: 'BBC News',
  editorial: 'The Economist',
  brief: 'Reuters Wire',
};
type Intent='generate'|'adapt'|'shorten';
type Pending={result:TemplateNewsGeneration;key:string;intent:Intent;sceneIndex:number|null};
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Could not prepare this story.';

export function NewsAssistant({ value, composition, contentCheck, onOpenSettings, categoryHint, onChange, onApply }: {
  value: EditorialDraft;
  composition: ReelComposition;
  contentCheck: {fit:ContentFit|null;error:string;checking:boolean};
  onOpenSettings:()=>void;
  categoryHint: string;
  onChange: (value: EditorialDraft) => void;
  onApply: (value: Partial<ReelComposition>) => void;
}) {
  const [busy, setBusy] = useState<'reading' | 'generating' | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending,setPending]=useState<Pending|null>(null);
  const [sourceHash, setSourceHash] = useState({ input: '', hash: '' });
  const sourceInput = JSON.stringify([value.text, value.source_url, value.language]);
  const current = useRef(value);current.current = value;
  const compositionRef=useRef(composition);compositionRef.current=composition;
  const key = useRef(contentReviewKey(value, composition));key.current = contentReviewKey(value,composition);
  const mounted = useRef(true);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controller.current?.abort(); };
  }, []);
  useEffect(() => {
    let alive = true;
    void newsSourceFingerprint(value).then(hash => { if (alive) setSourceHash({ input: sourceInput, hash }); });
    return () => { alive = false; };
  }, [sourceInput, value.text, value.source_url, value.language]);

  const update = (next: EditorialDraft) => {
    current.current = next;key.current = contentReviewKey(next,compositionRef.current);onChange(next);
  };
  const patch = (changes: Partial<EditorialDraft>) => update({ ...current.current, ...changes });
  const stale = !!value.generated && (sourceHash.input !== sourceInput || value.generated_source !== sourceHash.hash);
  const settingsChanged=!!value.applied_settings && value.applied_settings!==contentSettingsKey(composition,value.tone);
  const pendingStale=!!pending && pending.key!==key.current;
  const needsReadingTime=contentCheck.fit?.issues.some(issue=>issue.kind==='reading');
  const timingSuggestion=contentCheck.fit?moreReadingTime(composition,contentCheck.fit,reelDurations):null;
  const canGenerate=!(value.mode==='url'?!value.url.trim():value.text.trim().length<80);

  const prepare = async (generate: boolean, intent:Intent='generate', sceneIndex:number|null=null) => {
    controller.current?.abort();
    const abort = new AbortController();controller.current = abort;
    let working = { ...current.current };
    const spec=compositionRef.current;
    let expected = key.current;
    setError('');setNotice('');setPending(null);
    try {
      const settings = generate ? configuredGemini() : null;
      if (working.mode === 'url' && (!working.text || !working.source_url || !generate)) {
        if (!working.url.trim()) throw new Error('Paste a website article link first.');
        setBusy('reading');
        const article = await reelApi.article(working.url, abort.signal);
        if (!mounted.current || abort.signal.aborted || key.current !== expected) return;
        working = { ...working, text: article.text, source_url: article.url,source_title: article.title, publisher: article.publisher };
        update(working);expected=key.current;
      }
      if (!generate) { setNotice('Article loaded. Choose your template and settings, then generate.'); return; }
      if (working.text.trim().length < 80) throw new Error('Add at least 80 characters of source text.');
      if(working.language!=='English'){working={...working,language:'English'};update(working);expected=key.current;}
      setBusy('generating');
      const result = await reelApi.generateTemplateNews({ ...settings!, text: working.text,
        source_url: working.source_url, source_title: working.source_title, publisher: working.publisher,
        language:'English',category_hint:categoryHint,composition:spec,tone:working.tone ?? 'neutral',word_count:working.word_count ?? 'standard',intent,scene_index:sceneIndex }, abort.signal);
      if (!mounted.current || abort.signal.aborted) return;
      if (key.current !== expected) { setNotice('The source, settings, or text changed while Gemini was working. Your edits are preserved; generate again for the current version.'); return; }
      setPending({result,key:expected,intent,sceneIndex});
      setNotice('Suggestions are ready. Review them below before applying.');
    } catch (err) {
      if (mounted.current && !abort.signal.aborted) setError(errorText(err));
    } finally { if (mounted.current && controller.current===abort) setBusy(null); }
  };

  const apply=()=>{
    if(!pending || pending.key!==key.current)return;
    const changes=applyContentProposal(compositionRef.current,pending.result);
    update({...current.current,generated:pending.result.copy,generated_source:pending.result.source_fingerprint,
      generated_caption:pending.intent==='shorten'?current.current.generated_caption:pending.result.post_caption,
      post_caption:pending.intent==='shorten'?current.current.post_caption:pending.result.post_caption,
      applied_settings:contentSettingsKey({...compositionRef.current,...changes},current.current.tone)});
    onApply(changes);setPending(null);setNotice('Reviewed suggestions applied. Your design and photo framing are preserved.');
  };
  const photos=composition.photos.length?composition.photos:[{headline:composition.headline,caption:composition.supporting}];

  return <section className="space-y-4 rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-950/20 to-zinc-900/40 p-5" aria-label="Gemini news assistant">
    <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100"><Sparkles className="h-4 w-4 text-blue-400" />Content for your template</h2>
    <div className="space-y-2 rounded-xl border border-zinc-800 bg-zinc-950/50 p-3">
      <p className="text-xs text-zinc-200">{names[composition.template]} · {composition.aspect_ratio ?? '9:16'} · {photos.length} {photos.length===1?'scene':'scenes'} · {composition.auto_timing?'Auto timing':`${composition.duration} seconds`} · Captions {composition.show_subtitle?'on':'off'}</p>
      <Button size="sm" variant="ghost" onClick={onOpenSettings}>Change template & settings</Button>
      <p className="text-[11px] text-zinc-500">English copy follows your layout and reading time. Add photos before generation to plan one scene per photo.</p>
    </div>
    <div className="flex flex-wrap items-center gap-2">{(['text', 'url'] as const).map(mode => <button key={mode} type="button" aria-pressed={value.mode === mode} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${value.mode === mode ? 'border-blue-500/50 bg-blue-500/10 text-blue-200' : 'border-zinc-800 text-zinc-500'}`} onClick={() => patch({ mode, ...(mode === 'text' ? { source_url: '', publisher: '', source_title: '' } : {}) })}>{mode === 'url' ? <LinkIcon className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}{mode === 'url' ? 'Website link' : 'Source text'}</button>)}</div>
    {value.mode === 'url' && <div className="flex gap-2"><input aria-label="News article URL" type="url" className={field} value={value.url} maxLength={2048} placeholder="https://news-website.com/article" onChange={e => patch({ url: e.target.value, text: '', source_url: '', source_title: '', publisher: '' })} /><Button variant="outline" disabled={!!busy || !value.url.trim()} onClick={() => void prepare(false)}>{busy === 'reading' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}Read article</Button></div>}
    {value.source_title && <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 px-3 py-2"><p className="text-xs font-medium text-zinc-300">{value.source_title}</p><p className="mt-1 text-[10px] text-zinc-500">{value.publisher} · {value.text.length.toLocaleString()} characters extracted</p></div>}
    {value.mode === 'text' ? <label className="block space-y-2 text-xs text-zinc-400">Source text<textarea className={`${field} min-h-28 resize-y leading-relaxed`} value={value.text} maxLength={120000} placeholder="Paste the news article…" onChange={e => patch({ text: e.target.value })} /></label> : value.text && <details className="rounded-lg border border-zinc-800 p-3"><summary className="cursor-pointer text-xs text-zinc-400">Read or edit extracted article text</summary><textarea aria-label="Extracted article text" className={`${field} mt-3 min-h-28 resize-y leading-relaxed`} value={value.text} maxLength={120000} onChange={e => patch({ text: e.target.value })} /></details>}
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="block space-y-2 text-xs text-zinc-400">Writing tone<Select ariaLabel="News writing tone" value={value.tone ?? 'neutral'} options={[
        {value:'neutral',label:'Neutral · factual reporting'},{value:'explainer',label:'Explainer · plain context'},{value:'concise',label:'Concise · short and direct'},
      ]} onValueChange={tone=>patch({tone:tone as EditorialDraft['tone']})}/></label>
      <label className="block space-y-2 text-xs text-zinc-400">Target word count<Select ariaLabel="Target word count" value={value.word_count ?? 'standard'} options={[
        {value:'short',label:'Short · 4–8 words (punchy hook)'},
        {value:'standard',label:'Standard · 8–14 words (balanced)'},
        {value:'detailed',label:'Detailed · 14–20 words (in-depth)'},
      ]} onValueChange={word_count=>patch({word_count:word_count as EditorialDraft['word_count']})}/></label>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <Button disabled={!!busy || !canGenerate} onClick={() => void prepare(true)}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <WandSparkles className="h-4 w-4" />}{busy === 'reading' ? 'Reading article…' : busy === 'generating' ? 'Preparing suggestions…' : 'Generate for this template'}</Button>
      <Button variant="outline" disabled={!!busy || !canGenerate} onClick={()=>void prepare(true,'adapt')}>Adapt content to new settings</Button>
    </div>
    <details className="text-[10px] text-zinc-500"><summary className="cursor-pointer">Data use</summary><p className="mt-1">Source text, current copy, and content settings go to your saved Gemini model when you click a generation button. Photos and credentials stay out of the prompt. A request can make one additional attempt to correct text fit. Changing settings runs local checks only.</p></details>
    {contentCheck.checking && <p className="text-xs text-zinc-500">Checking layout and reading time…</p>}
    {contentCheck.error && <p role="alert" className="text-xs text-amber-300">Content check: {contentCheck.error}</p>}
    {contentCheck.fit && <div className="space-y-2 rounded-lg border border-zinc-800 p-3 text-xs">
      <p className={contentCheck.fit.fits?'text-emerald-300':'text-amber-300'}>{contentCheck.fit.fits?'Content fits the current layout and reading time.':'Content needs adjustment for these settings.'}</p>
      {needsReadingTime && <div className="space-y-2"><p className="text-[11px] text-zinc-400">Keep your wording by giving every scene enough time, or shorten the text below.</p>{timingSuggestion ? <Button size="sm" variant="outline" onClick={()=>{onApply(timingSuggestion);setError('');setNotice('Timing changed to Fit video. Your text and post caption are unchanged.');}}>Give scenes more time · {timingSuggestion.duration}s video</Button> : <p className="text-xs text-amber-200">This copy needs more than 60 seconds. Shorten it or split it into separate reels.</p>}</div>}
      {contentCheck.fit.issues.map((issue,index)=><p key={index} className="text-amber-200">{issue.scene===null?'Settings':`Scene ${issue.scene+1}`}: {issue.message}</p>)}
      {photos.map((photo,index)=><div key={index} className="flex items-center justify-between gap-2 border-t border-zinc-800 pt-2"><div className="min-w-0"><p className="truncate text-zinc-400">Scene {index+1}: {(photo.headline ?? composition.headline) || 'Untitled'}</p>{contentCheck.fit?.scenes[index] && <p className="mt-1 text-[10px] text-zinc-500">{contentCheck.fit.scenes[index].seconds.toFixed(1)}s · up to {contentCheck.fit.scenes[index].max_words} visible words</p>}</div><Button size="sm" variant="ghost" disabled={!!busy || !canGenerate} onClick={()=>void prepare(true,'shorten',index)}>Shorten this scene</Button></div>)}
      {composition.template!=='lens_report' && <p className="text-[10px] text-zinc-500">This template shares one subtitle across scenes. Adapt content to shorten that subtitle.</p>}
    </div>}
    {settingsChanged && contentCheck.fit?.fits && <p className="text-xs text-blue-200">Settings or tone changed. Your current text still fits; adapt it if you want to update the wording.</p>}
    {stale && <p className="text-xs text-amber-300">The source or language changed. Generate fresh suggestions before using the old copy.</p>}
    {pending && <div aria-label="Review generated content" className="space-y-4 rounded-xl border border-blue-500/40 bg-blue-950/20 p-4">
      <h3 className="text-sm font-semibold text-zinc-100">Review suggested changes</h3>
      {pending.result.warnings?.map((warning,index)=><p key={index} className="text-[11px] text-zinc-400">{warning}</p>)}
      {pendingStale && <p role="status" className="text-xs text-amber-300">The source, settings, or text has changed. Generate again to protect your edits.</p>}
      {pending.result.copy.scenes?.map((scene,index)=>{
        const target=pending.sceneIndex ?? index;
        const before=photos[target];
        return <div key={index} className="space-y-2 border-t border-zinc-800 pt-3 text-xs">
          <p className="font-semibold text-zinc-300">Scene {target+1}</p>
          <div className="grid gap-3 sm:grid-cols-2"><div><p className="mb-1 text-[10px] text-zinc-500">CURRENT</p><p className="text-zinc-400">{before?.headline ?? composition.headline}</p>{composition.show_subtitle && <p className="mt-2 text-zinc-500">{composition.template==='lens_report'?(before?.caption ?? composition.supporting):composition.supporting}</p>}</div><div><p className="mb-1 text-[10px] text-blue-300">SUGGESTED</p><p className="text-zinc-100">{scene.headline}</p>{composition.show_subtitle && <p className="mt-2 text-zinc-300">{scene.caption}</p>}</div></div>
          <details className="text-[10px] text-zinc-500"><summary className="cursor-pointer">Supporting source passages</summary>{scene.source_quotes.map((quote,i)=><blockquote className="mt-2 border-l border-zinc-700 pl-2" key={i}>{quote}</blockquote>)}</details>
        </div>;
      })}
      {pending.intent!=='shorten' && <div className="space-y-2 text-xs"><p className="text-zinc-400">Suggested category: {pending.result.copy.category} · Source: {pending.result.proposal.source || 'Add attribution'}</p><p className="font-medium text-zinc-300">Suggested post caption</p><p className="whitespace-pre-wrap text-zinc-400">{pending.result.post_caption}</p></div>}
      {pending.intent==='shorten' && <p className="text-[11px] text-zinc-500">Only this scene changes. The post caption and other scenes are preserved.</p>}
      {!composition.show_subtitle && <p className="text-[11px] text-zinc-500">Hidden captions and subtitles are preserved.</p>}
      <p className="text-[11px] text-zinc-500">Review factual accuracy against the source before accepting.</p>
      <div className="flex gap-2"><Button disabled={pendingStale || !!busy} onClick={apply}>Apply suggestions</Button><Button variant="ghost" onClick={()=>{setPending(null);setNotice('Suggestions discarded. Your content is unchanged.');}}>Discard</Button></div>
    </div>}
    {error && <p role="alert" className="text-xs leading-relaxed text-red-300">{error}</p>}
    {notice && <p role="status" className="text-xs leading-relaxed text-blue-200">{notice}</p>}
    <label className="block space-y-2 text-xs text-zinc-400">Post caption<textarea className={`${field} min-h-24 resize-y leading-relaxed`} aria-label="Reel post caption" maxLength={8000} value={value.post_caption} placeholder="Caption for your Reel post…" onChange={e => patch({ post_caption: e.target.value })} /></label>
  </section>;
}
