import { useEffect, useMemo, useState } from 'react';
import { CircleAlert, Film, ImagePlus, Lock, LockOpen, Plus, Send, ShieldCheck, Sparkles, Wand2, X } from 'lucide-react';
import { useAIReelStudio } from '../../hooks/useAIReelStudio';
import { compositionApi } from '../../services/compositionApi';
import { loadAISettings, modelLabel } from '../../services/aiSettings';
import { reelApi } from '../../services/reelCreator';
import type { Composition, CompositionScene, Part, PartKind, PartsCatalog } from '../../types/composition';
import type { CreatedReelMedia, ReelAsset, ReelGoal, StyleRole } from '../../types/reelCreator';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';
import { Spinner } from '../ui/Spinner';
import { Textarea } from '../ui/Textarea';
import { DirectorPanel } from '../reel-creator/DirectorPanel';
import { Thumb } from '../reel-creator/Thumb';

interface Props { active: boolean; onUseReel: (media: CreatedReelMedia) => void }

const SCENARIOS = ['Calm explainer', 'Breaking update', 'Energetic, for younger viewers', 'Serious and respectful'];
const GOALS = [{ value: 'engage', label: 'Get people talking' }, { value: 'readmore', label: 'Send them to the article' }];
const PACING = [{ value: 'calm', label: 'Calm pace' }, { value: 'normal', label: 'Normal pace' }, { value: 'fast', label: 'Fast pace' }];
const KIND_LABEL = { story: 'Story', stat: 'Figure', quote: 'Quote', end: 'End card' } as const;
/** The shared Select cannot hold an empty value; this stands for "keep the default". */
const DEFAULT = '__default';

/** The parts of one kind a scene can use, as Select options, with the recipe's own choice first. */
function options(catalog: PartsCatalog, kind: PartKind, scene: CompositionScene, keep?: string) {
  const fits = (p: Part) => p.kind === kind && p.applies_to.includes(scene.kind) && (!p.needs_photo || !!scene.background.image_id);
  const list = catalog.parts.filter(fits).map(p => ({ value: p.id, label: p.label }));
  return keep ? [{ value: DEFAULT, label: keep }, ...list] : list;
}
const hint = (catalog: PartsCatalog, kind: PartKind, id: string | null | undefined) => catalog.parts.find(p => p.kind === kind && p.id === id)?.use_when ?? '';

function Start({ busy, recipes, onCompose }: { busy: boolean; recipes: { value: string; label: string }[];
  onCompose: (source: string, photos: ReelAsset[], scenario: string, recipe: string | null, goal: ReelGoal) => void }) {
  const [source, setSource] = useState('');
  const [photos, setPhotos] = useState<ReelAsset[]>([]);
  const [scenario, setScenario] = useState('');
  const [recipe, setRecipe] = useState('');
  const [goal, setGoal] = useState<ReelGoal>('engage');
  const [missing, setMissing] = useState(false);
  const [problem, setProblem] = useState('');
  const pick = async () => {
    try {
      const picked = await reelApi.pickImages(7 - photos.length);
      if (picked) { setPhotos(old => [...old, ...picked.assets].slice(0, 7)); setProblem(picked.errors.join(' ')); }
    } catch (err) { setProblem(err instanceof Error ? err.message : 'Could not add the photos.'); }
  };
  const go = () => { if (!source.trim()) { setMissing(true); return; } onCompose(source, photos, scenario, recipe || null, goal); };
  return <section aria-label="Compose a reel" className="mx-auto max-w-2xl space-y-5 rounded-2xl border border-zinc-800 bg-zinc-950/60 p-6">
    <div className="flex items-center gap-2"><Wand2 className="h-4 w-4 text-sky-400" aria-hidden="true" /><h2 className="text-sm font-semibold text-zinc-100">Compose a reel with AI</h2></div>
    <p className="text-xs leading-5 text-zinc-400">The AI writes the scenes from the article, then picks each scene's layout, motion and transitions for the story. Every choice is checked so the reel stays readable, and you can change any part afterwards.</p>
    <label className="block text-xs font-medium text-zinc-300">Article text or link
      <Textarea value={source} rows={6} className="mt-2" placeholder="Paste the article, or a link to it" aria-invalid={missing}
        onChange={event => { setSource(event.target.value); setMissing(false); }} />
    </label>
    {missing && <p className="text-[11px] text-red-400">Paste the article or its link first.</p>}
    <div className="space-y-2">
      <p className="text-xs font-medium text-zinc-300">Photos <span className="font-normal text-zinc-500">(one per scene, in order)</span></p>
      <div className="flex flex-wrap gap-2">
        {photos.map((photo, i) => <div key={photo.id} className="group relative h-16 w-16 overflow-hidden rounded-lg border border-zinc-800">
          <Thumb id={photo.id} className="h-full w-full" />
          <button type="button" aria-label={`Remove photo ${i + 1}`} onClick={() => setPhotos(old => old.filter(p => p.id !== photo.id))}
            className="absolute right-0.5 top-0.5 rounded bg-black/70 p-0.5 text-zinc-200 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"><X className="h-3 w-3" /></button>
        </div>)}
        {photos.length < 7 && <button type="button" onClick={() => { void pick(); }} className="flex h-16 w-16 items-center justify-center rounded-lg border border-dashed border-zinc-700 text-zinc-500 hover:border-zinc-500 hover:text-zinc-300" aria-label="Add photos"><ImagePlus className="h-4 w-4" /></button>}
      </div>
      {problem && <p className="text-[11px] text-amber-300">{problem}</p>}
    </div>
    <label className="block text-xs font-medium text-zinc-300">Scenario <span className="font-normal text-zinc-500">(optional)</span>
      <Input value={scenario} maxLength={300} className="mt-2" placeholder="What should it feel like?" onChange={event => setScenario(event.target.value)} />
    </label>
    <div className="flex flex-wrap gap-1.5">{SCENARIOS.map(text => <button key={text} type="button" onClick={() => setScenario(text)}
      className={`rounded-full border px-2.5 py-1 text-[11px] ${scenario === text ? 'border-sky-500 text-sky-200' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}`}>{text}</button>)}</div>
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1.5"><p className="text-xs font-medium text-zinc-300">Recipe</p>
        <Select ariaLabel="Recipe" value={recipe || DEFAULT} options={[{ value: DEFAULT, label: 'Let the AI choose' }, ...recipes]}
          onValueChange={value => setRecipe(value === DEFAULT ? '' : value)} /></div>
      <div className="space-y-1.5"><p className="text-xs font-medium text-zinc-300">Goal</p>
        <Select ariaLabel="Goal" value={goal} options={GOALS} onValueChange={value => setGoal(value as ReelGoal)} /></div>
    </div>
    <div className="flex items-center justify-between">
      <span className="text-[11px] text-zinc-500">Writing with {modelLabel(loadAISettings(), 'write')}</span>
      <Button loading={busy} onClick={go}><Sparkles className="h-3.5 w-3.5" />Compose</Button>
    </div>
  </section>;
}

function Preview({ composition, scene, onScene, video }: { composition: Composition; scene: number; onScene: (i: number) => void; video: string }) {
  const [poster, setPoster] = useState('');
  const [problem, setProblem] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let url = '';
    const timer = window.setTimeout(() => {
      compositionApi.poster(composition, Math.min(scene, composition.scenes.length - 1), controller.signal).then(blob => {
        url = URL.createObjectURL(blob);
        setPoster(old => { if (old) URL.revokeObjectURL(old); return url; });
        setProblem('');
      }).catch(err => { if (!controller.signal.aborted) setProblem(err instanceof Error ? err.message : 'Preview unavailable.'); });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [composition, scene]);
  const tall = composition.ratio === '9:16';
  return <div className="mx-auto w-full max-w-[300px] space-y-2">
    <div className={`relative overflow-hidden rounded-[28px] border-4 border-zinc-800 bg-zinc-900 ${tall ? 'aspect-[9/16]' : 'aspect-[4/5]'}`}>
      {video ? <video src={video} controls playsInline className="h-full w-full object-cover" />
        : poster ? <img src={poster} alt={`Scene ${scene + 1} preview`} className="h-full w-full object-cover" />
        : <div className="flex h-full items-center justify-center"><Spinner label="Drawing the preview" className="text-zinc-500" /></div>}
    </div>
    {problem && <p role="alert" className="text-[11px] text-amber-300">{problem}</p>}
    <div className="flex justify-center gap-1.5" role="tablist" aria-label="Scenes">
      {composition.scenes.map((_, i) => <button key={i} type="button" role="tab" aria-selected={i === scene} aria-label={`Scene ${i + 1}`} onClick={() => onScene(i)}
        className={`h-2 rounded-full transition-all ${i === scene ? 'w-6 bg-sky-400' : 'w-2 bg-zinc-700 hover:bg-zinc-500'}`} />)}
    </div>
  </div>;
}

function SceneCard({ scene, index, count, catalog, selected, busy, onSelect, onEdit }: {
  scene: CompositionScene; index: number; count: number; catalog: PartsCatalog; selected: boolean; busy: boolean;
  onSelect: () => void; onEdit: (patch: Partial<CompositionScene>) => void;
}) {
  const role = (r: StyleRole) => scene.styles[r]?.animation ?? '';
  const setRole = (r: StyleRole, animation: string) =>
    onEdit({ styles: { ...scene.styles, [r]: { ...(scene.styles[r] ?? {}), animation: animation || null } } });
  const chip = (label: string, kind: PartKind, value: string, change: (v: string) => void, keep?: string) =>
    <div className="min-w-0 space-y-1" title={hint(catalog, kind, value)}>
      <p className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</p>
      <Select ariaLabel={`Scene ${index + 1} ${label.toLowerCase()}`} value={value || DEFAULT} options={options(catalog, kind, scene, keep)}
        onValueChange={v => change(v === DEFAULT ? '' : v)} />
    </div>;
  return <li onClick={onSelect} onFocusCapture={onSelect}
    className={`space-y-2.5 rounded-xl border p-3 transition-colors ${selected ? 'border-sky-600/70 bg-zinc-900/60' : 'border-zinc-800'}`}>
    <div className="flex items-start gap-3">
      <div className="h-[72px] w-[72px] shrink-0 overflow-hidden rounded-lg border border-zinc-800"><Thumb id={scene.background.image_id} className="h-full w-full" /></div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2 text-[11px] text-zinc-500">Scene {index + 1} · {KIND_LABEL[scene.kind]}
          {scene.kind === 'stat' && scene.stat && <span className="rounded bg-zinc-800 px-1.5 text-zinc-300">{scene.stat}</span>}</div>
        <Input value={scene.headline} maxLength={120} aria-label={`Scene ${index + 1} headline`} onChange={event => onEdit({ headline: event.target.value })} className="font-medium" />
        <Input value={scene.detail} maxLength={160} aria-label={`Scene ${index + 1} detail`} placeholder="Detail" onChange={event => onEdit({ detail: event.target.value })} />
      </div>
      <IconButton aria-label={scene.locked ? `Unlock scene ${index + 1}` : `Lock scene ${index + 1}`} aria-pressed={!!scene.locked} disabled={busy}
        title={scene.locked ? 'Locked: the AI leaves this scene alone' : 'Lock so the AI leaves this scene alone'} className={scene.locked ? 'text-amber-300' : undefined}
        onClick={event => { event.stopPropagation(); onEdit({ locked: scene.locked ? null : true }); }}>
        {scene.locked ? <Lock className="h-3.5 w-3.5" /> : <LockOpen className="h-3.5 w-3.5" />}</IconButton>
    </div>
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
      {chip('Layout', 'layout', scene.layout, layout => onEdit({ layout: layout as CompositionScene['layout'] }))}
      {chip('Camera', 'camera', scene.camera ?? '', camera => onEdit({ camera: (camera || null) as CompositionScene['camera'] }), 'Recipe default')}
      {index > 0 && chip('Transition in', 'transition', scene.transition ?? '', t => onEdit({ transition: (t || null) as CompositionScene['transition'] }), 'Crossfade')}
      {chip('Headline', 'animation', role('headline'), a => setRole('headline', a), 'Layout default')}
      {scene.detail && chip('Detail', 'animation', role('detail'), a => setRole('detail', a), 'Layout default')}
      {(scene.kind === 'stat' || scene.kind === 'quote') && chip(scene.kind === 'stat' ? 'Figure' : 'Quote mark', 'animation', role('lead'), a => setRole('lead', a), 'Layout default')}
    </div>
    {index === count - 1 && scene.kind !== 'end' && <p className="text-[11px] text-zinc-500">Tip: an end card asks viewers to comment or read on.</p>}
  </li>;
}

/** AI Reel Studio: the AI composes each scene from independent parts; the operator can change any part or ask the director. */
export function AIReelStudio({ active, onUseReel }: Props) {
  const studio = useAIReelStudio(active, onUseReel);
  const { doc, catalog, busy, job, fresh } = studio;
  const [scene, setScene] = useState(0);
  const [video, setVideo] = useState('');
  useEffect(() => { setScene(0); }, [studio.draftId]);
  useEffect(() => { if (doc) setScene(i => Math.min(i, doc.composition.scenes.length - 1)); }, [doc]);
  const done = job && fresh && job.state === 'completed' ? job : null;
  useEffect(() => {
    if (!done) { setVideo(''); return; }
    let url = '', cancelled = false;
    reelApi.video(done.id).then(blob => { if (!cancelled) { url = URL.createObjectURL(blob); setVideo(url); } }).catch(() => undefined);
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [done?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const recipes = useMemo(() => (catalog?.recipes ?? []).map(r => ({ value: r.id, label: r.label })), [catalog]);
  const changed = useMemo(() => new Set((studio.directed?.changes ?? []).flatMap(c => c.scene ? [c.scene - 1] : [])), [studio.directed]);

  if (!studio.loaded || !catalog) return <div className="flex h-full items-center justify-center p-10">
    {studio.error ? <Alert variant="danger" className="max-w-md">{studio.error}</Alert> : <Spinner label="Loading AI Reel Studio" className="text-zinc-500" />}
  </div>;

  const recent = studio.drafts.map(d => ({ value: d.id, label: d.reel.scenes.find(s => s.headline)?.headline.slice(0, 60) || 'Untitled reel' }));
  const rendering = busy === 'render' || studio.working;
  return <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 lg:p-6">
    <header className="flex items-center gap-3">
      <Wand2 className="h-4 w-4 text-sky-400" aria-hidden="true" /><h1 className="text-sm font-semibold text-zinc-100">AI Reel Studio</h1>
      {recent.length > 0 && <div className="w-72 max-w-full shrink-0"><Select value={recent.some(r => r.value === studio.draftId) ? studio.draftId : ''} options={recent}
        placeholder="Recent reels" ariaLabel="Recent AI reels" onValueChange={id => studio.open(studio.drafts.find(d => d.id === id) ?? null)} /></div>}
      {doc && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => studio.open(null)}><Plus className="h-3.5 w-3.5" />New</Button>}
      <span className="ml-auto text-xs text-zinc-500" aria-live="polite">{studio.saveState}</span>
    </header>
    {studio.error && <Alert variant="danger" role="alert">{studio.error}</Alert>}

    {!doc ? <Start busy={busy === 'compose'} recipes={recipes} onCompose={(...args) => { void studio.compose(...args); }} />
      : <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4 lg:sticky lg:top-4 lg:self-start">
          <Preview composition={doc.composition} scene={scene} onScene={setScene} video={video} />
          <DirectorPanel model={modelLabel(loadAISettings(), 'direct')} busy={!!busy} directing={busy === 'direct'} result={studio.directed}
            onDirect={instruction => { void studio.direct(instruction); }} onUndo={studio.undoDirected} onScene={setScene} />
          <div className="mx-auto flex w-full max-w-[300px] flex-col gap-2">
            {rendering && job && <div className="flex items-center justify-between text-xs text-zinc-400"><span>Rendering… {Math.round((job.progress ?? 0) * 100)}%</span>
              <button type="button" className="text-zinc-500 hover:text-zinc-200" onClick={() => { void studio.cancelJob(); }}>Cancel</button></div>}
            {job?.state === 'failed' && fresh && <p className="text-[11px] text-red-400">{job.error || 'The render failed.'}</p>}
            <Button loading={rendering} disabled={!!busy && !rendering} onClick={() => { void studio.startRender('final'); }}><Film className="h-3.5 w-3.5" />{done ? 'Render again' : 'Make video'}</Button>
            <Button variant="outline" disabled={!done || !!busy} loading={busy === 'send'} onClick={() => { void studio.send(); }}><Send className="h-3.5 w-3.5" />Use in Posts</Button>
          </div>
        </div>

        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-48 space-y-1"><p className="text-[10px] uppercase tracking-wide text-zinc-500">Recipe</p>
              <Select ariaLabel="Recipe" value={doc.composition.recipe ?? 'clean'} options={recipes}
                onValueChange={recipe => studio.setComposition(c => ({ ...c, recipe }))} /></div>
            <div className="w-40 space-y-1"><p className="text-[10px] uppercase tracking-wide text-zinc-500">Pacing</p>
              <Select ariaLabel="Pacing" value={doc.composition.pacing} options={PACING}
                onValueChange={pacing => studio.setComposition(c => ({ ...c, pacing: pacing as Composition['pacing'] }))} /></div>
            <Button variant="outline" size="sm" loading={busy === 'review'} disabled={!!busy} onClick={studio.reviewNow}
              title="Fix anything unreadable: text over faces, bright photos, too much motion"><ShieldCheck className="h-3.5 w-3.5" />Check quality</Button>
            <Button variant="outline" size="sm" loading={busy === 'photos'} disabled={!!busy} onClick={() => { void studio.addPhotos(); }}><ImagePlus className="h-3.5 w-3.5" />Photos</Button>
          </div>
          {studio.notes.length > 0 && <div className="space-y-1 rounded-xl border border-zinc-800 bg-zinc-950/60 p-3 text-[11px]" aria-live="polite">
            <div className="flex items-center justify-between"><p className="font-medium text-zinc-300">Quality check</p>
              <button type="button" aria-label="Dismiss quality notes" className="text-zinc-500 hover:text-zinc-200" onClick={() => studio.setNotes([])}><X className="h-3.5 w-3.5" /></button></div>
            {studio.notes.map((note, i) => <p key={i} className={`flex gap-1.5 ${note.rule === 'fit' ? 'text-red-400' : 'text-zinc-400'}`}>
              <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />{note.scene ? `Scene ${note.scene}: ` : ''}{note.message}</p>)}
          </div>}
          <ol className="space-y-2" aria-label="Scenes">
            {doc.composition.scenes.map((s, i) => <SceneCard key={i} scene={s} index={i} count={doc.composition.scenes.length} catalog={catalog}
              selected={i === scene || changed.has(i)} busy={!!busy} onSelect={() => setScene(i)} onEdit={patch => studio.editScene(i, patch)} />)}
          </ol>
          <label className="block text-xs font-medium text-zinc-300">Caption
            <Textarea value={doc.caption} rows={5} className="mt-2" onChange={event => studio.setDoc(d => ({ ...d, caption: event.target.value }))} />
          </label>
          {doc.goal === 'readmore' && <label className="block text-xs font-medium text-zinc-300">First comment
            <Textarea value={doc.comment} rows={2} className="mt-2" onChange={event => studio.setDoc(d => ({ ...d, comment: event.target.value }))} />
          </label>}
          <Button variant="ghost" size="sm" className="text-zinc-500" onClick={() => { void studio.discard(); }}>Delete this reel</Button>
        </div>
      </div>}
  </div>;
}
