import { useEffect, useRef, useState } from 'react';
import { Pause, Play, RefreshCw, Sparkles, Upload } from 'lucide-react';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { IconButton } from '../ui/IconButton';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';
import { Textarea } from '../ui/Textarea';
import { reelApi } from '../../services/reelCreator';
import type { LookInfo, MusicTrack, Reel, ReelGoal, ReelMotion } from '../../types/reelCreator';
import { Thumb } from './Thumb';
import { GoalPicker } from './SourceInput';

interface Props {
  reel: Reel;
  looks: LookInfo[];
  tracks: MusicTrack[];
  caption: string;
  goal: ReelGoal;
  onGoal: (goal: ReelGoal) => void;
  comment: string;
  onComment: (value: string) => void;
  busy: boolean;
  writing: boolean;
  canWrite: boolean;
  onBrand: (patch: Partial<Pick<Reel, 'brand' | 'logo_id' | 'source'>>) => void;
  onDate: (dateline: string) => void;
  onProgress: (show: boolean) => void;
  onMotion: (motion: ReelMotion) => void;
  onMusic: (music: string) => void;
  onPickMusic: () => void;
  onLogo: (file: File) => void;
  onCaption: (value: string) => void;
  onRewrite: () => void;
}

/** Brand, music and the post caption: set once, rarely touched. */
export function FinishPanel(p: Props) {
  const file = useRef<HTMLInputElement>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [manual, setManual] = useState(false);
  const track = p.reel.music === 'auto' ? p.looks.find(l => l.id === p.reel.look)?.music : p.tracks.some(t => t.id === p.reel.music) ? p.reel.music : undefined;
  const stop = () => { audio.current?.pause(); audio.current = null; setPlaying(false); };
  useEffect(() => stop, []);
  useEffect(stop, [track]);
  const play = async () => {
    if (playing || !track) return stop();
    try {
      const player = new Audio(URL.createObjectURL(await reelApi.musicPreview(track)));
      player.onended = stop;
      audio.current = player; setPlaying(true);
      await player.play();
    } catch { stop(); }
  };
  const options = [
    { value: 'auto', label: 'Music: match the look' }, { value: 'off', label: 'No music' },
    ...p.tracks.map(t => ({ value: t.id, label: t.name })),
    { value: 'custom', label: p.reel.music_id ? 'My own file' : 'Upload my own…' },
  ];
  const motions = [{ value: 'auto', label: 'Zoom in and out' }, { value: 'zoom_in', label: 'Zoom in' }, { value: 'zoom_out', label: 'Zoom out' }, { value: 'none', label: 'Still' }];
  return <section aria-label="Brand, music and caption" className="space-y-4">
    <div className="flex items-center gap-2">
      <Button type="button" variant="outline" size="icon" aria-label={p.reel.logo_id ? 'Change logo' : 'Add logo'}
        disabled={p.busy} onClick={() => file.current?.click()} className="shrink-0 overflow-hidden p-0">
        {p.reel.logo_id ? <Thumb id={p.reel.logo_id} className="h-full w-full" /> : <Upload className="h-4 w-4" />}
      </Button>
      <input ref={file} type="file" accept="image/png" hidden onChange={event => { const chosen = event.target.files?.[0]; event.target.value = ''; if (chosen) p.onLogo(chosen); }} />
      <Input value={p.reel.brand} onChange={event => p.onBrand({ brand: event.target.value })} maxLength={48} aria-label="Brand name" placeholder="Brand" />
      <Input value={p.reel.source} onChange={event => p.onBrand({ source: event.target.value })} maxLength={90} aria-label="Source" placeholder="Source" />
      <Input value={p.reel.dateline} onChange={event => p.onDate(event.target.value)} maxLength={32} aria-label="Date" placeholder="Date" className="max-w-[130px]" />
    </div>
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1"><Select value={p.reel.music} options={options} ariaLabel="Music" disabled={p.busy}
        onValueChange={value => { if (value === 'custom' && !p.reel.music_id) p.onPickMusic(); else p.onMusic(value); }} /></div>
      <IconButton aria-label={playing ? 'Stop music' : 'Play music'} disabled={!track} onClick={() => { void play(); }}>
        {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</IconButton>
      {p.reel.music === 'custom' && <Button type="button" variant="outline" size="sm" disabled={p.busy} onClick={p.onPickMusic}>Change</Button>}
      <div className="w-36 shrink-0"><Select value={p.reel.motion} options={motions} ariaLabel="Photo motion" onValueChange={value => p.onMotion(value as ReelMotion)} /></div>
      <label className="flex shrink-0 items-center gap-2 text-xs text-zinc-300"><Checkbox checked={p.reel.show_progress} onCheckedChange={p.onProgress} />Progress</label>
    </div>
    <GoalPicker goal={p.goal} onGoal={p.onGoal} />
    {p.caption || manual || !p.canWrite
      ? <div className="relative">
        <Textarea value={p.caption} onChange={event => p.onCaption(event.target.value)} rows={6} maxLength={8000} aria-label="Post caption" placeholder="Caption" className="pr-10 leading-relaxed" />
        {p.canWrite && <IconButton aria-label="Rewrite caption" title="Rewrite caption" loading={p.writing} disabled={p.busy} onClick={p.onRewrite} className="absolute right-1 top-1"><RefreshCw className="h-4 w-4" /></IconButton>}
      </div>
      : <div className="flex items-center gap-3">
        <Button type="button" variant="outline" loading={p.writing} disabled={p.busy} onClick={p.onRewrite}><Sparkles className="h-4 w-4" />Generate caption</Button>
        <button type="button" onClick={() => setManual(true)} className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline">Write it myself</button>
      </div>}
    {p.goal === 'readmore' && <Textarea value={p.comment} onChange={event => p.onComment(event.target.value)} rows={3} maxLength={2000}
      aria-label="First comment" placeholder="First comment with the link to the full story" className="leading-relaxed" />}
  </section>;
}
