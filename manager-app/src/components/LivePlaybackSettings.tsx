import { Select } from './ui/Select';
import { Card } from './ui/Card';
import { Field } from './ui/Field';
import { Input } from './ui/Input';

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
  return <Card className="space-y-3 border-rose-900/50" aria-label="Live playback settings">
    <Field label="Playback mode">
      <Select ariaLabel="Live playback mode" value={mode} disabled={disabled} onValueChange={value => onMode(value as LivePlaybackMode)}
        options={[{value: 'auto', label: 'Automatic'}, {value: 'manual', label: 'Manual'}]} />
    </Field>
    <p className="text-[11px] text-zinc-400">{mode === 'auto'
      ? 'Automatically handles startup, plays the full video once from the beginning after Live is confirmed, then ends the broadcast.'
      : 'Startup repeats until Live is confirmed. Your playback and duration settings apply after confirmation.'}</p>
    {mode === 'manual' && <>
      <Field label="Playback">
        <Select ariaLabel="Live playback" value={loop ? 'loop' : 'once'} disabled={disabled} onValueChange={value => {
          onLoop(value === 'loop'); if (value === 'loop' && !minutes) onMinutes('60');
        }} options={[{value: 'once', label: 'Play once'}, {value: 'loop', label: 'Loop video'}]} />
      </Field>
      <Field label={loop ? 'Stop after (minutes)' : 'Duration limit (minutes, optional)'} htmlFor="live-duration-limit">
        <Input id="live-duration-limit" aria-label="Live duration limit" type="number" min={1} max={240} placeholder="Full video" value={minutes} disabled={disabled}
          onChange={e => onMinutes(e.target.value)} />
      </Field>
      <p className="text-[11px] text-zinc-500">{loop ? 'Repeats until the duration limit or Stop Live.' : 'Ends with the video or duration limit.'} Maximum 4 hours after confirmation.</p>
    </>}
    <Field label="Audio">
      <Select ariaLabel="Live audio" value={muted ? 'muted' : 'original'} disabled={disabled} onValueChange={value => onMuted(value === 'muted')}
        options={[{value: 'original', label: 'Original audio'}, {value: 'muted', label: 'Muted'}]} />
    </Field>
    <p className="text-[11px] text-zinc-500">Viewers may see startup footage before the video restarts from the beginning.</p>
  </Card>;
}
