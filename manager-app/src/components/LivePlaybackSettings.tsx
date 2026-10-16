import { Select } from './ui/Select';

export type LivePlaybackMode = 'auto' | 'manual';
interface Props {
  mode: LivePlaybackMode;
  onMode: (value: LivePlaybackMode) => void;
  loop: boolean;
  onLoop: (value: boolean) => void;
  minutes: string;
  onMinutes: (value: string) => void;
  muted: boolean;
  onMuted: (value: boolean) => void;
  disabled?: boolean;
}

export function LivePlaybackSettings({ mode, onMode, loop, onLoop, minutes, onMinutes, muted, onMuted, disabled }: Props) {
  return <section className="space-y-3 rounded-xl border border-rose-900/50 bg-zinc-900 p-3" aria-label="Live playback settings">
    <label className="block text-xs text-zinc-400">Playback mode
      <Select ariaLabel="Live playback mode" value={mode} disabled={disabled} onValueChange={value => onMode(value as LivePlaybackMode)}
        className="mt-1" options={[{value: 'auto', label: 'Automatic'}, {value: 'manual', label: 'Manual'}]} />
    </label>
    <p className="text-[11px] text-zinc-400">{mode === 'auto'
      ? 'Automatically handles startup, plays the full video once from the beginning after Live is confirmed, then ends the broadcast.'
      : 'Startup repeats until Live is confirmed. Your playback and duration settings apply after confirmation.'}</p>
    {mode === 'manual' && <>
      <label className="block text-xs text-zinc-400">Playback
        <Select ariaLabel="Live playback" value={loop ? 'loop' : 'once'} disabled={disabled} onValueChange={value => {
          onLoop(value === 'loop'); if (value === 'loop' && !minutes) onMinutes('60');
        }} className="mt-1" options={[{value: 'once', label: 'Play once'}, {value: 'loop', label: 'Loop video'}]} />
      </label>
      <label className="block text-xs text-zinc-400">{loop ? 'Stop after (minutes)' : 'Duration limit (minutes, optional)'}
        <input aria-label="Live duration limit" type="number" min={1} max={240} placeholder="Full video" value={minutes} disabled={disabled}
          onChange={e => onMinutes(e.target.value)} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-zinc-100" />
      </label>
      <p className="text-[11px] text-zinc-500">{loop ? 'Repeats until the duration limit or Stop Live.' : 'Ends with the video or duration limit.'} Maximum 4 hours after confirmation.</p>
    </>}
    <label className="block text-xs text-zinc-400">Audio
      <Select ariaLabel="Live audio" value={muted ? 'muted' : 'original'} disabled={disabled} onValueChange={value => onMuted(value === 'muted')}
        className="mt-1" options={[{value: 'original', label: 'Original audio'}, {value: 'muted', label: 'Muted'}]} />
    </label>
    <p className="text-[11px] text-zinc-500">Viewers may see startup footage before the video restarts from the beginning.</p>
  </section>;
}
