/** Before posting: hook ideas for scene 1, a story check of the running order, and the facts next to their sources. */
import { useEffect, useState } from 'react';
import { CircleAlert, CircleCheck, CircleHelp, ClipboardCheck, ListOrdered, Sparkles } from 'lucide-react';
import { compositionApi, type FactPanel, type HookIdea, type StoryReview, type StorySuggestion } from '../../services/compositionApi';
import { configuredAI } from '../../services/aiSettings';
import type { Composition } from '../../types/composition';
import { Button } from '../ui/Button';

type Tab = 'hooks' | 'story' | 'facts';
const ANGLE = { question: 'Question', number: 'Number', consequence: 'What it means', surprise: 'Surprise', human: 'Human' } as const;
const FIELD = { stat: 'Figure', headline: 'Headline', detail: 'Detail', attribution: 'Speaker', voice: 'Voice' } as const;
const VERDICT = { strong: 'Strong story', ok: 'Good, could be better', weak: 'Needs work' } as const;
const message = (err: unknown) => err instanceof Error ? err.message : 'Something went wrong.';

function suggestionLabel(s: StorySuggestion) {
  if (s.kind === 'move') return `Move scene ${s.scene} to position ${s.to}`;
  if (s.kind === 'cut') return `Cut scene ${s.scene}`;
  if (s.kind === 'rewrite') return `Rewrite scene ${s.scene}: ${s.instruction}`;
  return `Add a scene after scene ${s.after}: ${s.instruction}`;
}

/** What the director is asked to do for a rewrite or a new scene. */
export const storyInstruction = (s: StorySuggestion) => s.kind === 'rewrite'
  ? `Rewrite only scene ${s.scene}'s text from the article: ${s.instruction}. Change no other scene.`
  : `Add one story scene after scene ${s.after}, from the article: ${s.instruction}. Change no other scene.`;

export function CoachPanel({ composition, sourceText, goal, busy, onHook, onMove, onCut, onDirect }: {
  composition: Composition; sourceText: string; goal: string; busy: boolean;
  onHook: (text: string, highlight: string) => void; onMove: (from: number, to: number) => void; onCut: (scene: number) => void;
  onDirect: (instruction: string) => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab | null>(null);
  const [working, setWorking] = useState<Tab | null>(null);
  const [problem, setProblem] = useState('');
  const [hooks, setHooks] = useState<HookIdea[]>([]);
  const [story, setStory] = useState<StoryReview | null>(null);
  const [facts, setFacts] = useState<FactPanel | null>(null);
  const [done, setDone] = useState<Set<number>>(new Set());

  const run = async (which: Tab) => {
    setTab(which); setProblem(''); setWorking(which);
    try {
      const input = { ...configuredAI('write'), composition, source_text: sourceText, goal };
      if (which === 'hooks') setHooks((await compositionApi.hooks(input)).hooks);
      if (which === 'story') { setStory(await compositionApi.storyCheck(input)); setDone(new Set()); }
      if (which === 'facts') setFacts(await compositionApi.facts(composition, sourceText));
    } catch (err) { setProblem(message(err)); } finally { setWorking(null); }
  };
  // The fact panel is local and free: keep it current while it is open.
  useEffect(() => {
    if (tab !== 'facts') return;
    const timer = window.setTimeout(() => { void compositionApi.facts(composition, sourceText).then(setFacts).catch(() => undefined); }, 400);
    return () => window.clearTimeout(timer);
  }, [tab, composition, sourceText]);

  const apply = async (s: StorySuggestion, index: number) => {
    if (s.kind === 'move' && s.scene && s.to) onMove(s.scene - 1, s.to - 1);
    else if (s.kind === 'cut' && s.scene) onCut(s.scene - 1);
    else await onDirect(storyInstruction(s));
    setDone(old => new Set(old).add(index));
  };
  const disabled = busy || !!working;

  return <section aria-label="Before you post" className="space-y-2 rounded-xl border border-zinc-800 bg-zinc-950/60 p-3">
    <div className="flex flex-wrap items-center gap-2">
      <ClipboardCheck className="h-3.5 w-3.5 text-sky-400" aria-hidden="true" /><h3 className="text-xs font-semibold text-zinc-100">Before you post</h3>
      <div className="ml-auto flex flex-wrap gap-1.5">
        <Button size="sm" variant={tab === 'hooks' ? 'primary' : 'outline'} loading={working === 'hooks'} disabled={disabled}
          title="Three new openings for scene 1, from the article, scored for stopping the scroll" onClick={() => { void run('hooks'); }}>
          <Sparkles className="h-3.5 w-3.5" />Hook ideas</Button>
        <Button size="sm" variant={tab === 'story' ? 'primary' : 'outline'} loading={working === 'story'} disabled={disabled}
          title="The AI reads the running order: hook, context, key fact, why it matters, ending" onClick={() => { void run('story'); }}>
          <ListOrdered className="h-3.5 w-3.5" />Story check</Button>
        <Button size="sm" variant={tab === 'facts' ? 'primary' : 'outline'} loading={working === 'facts'} disabled={disabled}
          title="Every number, name and quote next to the article sentence it comes from. Free: no AI." onClick={() => { void run('facts'); }}>
          <ClipboardCheck className="h-3.5 w-3.5" />Check facts</Button>
      </div>
    </div>
    {problem && <p role="alert" className="text-[11px] text-amber-300">{problem}</p>}

    {tab === 'hooks' && !working && hooks.length > 0 && <ul className="space-y-1.5" aria-label="Hook ideas">
      <li className="text-[11px] text-zinc-500">Now: <span className="text-zinc-300">{composition.scenes[0]?.headline}</span></li>
      {hooks.map(h => <li key={h.text} className="flex items-start gap-2 rounded-lg border border-zinc-800 p-2">
        <div className="min-w-0 flex-1 space-y-0.5">
          <p className="text-xs font-medium text-zinc-100">{h.text}</p>
          <p className="text-[11px] text-zinc-500"><span className="rounded bg-zinc-800 px-1.5 text-zinc-300">{ANGLE[h.angle]}</span> · {h.score}/10 · {h.why}</p>
        </div>
        <Button size="sm" variant="outline" disabled={busy || composition.scenes[0]?.headline === h.text || !!composition.scenes[0]?.locked}
          title={composition.scenes[0]?.locked ? 'Scene 1 is locked' : 'Use as scene 1 headline'} onClick={() => onHook(h.text, h.highlight)}>
          {composition.scenes[0]?.headline === h.text ? 'In use' : 'Use'}</Button>
      </li>)}
    </ul>}

    {tab === 'story' && !working && story && <div className="space-y-1.5">
      <p className="text-xs text-zinc-200"><span className={story.verdict === 'strong' ? 'text-emerald-400' : story.verdict === 'weak' ? 'text-amber-300' : 'text-sky-300'}>
        {VERDICT[story.verdict]}.</span> {story.summary}</p>
      {story.suggestions.length === 0 && <p className="text-[11px] text-zinc-500">No changes suggested.</p>}
      <ul className="space-y-1.5" aria-label="Story suggestions">
        {story.suggestions.map((s, i) => <li key={i} className="flex items-start gap-2 rounded-lg border border-zinc-800 p-2">
          <div className="min-w-0 flex-1 space-y-0.5">
            <p className="text-xs text-zinc-100">{suggestionLabel(s)}</p>
            {s.problem && <p className="text-[11px] text-zinc-500">{s.problem}</p>}
          </div>
          <Button size="sm" variant="outline" disabled={busy || done.has(i)} title={s.kind === 'move' || s.kind === 'cut' ? 'Apply now' : 'Ask the director to do it; Undo is in the Director panel'}
            onClick={() => { void apply(s, i); }}>{done.has(i) ? 'Done' : 'Apply'}</Button>
        </li>)}
      </ul>
      {done.size > 0 && <p className="text-[11px] text-zinc-500">Scene numbers above refer to the order before your changes. Run Story check again to see the new order.</p>}
    </div>}

    {tab === 'facts' && facts && <div className="space-y-2">
      {!facts.has_article && <p className="text-[11px] text-amber-300">This reel has no saved article, so nothing can be checked against it.</p>}
      {facts.has_article && <p className="text-[11px] text-zinc-400">
        <span className="text-emerald-400">{facts.counts.ok} found</span> · <span className="text-sky-300">{facts.counts.check} to read yourself</span> ·{' '}
        <span className={facts.counts.problem ? 'text-red-400' : 'text-zinc-500'}>{facts.counts.problem} not in the article</span></p>}
      {facts.scenes.map(scene => <div key={scene.scene} className="space-y-1">
        <p className="text-[10px] uppercase tracking-wide text-zinc-500">Scene {scene.scene}</p>
        {scene.checks.map((c, i) => <div key={i} className="flex gap-2 text-[11px]">
          {c.status === 'ok' ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" aria-label="Found in the article" />
            : c.status === 'problem' ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" aria-label="Not in the article" />
              : <CircleHelp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-300" aria-label="Read it yourself" />}
          <div className="min-w-0 space-y-0.5">
            <p className="text-zinc-200"><span className="text-zinc-500">{FIELD[c.field]}:</span> {c.text}</p>
            {c.note && <p className="text-zinc-500">{c.note}</p>}
            {c.source && <p className="border-l-2 border-zinc-700 pl-2 italic text-zinc-500">{c.source}</p>}
            {c.status === 'check' && !c.source && <p className="text-zinc-500">No single sentence matches; it may combine several. Read it against the article.</p>}
            {c.missing_numbers.length > 0 && <p className="text-red-400">Not in the article: {c.missing_numbers.join(', ')}</p>}
            {c.missing_names.length > 0 && <p className="text-red-400">Names not in the article: {c.missing_names.join(', ')}</p>}
            {c.quote_missing && <p className="text-red-400">These words are not quoted like this in the article.</p>}
          </div>
        </div>)}
      </div>)}
    </div>}
  </section>;
}
