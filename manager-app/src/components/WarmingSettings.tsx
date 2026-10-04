import { ChevronDown, SlidersHorizontal } from 'lucide-react';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';
import { NumberInput } from './ui/NumberInput';
import { WarmingOptions } from '../types/automation';

export const defaultWarmingOptions: WarmingOptions = {
  random_scrolls: true, min_scrolls: 3, max_scrolls: 8,
  pace: 'balanced', surface: 'random', reread: true, long_breaks: true,
  cursor_movement: true, return_to_top: true, max_seconds: 180,
};

interface Props {
  scrolls: number;
  onScrollsChange: (value: number) => void;
  value: WarmingOptions;
  onChange: (value: WarmingOptions) => void;
}

export function WarmingSettings({ scrolls, onScrollsChange, value, onChange }: Props) {
  const update = (patch: Partial<WarmingOptions>) => onChange({ ...value, ...patch });
  return (
    <details className="group text-xs text-zinc-300">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md p-1 outline-none transition-colors hover:bg-zinc-900 focus-visible:ring-2 focus-visible:ring-blue-500 [&::-webkit-details-marker]:hidden">
        <SlidersHorizontal className="h-4 w-4 shrink-0 text-amber-400" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <span className="block font-medium text-zinc-200">Warming settings</span>
          <span className="mt-1 block text-[11px] leading-relaxed text-zinc-500">
            {value.random_scrolls ? `${value.min_scrolls}–${value.max_scrolls} random cycles` : `${scrolls} cycles`}
            {' · '}{value.pace.charAt(0).toUpperCase() + value.pace.slice(1)}
            {' · '}{value.surface === 'random' ? 'Random surface' : value.surface === 'profile' ? 'Own profile' : 'News feed'}
            {' · '}{value.max_seconds}s limit
          </span>
        </div>
        <ChevronDown className="h-4 w-4 shrink-0 text-zinc-500 transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="mt-3 space-y-3 border-t border-zinc-800 pt-3">
      <label className="flex items-center gap-2 cursor-pointer">
        <Checkbox ariaLabel="Random scroll count for each session" checked={value.random_scrolls} onCheckedChange={(checked) => update({ random_scrolls: checked })} />
        Random scroll count for each session
      </label>
      {value.random_scrolls ? (
        <div className="grid grid-cols-2 gap-3">
          <NumberInput label="Minimum cycles" min={1} max={value.max_scrolls} value={value.min_scrolls}
            onValueChange={(min_scrolls) => update({ min_scrolls })} />
          <NumberInput label="Maximum cycles" min={value.min_scrolls} max={30} value={value.max_scrolls}
            onValueChange={(max_scrolls) => update({ max_scrolls })} />
        </div>
      ) : (
        <label className="block">Scroll depth: <span className="text-amber-400">{scrolls} cycles</span>
          <input aria-label="Scroll depth" type="range" min={1} max={30} value={scrolls} onChange={(e) => onScrollsChange(Number(e.target.value))}
            className="mt-2 w-full accent-amber-500" />
        </label>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <span className="block mb-1">Reading pace</span>
          <Select ariaLabel="Reading pace" value={value.pace}
            onValueChange={(pace) => update({ pace: pace as WarmingOptions['pace'] })}
            options={[
              { value: 'quick', label: 'Quick · 1–3s' },
              { value: 'balanced', label: 'Balanced · 2.5–5.5s' },
              { value: 'relaxed', label: 'Relaxed · 5–10s' },
            ]} />
        </div>
        <div>
          <span className="block mb-1">Browse</span>
          <Select ariaLabel="Browse" value={value.surface}
            onValueChange={(surface) => update({ surface: surface as WarmingOptions['surface'] })}
            options={[
              { value: 'random', label: 'Random' },
              { value: 'news_feed', label: 'News feed' },
              { value: 'profile', label: 'Own profile' },
            ]} />
        </div>
      </div>
      {([
        ['reread', 'Occasionally scroll back to reread'],
        ['long_breaks', 'Occasional longer pauses (8–15s)'],
        ['cursor_movement', 'Vary cursor movement while browsing'],
        ['return_to_top', 'Finish with an upward scroll'],
      ] as const).map(([key, label]) => (
        <label key={key} className="flex items-center gap-2 cursor-pointer">
          <Checkbox ariaLabel={label} checked={value[key]} onCheckedChange={(checked) => update({ [key]: checked })} />{label}
        </label>
      ))}
      <NumberInput label="Browsing time limit (seconds)" min={15} max={900} step={15} value={value.max_seconds}
        onValueChange={(max_seconds) => update({ max_seconds })} />
      <p className="text-[11px] text-zinc-500 leading-relaxed">Each profile samples its own pauses and scroll distances. Browsing ends at the selected depth or time limit; page loading and verification add time. Unavailable surfaces fall back once. Passive browsing only.</p>
      </div>
    </details>
  );
}
