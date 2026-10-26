import { ArrowDown, ArrowUp, Frame, ImagePlus, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';
import { MAX_SCENES, sceneSeconds } from '../../services/reelDocument';
import type { Reel, ReelScene, SceneKind } from '../../types/reelCreator';
import { Thumb } from './Thumb';

interface Props {
  reel: Reel;
  hooks: string[];
  selected: number;
  busy: boolean;
  writing: boolean;
  adding: boolean;
  canWrite: boolean;
  onSelect: (index: number) => void;
  onEdit: (index: number, patch: Partial<ReelScene>) => void;
  onHook: (hook: string) => void;
  onMoreHooks: () => void;
  onPhoto: (index: number) => void;
  onPhotos: () => void;
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
  onAdd: () => void;
}

const kinds: { value: SceneKind; label: string }[] = [
  { value: 'story', label: 'Story' }, { value: 'stat', label: 'Figure' }, { value: 'quote', label: 'Quote' }, { value: 'end', label: 'End card' },
];
const headlineHint: Record<SceneKind, string> = { story: 'Headline', stat: 'What the figure counts', quote: 'Quoted words', end: 'Question or where to read more' };

/** Mirrors the renderer: whole words, any case; a highlight that is not in the headline is simply not drawn. */
const includesWords = (headline: string, phrase: string) => {
  const escaped = phrase.trim().split(/\s+/).map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+');
  return !!escaped && new RegExp(`(?<![\\p{L}\\p{N}_])${escaped}(?![\\p{L}\\p{N}_])`, 'iu').test(headline);
};

export function SceneList(p: Props) {
  const first = p.reel.scenes[0]?.headline ?? '';
  return <section aria-label="Scenes" className="space-y-3">
    {(p.hooks.length > 0 || p.canWrite) && <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Hook">
      {p.hooks.map(hook => <button key={hook} type="button" onClick={() => p.onHook(hook)} aria-pressed={hook === first}
        className={`max-w-full truncate rounded-full border px-3 py-1.5 text-xs transition-colors ${hook === first ? 'border-violet-500 bg-violet-950/40 text-violet-100' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}`}>{hook}</button>)}
      {p.canWrite && <IconButton aria-label="More hooks" title="More hooks" loading={p.writing} disabled={p.busy} onClick={p.onMoreHooks}><RefreshCw className="h-4 w-4" /></IconButton>}
    </div>}

    <ol className="space-y-2">
      {p.reel.scenes.map((scene, i) => <li key={i} onFocusCapture={() => p.onSelect(i)} onClick={() => p.onSelect(i)}
        className={`flex gap-3 rounded-xl border p-3 transition-colors ${i === p.selected ? 'border-violet-600/70 bg-zinc-900/60' : 'border-zinc-800'}`}>
        <button type="button" aria-label={scene.image_id ? `Change photo for scene ${i + 1}` : `Add photo for scene ${i + 1}`} disabled={p.busy}
          onClick={event => { event.stopPropagation(); p.onPhoto(i); }}
          className="group relative h-[72px] w-[72px] shrink-0 overflow-hidden rounded-lg border border-zinc-800 hover:border-zinc-600">
          <Thumb id={scene.image_id} className="h-full w-full" />
          <span className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"><ImagePlus className="h-4 w-4" /></span>
        </button>
        <div className="min-w-0 flex-1 space-y-1.5">
          <Input value={scene.headline} onChange={event => p.onEdit(i, { headline: event.target.value })} maxLength={120} aria-label={`Scene ${i + 1} headline`} placeholder={headlineHint[scene.kind ?? 'story']} className="font-medium" />
          <Input value={scene.detail} onChange={event => p.onEdit(i, { detail: event.target.value })} maxLength={160} aria-label={`Scene ${i + 1} detail`} placeholder="Detail" />
          <div className="flex gap-1.5">
            <div className="w-28 shrink-0"><Select value={scene.kind ?? 'story'} options={kinds} ariaLabel={`Scene ${i + 1} type`}
              onValueChange={value => p.onEdit(i, { kind: value as SceneKind })} /></div>
            {scene.kind === 'stat' && <Input value={scene.stat ?? ''} onChange={event => p.onEdit(i, { stat: event.target.value })} maxLength={24}
              aria-label={`Scene ${i + 1} figure`} placeholder="Figure, e.g. 7-2" className="w-32 shrink-0" />}
            {scene.kind === 'quote' && <Input value={scene.attribution ?? ''} onChange={event => p.onEdit(i, { attribution: event.target.value })} maxLength={60}
              aria-label={`Scene ${i + 1} speaker`} placeholder="Who said it" className="min-w-0 flex-1" />}
            <Input value={scene.highlight ?? ''} onChange={event => p.onEdit(i, { highlight: event.target.value })} maxLength={40}
              aria-label={`Scene ${i + 1} highlighted words`} placeholder="Highlight words" title="Words from the headline shown in the accent color"
              className={`min-w-0 flex-1 ${scene.highlight && !includesWords(scene.headline, scene.highlight) ? 'border-amber-600' : ''}`} />
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-center justify-between">
          <span className="text-[11px] tabular-nums text-zinc-500">{sceneSeconds(scene)}s</span>
          <div className="flex">
            {scene.image_id && <IconButton aria-label="Show whole photo" title="Show whole photo (blurred fill)" aria-pressed={scene.fit === 'blur'}
              className={scene.fit === 'blur' ? 'text-violet-300' : undefined}
              onClick={event => { event.stopPropagation(); p.onEdit(i, { fit: scene.fit === 'blur' ? 'cover' : 'blur' }); }}><Frame className="h-3.5 w-3.5" /></IconButton>}
            <IconButton aria-label="Move up" disabled={i === 0} onClick={event => { event.stopPropagation(); p.onMove(i, i - 1); }}><ArrowUp className="h-3.5 w-3.5" /></IconButton>
            <IconButton aria-label="Move down" disabled={i === p.reel.scenes.length - 1} onClick={event => { event.stopPropagation(); p.onMove(i, i + 1); }}><ArrowDown className="h-3.5 w-3.5" /></IconButton>
            <IconButton aria-label="Delete scene" disabled={p.reel.scenes.length < 2} onClick={event => { event.stopPropagation(); p.onRemove(i); }}><Trash2 className="h-3.5 w-3.5" /></IconButton>
          </div>
        </div>
      </li>)}
    </ol>

    <div className="flex gap-2">
      <Button variant="outline" size="sm" loading={p.adding} disabled={p.busy || p.reel.scenes.length >= MAX_SCENES} onClick={p.onAdd}><Plus className="h-3.5 w-3.5" />Scene</Button>
      <Button variant="outline" size="sm" disabled={p.busy} onClick={p.onPhotos}><ImagePlus className="h-3.5 w-3.5" />Photos</Button>
    </div>
  </section>;
}
