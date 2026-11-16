import { useState } from 'react';
import { CircleAlert, CornerDownLeft, Undo2, Wand2 } from 'lucide-react';
import { Button } from '../ui/Button';
import { Textarea } from '../ui/Textarea';
import type { DirectorChange, DirectorResult } from '../../types/reelCreator';

interface Props {
  /** The model the director uses, e.g. "Claude Sonnet 5.5". */
  model: string;
  busy: boolean;
  /** This step is running. */
  directing: boolean;
  result: DirectorResult | null;
  onDirect: (instruction: string) => void;
  onUndo: () => void;
  onScene: (index: number) => void;
}

const SUGGESTIONS = ['Make the hook punchier', 'Shorten every scene', 'Vary the animations', 'Calmer, for a serious story', 'Add a figure scene'];
const FIELD_NAMES: Record<string, string> = { headline: 'Headline', detail: 'Detail', highlight: 'Highlight', kind: 'Type', stat: 'Figure', attribution: 'Speaker',
  motion: 'Camera', transition: 'Transition', fit: 'Photo fit', look: 'Look', scene: 'Scene' };

const fieldName = (field: string) => FIELD_NAMES[field] ?? field.replace(/^./, c => c.toUpperCase());
const shorten = (text: string) => text.length > 38 ? `${text.slice(0, 36)}…` : text;
const tokens = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

function ChangeRow({ change, onScene }: { change: DirectorChange; onScene: (index: number) => void }) {
  const where = change.scene ? `Scene ${change.scene}` : 'Reel';
  const body = <>
    <span className="shrink-0 text-zinc-500">{where} · {fieldName(change.field)}</span>
    <span className="min-w-0 truncate text-zinc-300" title={`${change.before} → ${change.after}`}>
      {change.before && <><span className="text-zinc-500 line-through decoration-zinc-600">{shorten(change.before)}</span> → </>}{shorten(change.after)}</span>
  </>;
  return change.scene
    ? <button type="button" onClick={() => onScene(change.scene! - 1)} className="flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left hover:bg-zinc-800/60">{body}</button>
    : <div className="flex items-baseline gap-2 px-1 py-0.5">{body}</div>;
}

/** Under the preview: tell the AI director what to change, see what it changed, and undo it. */
export function DirectorPanel(p: Props) {
  const [instruction, setInstruction] = useState('');
  const [missing, setMissing] = useState(false);
  const send = (text = instruction) => {
    if (!text.trim()) { setMissing(true); return; }
    p.onDirect(text);
    setInstruction('');
  };
  const total = p.result ? p.result.usage.input + p.result.usage.output : 0;
  return <section aria-label="Director" className="mx-auto w-full max-w-[300px] space-y-2 rounded-xl border border-zinc-800 bg-zinc-950/60 p-3">
    <div className="flex items-center gap-2">
      <Wand2 className="h-3.5 w-3.5 text-sky-400" aria-hidden="true" />
      <h3 className="text-xs font-semibold text-zinc-100">Director</h3>
      <span className="ml-auto truncate text-[10px] text-zinc-500" title="Choose the director's AI in Settings → AI models">{p.model}</span>
    </div>
    <div className="flex flex-wrap gap-1" role="group" aria-label="Suggestions">
      {SUGGESTIONS.map(text => <button key={text} type="button" disabled={p.busy} onClick={() => send(text)}
        className="rounded-full border border-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-zinc-600 hover:text-zinc-200 disabled:opacity-50">{text}</button>)}
    </div>
    <div className="relative">
      <Textarea value={instruction} rows={2} maxLength={500} disabled={p.busy} aria-label="Tell the director what to change"
        aria-invalid={missing} aria-describedby={missing ? 'director-missing' : undefined}
        placeholder="Tell the director what to change…" className="resize-none pr-9"
        onChange={event => { setInstruction(event.target.value); setMissing(false); }}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
      <button type="button" aria-label="Send to the director" disabled={p.busy} onClick={() => send()}
        className="absolute bottom-2 right-2 rounded p-1 text-zinc-500 hover:text-zinc-200 disabled:opacity-50"><CornerDownLeft className="h-3.5 w-3.5" /></button>
    </div>
    {missing && <p id="director-missing" className="text-[11px] text-red-400">Type what to change first.</p>}
    {p.directing && <p role="status" className="text-xs text-sky-300">Directing…</p>}
    {p.result && !p.directing && <div className="space-y-1.5 border-t border-zinc-800 pt-2" aria-live="polite">
      <p className="text-xs leading-5 text-zinc-200">{p.result.message}</p>
      {p.result.changes.length > 0 && <div className="max-h-40 space-y-0.5 overflow-y-auto text-[11px]">
        {p.result.changes.map((change, i) => <ChangeRow key={i} change={change} onScene={p.onScene} />)}
      </div>}
      {p.result.refused.length > 0 && <ul className="space-y-0.5 text-[11px] text-amber-300/90">
        {p.result.refused.map(reason => <li key={reason} className="flex gap-1.5"><CircleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />Not done: {reason}</li>)}
      </ul>}
      <div className="flex items-center justify-between">
        {p.result.changes.length > 0
          ? <Button variant="ghost" size="sm" disabled={p.busy} onClick={p.onUndo}><Undo2 className="h-3.5 w-3.5" />Undo</Button> : <span />}
        {total > 0 && <span className="text-[10px] tabular-nums text-zinc-500" title={`${p.result.usage.input} in, ${p.result.usage.output} out`}>~{tokens(total)} tokens</span>}
      </div>
    </div>}
  </section>;
}
