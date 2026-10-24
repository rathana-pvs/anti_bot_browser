import { useEffect, useRef, useState } from 'react';
import { Mic, Play, WandSparkles } from 'lucide-react';
import type { EditorialDraft, ReelComposition } from '../../types/reelCreator';
import { configuredGemini } from '../../services/newsSource';
import { reelApi } from '../../services/reelCreator';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Select } from '../ui/Select';

export function NewsNarration({value, editorial, onChange}: {
  value: ReelComposition; editorial: EditorialDraft; onChange: (changes: Partial<ReelComposition>) => void;
}) {
  const text = value.narration_text || '';
  const voice = value.narration_voice || 'en-US-GuyNeural';
  const rate = value.narration_rate || '+0%';
  const [busy, setBusy] = useState<'script'|'voice'|null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [evidence, setEvidence] = useState<{script:string;source:string;quotes:string[]}|null>(null);
  const quotes = evidence?.script === text && evidence?.source === editorial.text ? evidence.quotes : [];
  const [audioUrl, setAudioUrl] = useState('');
  const [seconds, setSeconds] = useState<number|null>(null);
  const controller = useRef<AbortController|null>(null);
  const mounted = useRef(true);
  const context = JSON.stringify([text, voice, rate, editorial.text, editorial.source_url, value.duration, value.narration_enabled]);
  const current = useRef(context); current.current = context;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
  useEffect(() => {
    setAudioUrl(''); setSeconds(null);
    if (!value.narration_enabled || !value.narration_id) return;
    const abort = new AbortController(); let url = '';
    void Promise.all([reelApi.narrationAudio(value.narration_id, abort.signal), reelApi.asset(value.narration_id)]).then(([blob, asset]) => {
      if (abort.signal.aborted) return;
      url = URL.createObjectURL(blob); setAudioUrl(url); setSeconds(asset.duration ?? null);
      if (asset.duration && asset.duration !== value.narration_duration) onChange({narration_duration: asset.duration});
    }).catch(() => { if (!abort.signal.aborted) setError('Could not load saved narration. Preview voice again.'); });
    return () => { abort.abort(); if (url) URL.revokeObjectURL(url); };
  }, [value.narration_enabled, value.narration_id]);
  const prepare = async (kind: 'script'|'voice') => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const expected = current.current; setBusy(kind); setError(''); setNotice('');
    try {
      if (kind === 'script') {
        const settings = configuredGemini();
        const result = await reelApi.narrationScript({...settings, text:editorial.text, source_url:editorial.source_url,
          source_title:editorial.source_title, publisher:editorial.publisher, language:'English', duration:value.duration}, abort.signal);
        if (!mounted.current || abort.signal.aborted) return;
        if (current.current !== expected) { setNotice('Your source or settings changed. Generate the script again.'); return; }
        onChange({narration_text:result.script, narration_id:null});
        // Evidence is shown independently; it is never included in synthesized speech.
        setEvidence({script:result.script, source:editorial.text, quotes:result.source_quotes});
        setNotice('Script ready. Review, then preview voice.');
      } else {
        const asset = await reelApi.narrationVoice({text, voice, rate}, abort.signal);
        if (!mounted.current || abort.signal.aborted) return;
        if (current.current !== expected) { setNotice('Your script or settings changed. Preview voice again.'); return; }
        onChange({narration_id:asset.id,narration_duration:asset.duration ?? null});
        setNotice('Voice saved.');
      }
    } catch (err) { if (mounted.current && !abort.signal.aborted) setError(err instanceof Error ? err.message : 'Could not prepare narration.'); }
    finally { if (mounted.current && !abort.signal.aborted) setBusy(null); }
  };
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const budget = Math.max(8, Math.floor((value.duration - 1) * 2.2));
  return <section aria-label="English narration" className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5">
    <div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100"><Mic className="h-4 w-4 text-blue-400"/>Narration</h2><label className="flex items-center gap-2 text-xs text-zinc-300"><Checkbox ariaLabel="Enable English narration" checked={!!value.narration_enabled} disabled={!!busy} onCheckedChange={enabled=>onChange({narration_enabled:enabled})}/>English voice</label></div>
    {value.narration_enabled && <>
      <Button variant="outline" size="sm" disabled={!!busy || editorial.text.trim().length<80} loading={busy==='script'} onClick={()=>void prepare('script')}><WandSparkles className="h-3.5 w-3.5"/>Generate script</Button>
      {editorial.text.trim().length<80 && <p className="text-[11px] text-zinc-500">Add source text above to generate a script.</p>}
      <label className="block space-y-2 text-xs text-zinc-400">Narration script<textarea aria-label="Narration script" className="min-h-28 w-full resize-y rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-sm leading-relaxed text-zinc-100 outline-none focus:border-blue-500" value={text} disabled={!!busy} maxLength={1500} placeholder="The exact words the voice will read…" onChange={event=>{setEvidence(null);setNotice('');onChange({narration_text:event.target.value,narration_id:null});}}/></label>
      <p className={`text-[11px] ${words>budget?'text-amber-300':'text-zinc-500'}`}>{words} / ~{budget} words · {value.duration}s video</p>
      <details><summary className="cursor-pointer text-xs text-zinc-500">Voice settings</summary><div className="mt-3 grid grid-cols-2 gap-3"><label className="space-y-2 text-xs text-zinc-400">Voice<Select ariaLabel="English narrator voice" value={voice} disabled={!!busy} options={[{value:'en-US-GuyNeural',label:'US English · male'},{value:'en-US-JennyNeural',label:'US English · female'}]} onValueChange={next=>onChange({narration_voice:next as typeof voice,narration_id:null})}/></label><label className="space-y-2 text-xs text-zinc-400">Speed<Select ariaLabel="Narration speed" value={rate} disabled={!!busy} options={[{value:'-10%',label:'Slower'},{value:'+0%',label:'Normal'},{value:'+10%',label:'Faster'}]} onValueChange={next=>onChange({narration_rate:next as typeof rate,narration_id:null})}/></label></div></details>
      <Button variant="outline" disabled={!!busy || text.trim().length<10} loading={busy==='voice'} onClick={()=>void prepare('voice')}><Play className="h-3.5 w-3.5"/>Preview voice</Button>
      <details className="text-[10px] text-zinc-500"><summary className="cursor-pointer">Data use</summary><p className="mt-1">Script generation uses Gemini. Voice preview sends the script to Microsoft’s voice service.</p></details>
      {audioUrl && <audio aria-label="Narration voice preview" controls src={audioUrl} className="h-9 w-full"/>}
      {seconds!==null && <p role="status" className={`text-[11px] ${seconds>value.duration-.25?'text-amber-300':'text-emerald-300'}`}>{seconds.toFixed(1)} seconds of speech. {seconds>value.duration-.25?'Shorten the script or increase video length before rendering.':'Ready for rendering.'}</p>}
      {!value.narration_id && text && <p className="text-[11px] text-amber-300">Preview voice to prepare this script before rendering.</p>}
      {quotes.length>0 && <details><summary className="cursor-pointer text-xs text-zinc-500">Script source passages</summary><div className="mt-2 space-y-2">{quotes.map((quote,i)=><p key={i} className="border-l border-zinc-700 pl-3 text-[11px] text-zinc-400">{quote}</p>)}</div></details>}
    </>}
    {error && <p role="alert" className="text-xs text-amber-200">{error}</p>}{notice && <p role="status" className="text-[11px] text-blue-200">{notice}</p>}
  </section>;
}
