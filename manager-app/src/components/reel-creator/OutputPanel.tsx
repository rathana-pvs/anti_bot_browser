import { Check, Download, Film, Image as ImageIcon, Play, Radio, Send } from 'lucide-react';
import { Button } from '../ui/Button';
import type { ReelOutput } from '../../hooks/useReelStudio';
import type { ReelRatio } from '../../types/reelCreator';

interface Props {
  output: ReelOutput;
  onOutput: (output: ReelOutput) => void;
  photoRatio: ReelRatio;
  onPhotoRatio: (ratio: ReelRatio) => void;
  /** The scene the photo is made from: the one selected in the preview. */
  scene: number;
  seconds: number;
  reelRatio: ReelRatio;
  /** The chosen video's render is running, or the studio is busy with something else. */
  rendering: boolean;
  busy: boolean;
  makingPhoto: boolean;
  /** A full-quality video of the chosen output matches the edits. */
  ready: boolean;
  onCreate: (kind: 'final' | 'preview') => void;
  onSend: () => void;
  onSave: () => void;
}

const OUTPUTS: { value: ReelOutput; label: string; icon: typeof Film }[] = [
  { value: 'photo', label: 'Photo', icon: ImageIcon },
  { value: 'reel', label: 'Reel', icon: Film },
  { value: 'live', label: 'Live', icon: Radio },
];

/** Under the preview: use the reel as a photo post, a Reel, or a Live video, then send it on or save the file. */
export function OutputPanel(p: Props) {
  const detail = { photo: `Scene ${p.scene + 1} · ${p.photoRatio}`, reel: `${p.seconds}s · ${p.reelRatio}`, live: `${p.seconds}s · ${p.reelRatio}` };
  return <div className="mx-auto flex w-full max-w-[300px] flex-col gap-2">
    <div role="radiogroup" aria-label="Use as" className="grid grid-cols-3 gap-1.5">
      {OUTPUTS.map(({ value, label, icon: Icon }) => <button key={value} type="button" role="radio" aria-checked={p.output === value} onClick={() => p.onOutput(value)}
        className={`flex flex-col items-center gap-0.5 rounded-lg border px-1 py-2 text-xs transition-colors ${p.output === value ? 'border-violet-500 bg-violet-950/40 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200'}`}>
        <Icon className="h-4 w-4" aria-hidden="true" />{label}
        <span className="text-[10px] text-zinc-500">{detail[value]}</span>
      </button>)}
    </div>

    {p.output === 'photo' ? <>
      <div role="radiogroup" aria-label="Photo ratio" className="flex items-center justify-center gap-1 text-xs">
        <span className="mr-1 text-zinc-500">Ratio</span>
        {(['4:5', '9:16'] as const).map(ratio => <button key={ratio} type="button" role="radio" aria-checked={p.photoRatio === ratio} onClick={() => p.onPhotoRatio(ratio)}
          className={`rounded-md px-2.5 py-1 transition-colors ${p.photoRatio === ratio ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-200'}`}>{ratio}</button>)}
      </div>
      <Button size="lg" loading={p.makingPhoto} disabled={p.busy} onClick={p.onSend}><Send className="h-4 w-4" />Use for photo post</Button>
      <Button variant="outline" disabled={p.busy} onClick={p.onSave}><Download className="h-4 w-4" />Save photo</Button>
      <p className="text-center text-xs text-zinc-500">Scene {p.scene + 1}, fully drawn. Pick another scene with the dots above.</p>
    </> : p.ready ? <>
      <Button size="lg" onClick={p.onSend}><Send className="h-4 w-4" />{p.output === 'live' ? 'Use for Live' : 'Use for Reel'}</Button>
      <Button variant="outline" disabled={p.busy} onClick={p.onSave}><Download className="h-4 w-4" />Save video</Button>
      <p className="text-center text-xs text-zinc-500">{p.output === 'live' ? 'Choose accounts and playback, then go live now or schedule.' : 'Choose accounts, then post now or schedule.'}</p>
    </> : <>
      <Button size="lg" loading={p.rendering} disabled={p.rendering || p.busy} onClick={() => p.onCreate('final')}>
        <Check className="h-4 w-4" />{p.output === 'live' ? 'Create Live video' : 'Create video'} · {p.seconds}s
      </Button>
      <Button variant="outline" disabled={p.rendering || p.busy} onClick={() => p.onCreate('preview')}><Play className="h-4 w-4" />Quick preview</Button>
      {p.output === 'live' && <p className="text-center text-xs text-zinc-500">No progress bar, so a loop never shows it restarting. Breaking shows its LIVE badge.</p>}
    </>}
  </div>;
}
