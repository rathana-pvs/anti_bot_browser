/** The reel's music: the recipe's track, a built-in one, or one of the operator's own, with a play button. */
import { useEffect, useRef, useState } from 'react';
import { Music, Pause, Play, Plus } from 'lucide-react';
import { compositionApi, type MyTrack } from '../../services/compositionApi';
import { reelApi } from '../../services/reelCreator';
import type { Composition, Sting } from '../../types/composition';
import type { MusicTrack } from '../../types/reelCreator';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';

const MINE = 'mine:';
const STING_MAX = 8;
const STING_DEFAULT = 3;
const NO_STING = 'none';
const STING_AT = [{ value: 'both', label: 'Before and after the voice' }, { value: 'start', label: 'Before the voice' }, { value: 'end', label: 'After the voice' }];
const ROLES = [{ value: 'bed', label: 'Plays under the whole reel' }, { value: 'intro', label: 'Plays once at the start' }];
const length = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
const message = (err: unknown) => err instanceof Error ? err.message : 'Something went wrong with the music.';

export function MusicPanel({ composition, busy, onMusic, onSting }: { composition: Composition; busy: boolean;
  onMusic: (music: string, musicId: string | null) => void; onSting: (sting: Sting | null) => void }) {
  const [builtIn, setBuiltIn] = useState<MusicTrack[]>([]);
  const [lookMusic, setLookMusic] = useState<Record<string, string>>({});
  const [mine, setMine] = useState<MyTrack[]>([]);
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState('');
  const [playing, setPlaying] = useState(false);
  const [link, setLink] = useState('');
  const [note, setNote] = useState('');
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    void reelApi.capabilities().then(c => { setBuiltIn(c.music); setLookMusic(Object.fromEntries(c.looks.map(l => [l.id, l.music]))); }).catch(() => undefined);
    void compositionApi.musicLibrary().then(setMine).catch(err => setProblem(message(err)));
  }, []);
  const value = composition.music === 'custom' && composition.music_id ? MINE + composition.music_id : composition.music;
  const own = composition.music === 'custom' ? mine.find(t => t.id === composition.music_id) : undefined;
  useEffect(() => { setLink(own?.source_url ?? ''); }, [own?.id, own?.source_url]);

  const recipeTrack = lookMusic[composition.recipe ?? 'clean'];
  const recipeName = builtIn.find(t => t.id === recipeTrack)?.name;
  const stop = () => { audio.current?.pause(); audio.current = null; setPlaying(false); };
  useEffect(() => stop, []);
  useEffect(stop, [value]);
  const play = async () => {
    if (playing) return stop();
    try {
      const blob = value.startsWith(MINE) ? await compositionApi.myTrackPreview(value.slice(MINE.length))
        : await reelApi.musicPreview(value === 'auto' ? recipeTrack ?? 'headlines' : value);
      const player = new Audio(URL.createObjectURL(blob));
      player.onended = stop;
      audio.current = player; setPlaying(true);
      await player.play();
    } catch { stop(); }
  };
  const add = async () => {
    setProblem(''); setNote('');
    try {
      const picked = await reelApi.pickAudios();
      if (!picked) return;
      setAdding(true);
      const added: MyTrack[] = [], failed = [...picked.errors];
      for (const asset of picked.assets) {  // each is trimmed and levelled; one that fails does not stop the rest
        try { added.push(await compositionApi.addMusic(asset.id)); }
        catch (err) { failed.push(`${asset.name}: ${message(err)}`); }
      }
      if (added.length) setMine(old => [...added.slice().reverse(), ...old.filter(t => !added.some(a => a.id === t.id))]);
      if (added.length === 1 && !failed.length) onMusic('custom', added[0].id);  // one track: use it right away
      setProblem(failed.length ? `Not added: ${failed.join('; ')}` : '');
      if (added.length > 1) setNote(`Added ${added.length} tracks to My music. Choose one above.`);
    } catch (err) { setProblem(message(err)); } finally { setAdding(false); }
  };
  const change = async (update: { source_url?: string; role?: MyTrack['role'] }) => {
    if (!own) return;
    try {
      const track = await compositionApi.updateMusic(own.id, update);
      setMine(old => old.map(t => t.id === track.id ? track : t)); setProblem('');
    } catch (err) { setProblem(message(err)); }
  };
  const remove = async () => {
    if (!own) return;
    try {
      await compositionApi.removeMusic(own.id);
      setMine(old => old.filter(t => t.id !== own.id));
      onMusic('auto', null);
    } catch (err) { setProblem(message(err)); }
  };

  const options = [
    { value: 'auto', label: recipeName ? `Match the recipe (${recipeName})` : 'Match the recipe' }, { value: 'off', label: 'No music' },
    ...mine.map(t => ({ value: MINE + t.id, label: `My music · ${t.name.replace(/\.[a-z0-9]+$/i, '')} (${length(t.seconds)}${t.role === 'intro' ? ', once' : ''})` })),
    ...builtIn.map(t => ({ value: t.id, label: `Built-in · ${t.name}` })),
    ...(composition.music === 'custom' && !own ? [{ value, label: 'A file added in Reel Studio' }] : []),
  ];
  const sting = composition.sting;
  const stingTrack = sting ? mine.find(t => t.id === sting.asset_id) : undefined;
  const stingOptions = [{ value: NO_STING, label: 'No intro sound' },
    ...mine.map(t => ({ value: t.id, label: `${t.name.replace(/\.[a-z0-9]+$/i, '')} (${length(t.seconds)})` })),
    ...(sting && !stingTrack ? [{ value: sting.asset_id, label: 'A sound no longer in My music' }] : [])];
  const longest = Math.min(STING_MAX, stingTrack?.seconds ?? STING_MAX);
  const stingLengths = [1, 2, 3, 4, 5, 6, 8].filter(n => n <= longest + .05).map(n => ({ value: String(n), label: `Plays ${n} s` }));
  if (sting && !stingLengths.some(o => Number(o.value) === sting.seconds)) stingLengths.push({ value: String(sting.seconds), label: `Plays ${sting.seconds.toFixed(1)} s` });
  return <section aria-label="Music" className="space-y-2 rounded-xl border border-zinc-800 bg-zinc-950/60 p-3">
    <div className="flex items-center gap-2"><Music className="h-3.5 w-3.5 text-sky-400" aria-hidden="true" /><h3 className="text-xs font-semibold text-zinc-100">Music</h3>
      <span className="ml-auto text-[10px] text-zinc-500">Quieter while the voice speaks</span></div>
    <div className="flex flex-wrap items-center gap-2">
      <div className="min-w-0 flex-1"><Select ariaLabel="Music" value={value} options={options} disabled={busy}
        onValueChange={v => v.startsWith(MINE) ? onMusic('custom', v.slice(MINE.length)) : onMusic(v, null)} /></div>
      <IconButton aria-label={playing ? 'Stop music' : 'Play music'} disabled={value === 'off'} onClick={() => { void play(); }}>
        {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</IconButton>
      <Button variant="outline" size="sm" loading={adding} disabled={busy || adding} onClick={() => { void add(); }}
        title="Add MP3, WAV, M4A, Ogg or FLAC files you downloaded; choose up to 10 at once. Each is brought to the same loudness as the built-in tracks."><Plus className="h-3.5 w-3.5" />Add tracks</Button>
    </div>
    {own && <div className="grid gap-2 sm:grid-cols-[200px_minmax(0,1fr)_auto]">
      <Select ariaLabel="How the track plays" value={own.role} options={ROLES} disabled={busy} onValueChange={role => { void change({ role: role as MyTrack['role'] }); }} />
      <Input value={link} maxLength={300} placeholder="Where it's from: paste the track's page link" aria-label="Track source link"
        onChange={event => setLink(event.target.value)} onBlur={() => { if (link !== own.source_url) void change({ source_url: link }); }} />
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => { void remove(); }}>Remove from My music</Button>
    </div>}
    <div className="flex flex-wrap items-center gap-2">
      <p className="w-full text-[10px] uppercase tracking-wide text-zinc-500">Intro sound</p>
      <div className="min-w-0 flex-1"><Select ariaLabel="Intro sound" value={sting?.asset_id ?? NO_STING} options={stingOptions} disabled={busy}
        onValueChange={id => { const t = mine.find(x => x.id === id);
          onSting(t ? { asset_id: t.id, seconds: Math.round(Math.min(t.seconds, sting?.seconds ?? STING_DEFAULT) * 10) / 10, at: sting?.at ?? 'both' } : null); }} /></div>
      {sting && <div className="w-28"><Select ariaLabel="How long the intro sound plays" value={String(sting.seconds)} options={stingLengths} disabled={busy}
        onValueChange={n => onSting({ ...sting, seconds: Number(n) })} /></div>}
      {sting && <div className="w-56"><Select ariaLabel="When the intro sound plays" value={sting.at} options={STING_AT} disabled={busy}
        onValueChange={at => onSting({ ...sting, at: at as Sting['at'] })} /></div>}
    </div>
    {!mine.length && <p className="text-[11px] text-zinc-500">My music is empty. Press Add tracks and pick a news sting or opener; any length works, the start of it is used.</p>}
    {sting && <p className="text-[11px] text-zinc-500">The start of the track plays and fades out. The voice starts after it, and the reel runs on until it ends.</p>}
    {own && <p className="text-[11px] text-zinc-500">Keep the link: if Facebook flags the music, it shows where you got it and its licence.</p>}
    {note && !problem && <p className="text-[11px] text-emerald-400" aria-live="polite">{note}</p>}
    {problem && <p role="alert" className="text-[11px] text-amber-300">{problem}</p>}
  </section>;
}
