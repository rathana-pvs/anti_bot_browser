import { useState } from 'react';
import { Bookmark, ImagePlus, Sparkles, X } from 'lucide-react';
import { Button } from '../ui/Button';
import { Textarea } from '../ui/Textarea';
import { useFileDrop } from '../../hooks/useFileDrop';
import { usePastedImages } from '../../hooks/usePastedImages';
import { sourceKind } from '../../services/reelDocument';
import type { WriteRequest } from '../../services/reelCreator';
import type { HouseStyle, LookInfo, ReelAsset, ReelGoal } from '../../types/reelCreator';
import { Thumb } from './Thumb';

interface Props {
  /** The studio is on screen, so a paste is meant for it. */
  active: boolean;
  looks: LookInfo[];
  /** Saved newsroom looks, offered beside the looks; the reel is written for the style's look and then styled. */
  houseStyles: HouseStyle[];
  busy: boolean;
  goal: ReelGoal;
  onGoal: (goal: ReelGoal) => void;
  onCreate: (source: string, look: WriteRequest['look'], photos: ReelAsset[], goal: ReelGoal, house?: HouseStyle) => void;
  importImages: (limit: number, files?: File[]) => Promise<ReelAsset[]>;
  onBlank: () => void;
}

const MAX_PHOTOS = 8;

/** The two kinds of reel, chosen before anything is written. */
export const goals: { id: ReelGoal; name: string; note: string }[] = [
  { id: 'engage', name: 'Engage', note: 'People share their view' },
  { id: 'readmore', name: 'Read more', note: 'People open the article' },
];

export function GoalPicker({ goal, onGoal, disabled }: { goal: ReelGoal; onGoal: (goal: ReelGoal) => void; disabled?: boolean }) {
  return <div role="radiogroup" aria-label="Goal" className="grid grid-cols-2 gap-2">
    {goals.map(item => <button key={item.id} type="button" role="radio" aria-checked={goal === item.id} disabled={disabled} onClick={() => onGoal(item.id)}
      className={`rounded-xl border p-3 text-left transition-colors ${goal === item.id ? 'border-violet-500 bg-violet-950/30' : 'border-zinc-800 hover:border-zinc-600'}`}>
      <span className="block text-sm font-medium text-zinc-100">{item.name}</span>
      <span className="block text-xs text-zinc-500">{item.note}</span>
    </button>)}
  </div>;
}

/** The whole first step: paste an article, optionally add photos, press one button. */
export function SourceInput({ active, looks, houseStyles, busy, goal, onGoal, onCreate, importImages, onBlank }: Props) {
  const [source, setSource] = useState('');
  const [look, setLook] = useState<WriteRequest['look']>('auto');
  const [houseId, setHouseId] = useState<string | null>(null);
  const house = houseStyles.find(style => style.id === houseId);
  const [photos, setPhotos] = useState<ReelAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState('');
  const addPhotos = async (files?: File[]) => {
    setProblem(''); setLoading(true);
    try { const added = await importImages(MAX_PHOTOS - photos.length, files); setPhotos(old => [...old, ...added]); }
    catch (err) { setProblem(err instanceof Error ? err.message : 'Could not add photos.'); }
    finally { setLoading(false); }
  };
  const drop = useFileDrop(files => { void addPhotos(files); }, busy || loading || photos.length >= MAX_PHOTOS);
  usePastedImages(files => { void addPhotos(files); }, active && !busy && !loading && photos.length < MAX_PHOTOS);
  const ready = sourceKind(source) !== 'empty';
  const chips = [{ id: 'auto', name: 'Auto', accent: '#a1a1aa' }, ...looks];

  return <div {...drop.handlers} className={`mx-auto flex w-full max-w-2xl flex-col gap-4 rounded-2xl border p-5 transition-colors ${drop.dragging ? 'border-violet-500 bg-violet-950/20' : 'border-zinc-800 bg-zinc-950'}`}>
    <Textarea value={source} onChange={event => setSource(event.target.value)} rows={7} disabled={busy}
      aria-label="Article link or text" placeholder="Paste a news link or the article text"
      className="resize-none text-sm leading-relaxed" />

    <GoalPicker goal={goal} onGoal={onGoal} disabled={busy} />

    <div role="radiogroup" aria-label="Look" className="flex flex-wrap gap-2">
      {houseStyles.map(style => <button key={style.id} type="button" role="radio" aria-checked={house?.id === style.id}
        onClick={() => setHouseId(style.id)}
        className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors ${house?.id === style.id ? 'border-violet-400 bg-violet-950/40 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}`}>
        <Bookmark className="h-3 w-3" style={{ color: style.accent ?? looks.find(l => l.id === style.look)?.accent }} aria-hidden="true" />{style.name}
      </button>)}
      {chips.map(chip => <button key={chip.id} type="button" role="radio" aria-checked={!house && look === chip.id}
        onClick={() => { setHouseId(null); setLook(chip.id as WriteRequest['look']); }}
        className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors ${!house && look === chip.id ? 'border-zinc-300 bg-zinc-800 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}`}>
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: chip.accent }} />{chip.name}
      </button>)}
    </div>

    <div className="flex flex-wrap items-center gap-2">
      {photos.map(photo => <div key={photo.id} className="group relative h-12 w-12 overflow-hidden rounded-lg border border-zinc-800">
        <Thumb id={photo.id} className="h-full w-full" />
        <button type="button" aria-label="Remove photo" onClick={() => setPhotos(old => old.filter(p => p.id !== photo.id))}
          className="absolute inset-0 flex items-center justify-center bg-black/60 opacity-0 transition-opacity focus:opacity-100 group-hover:opacity-100"><X className="h-4 w-4" /></button>
      </div>)}
      {photos.length > 0 && <span className="text-xs text-zinc-500">{photos.length} {photos.length === 1 ? 'scene' : 'scenes'}</span>}
      {photos.length < MAX_PHOTOS && <Button type="button" variant="outline" size="sm" loading={loading} disabled={busy}
        onClick={() => { void addPhotos(); }}><ImagePlus className="h-3.5 w-3.5" />Photos</Button>}
      {photos.length < MAX_PHOTOS && <span className="text-xs text-zinc-500">or copy a photo and press Ctrl+V</span>}
    </div>

    {problem && <p role="alert" className="text-xs text-red-400">{problem}</p>}

    <div className="flex items-center justify-between gap-3">
      <button type="button" onClick={onBlank} disabled={busy} className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline">Write it myself</button>
      <Button type="button" size="lg" loading={busy} disabled={!ready || busy} onClick={() => onCreate(source, look, photos, goal, house)}>
        <Sparkles className="h-4 w-4" />Create reel
      </Button>
    </div>
  </div>;
}
