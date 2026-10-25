import { useEffect, useState } from 'react';
import { Headphones, Music2, X } from 'lucide-react';
import type { AudioPreset, ReelComposition } from '../../types/reelCreator';
import { isDesktopApp } from '../../services/desktopEnvironment';
import { reelApi } from '../../services/reelCreator';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';

export function NewsAudio({ value, onChange, onUpload, onPick, uploading, customName }: {
  value: ReelComposition;
  onChange: (value: Partial<ReelComposition>) => void;
  onUpload: (file?: File) => void;
  onPick: () => void;
  uploading: boolean;
  customName: string;
}) {
  const [presets, setPresets] = useState<AudioPreset[]>([]);
  const [error, setError] = useState('');
  const [listening, setListening] = useState(false);
  const [audioUrl, setAudioUrl] = useState('');
  const selected = value.audio_id ? 'custom' : value.audio_preset || 'none';
  useEffect(() => {
    let alive = true;
    void reelApi.audioPresets().then(items => { if (alive) setPresets(items); })
      .catch(() => { if (alive) setError('News audio presets are unavailable. Update the app and try again.'); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { setListening(false); setError(''); }, [selected]);
  useEffect(() => {
    setAudioUrl('');
    if (!listening || selected === 'none' || selected === 'custom') return;
    let alive = true;
    let url = '';
    void reelApi.audioPreview(selected).then(blob => {
      if (!alive) return;
      url = URL.createObjectURL(blob); setAudioUrl(url);
    }).catch(() => { if (alive) setError('The audio preview could not load.'); });
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [selected, listening]);
  return <section className="space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5" aria-label="Reel soundtrack">
    <h2 className="flex items-center gap-2 text-xs font-semibold text-zinc-200"><Music2 className="h-4 w-4 text-blue-400" />News soundtrack</h2>
    <div className="flex gap-2"><div className="flex-1"><Select ariaLabel="News audio preset" value={selected} options={[
      { value: 'none', label: 'No music' }, ...presets.map(p => ({ value: p.id, label: p.name })),
      { value: 'custom', label: 'Your uploaded audio', disabled: !value.audio_id },
    ]} onValueChange={preset => { if (preset !== 'custom') onChange({ audio_preset: preset as ReelComposition['audio_preset'], audio_id: null }); }} /></div><Button variant="outline" aria-label="Listen to news audio preset" disabled={selected === 'none' || selected === 'custom'} onClick={() => setListening(old => !old)}><Headphones className="h-3.5 w-3.5" />Listen</Button></div>
    <p className="text-[10px] leading-relaxed text-zinc-500">{presets.find(p => p.id === selected)?.description || 'Choose a built-in news bed or import your own audio.'} Built-in tracks are original synthesized music, available offline.</p>
    {audioUrl && <audio controls src={audioUrl} className="h-9 w-full" aria-label="News soundtrack preview" />}
    <div className="flex items-center gap-2">{isDesktopApp() ? <Button variant="outline" className="min-w-0 flex-1 justify-start" aria-label="Upload audio" disabled={uploading} onClick={onPick}><Music2 className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{uploading ? 'Choosing / importing audio…' : value.audio_id ? customName || 'Custom audio selected' : 'Upload your own music'}</span></Button> : <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-[11px] text-zinc-500"><Music2 className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{uploading ? 'Importing audio…' : value.audio_id ? customName || 'Custom audio selected' : 'Upload your own music'}</span><input type="file" className="sr-only" accept="audio/mpeg,audio/wav,audio/mp4,audio/ogg,audio/flac" aria-label="Upload audio" disabled={uploading} onChange={e => { const file = e.currentTarget.files?.[0]; e.currentTarget.value = ''; if (file) onUpload(file); }} /></label>}{value.audio_id && <Button variant="ghost" size="icon" aria-label="Remove custom audio" onClick={() => onChange({ audio_id: null, audio_preset: 'none' })}><X className="h-4 w-4" /></Button>}</div>
    {selected !== 'none' && <label className="block text-[11px] text-zinc-400">Music volume · {Math.round(value.volume * 100)}%<input type="range" className="mt-2 w-full accent-blue-500" min="0" max="0.7" step="0.01" value={value.volume} onChange={e => onChange({ volume: Number(e.target.value) })} /></label>}
    {error && <p role="alert" className="text-xs text-amber-200">{error}</p>}
  </section>;
}
