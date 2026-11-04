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
      ? 'Handles startup and plays from the beginning after Live is confirmed, within the two-minute broadcast limit.'
      : 'Playback settings apply after confirmation. The two-minute broadcast limit includes startup.'}</p>
    {mode === 'manual' && <>
      <Field label="Playback">
        <Select ariaLabel="Live playback" value={loop ? 'loop' : 'once'} disabled={disabled} onValueChange={value => {
          onLoop(value === 'loop'); if (value === 'loop' && !minutes) onMinutes('2');
        }} options={[{value: 'once', label: 'Play once'}, {value: 'loop', label: 'Loop video'}]} />
      </Field>
      <Field label={loop ? 'Stop after (minutes)' : 'Duration limit (minutes, optional)'} htmlFor="live-duration-limit">
        <Input id="live-duration-limit" aria-label="Live duration limit" type="number" min={1} max={2} placeholder="Up to 2 minutes" value={minutes} disabled={disabled}
          onChange={e => onMinutes(e.target.value)} />
      </Field>
      <p className="text-[11px] text-zinc-500">{loop ? 'Repeats until the duration limit or Stop Live.' : 'Ends with the video or duration limit.'} Maximum 2 minutes including startup.</p>
    </>}
    <Field label="Audio">
      <Select ariaLabel="Live audio" value={muted ? 'muted' : 'original'} disabled={disabled} onValueChange={value => onMuted(value === 'muted')}
        options={[{value: 'original', label: 'Original audio'}, {value: 'muted', label: 'Muted'}]} />
    </Field>
    <p className="text-[11px] text-zinc-500">Viewers may see startup footage before the video restarts from the beginning.</p>
  </Card>;
}
