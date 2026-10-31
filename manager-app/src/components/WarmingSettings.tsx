import { Clock, Compass } from 'lucide-react';
import { WarmingOptions } from '../types/automation';
import { cn } from './ui/cn';

/** What a warm-up does. Pace, scroll size and pauses come from each account's own habits. */
export type WarmupActivity = 'visit' | 'feed' | 'page';
export type WarmupLength = 'short' | 'normal' | 'long';

const LENGTH_SECONDS: Record<WarmupLength, number> = { short: 120, normal: 240, long: 600 };

const ACTIVITIES: { value: WarmupActivity; label: string; hint: string }[] = [
  { value: 'visit', label: 'Like a real visit', hint: 'Feed, the Page itself, Reels and notifications, mixed the way this account usually does' },
  { value: 'feed', label: 'News feed only', hint: 'Scroll and read the home feed' },
  { value: 'page', label: 'Own Page only', hint: 'Look through the account’s own Page' },
];

const LENGTHS: { value: WarmupLength; label: string; hint: string }[] = [
  { value: 'short', label: 'Short', hint: 'About 2 minutes' },
  { value: 'normal', label: 'Normal', hint: 'About 4 minutes' },
  { value: 'long', label: 'Long', hint: 'About 10 minutes' },
];

export function warmupOptions(activity: WarmupActivity, length: WarmupLength): WarmingOptions {
  return {
    // Time decides when browsing ends; the cycle range only keeps it varied.
    random_scrolls: true, min_scrolls: 3, max_scrolls: 30,
    pace: 'balanced', reread: true, long_breaks: true, cursor_movement: true, return_to_top: true,
    surface: activity === 'feed' ? 'news_feed' : activity === 'page' ? 'profile' : 'random',
    auto: activity === 'visit',
    max_seconds: LENGTH_SECONDS[length],
  };
}

export const defaultWarmingOptions = warmupOptions('visit', 'normal');

/** Reads the two choices back from saved options, including ones saved by older versions. */
export function warmupChoice(value: WarmingOptions): { activity: WarmupActivity; length: WarmupLength } {
  const activity: WarmupActivity = value.auto ? 'visit' : value.surface === 'news_feed' ? 'feed' : value.surface === 'profile' ? 'page' : 'visit';
  const length = (Object.keys(LENGTH_SECONDS) as WarmupLength[])
    .reduce((best, key) => Math.abs(LENGTH_SECONDS[key] - value.max_seconds) < Math.abs(LENGTH_SECONDS[best] - value.max_seconds) ? key : best, 'normal');
  return { activity, length };
}

function Choice<T extends string>({ label, icon, options, value, onChange }: {
  label: string; icon: React.ReactNode; options: { value: T; label: string; hint: string }[]; value: T; onChange: (value: T) => void;
}) {
  return <fieldset className="space-y-1.5">
    <legend className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">{icon}{label}</legend>
    <div role="radiogroup" aria-label={label} className="grid gap-1.5 sm:grid-cols-3">
      {options.map(option => <button key={option.value} type="button" role="radio" aria-checked={value === option.value}
        onClick={() => onChange(option.value)} title={option.hint}
        className={cn('rounded-lg border px-3 py-2 text-left transition-colors',
          value === option.value ? 'border-amber-600/70 bg-amber-950/30 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200')}>
        <span className="block text-xs font-medium">{option.label}</span>
        <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500">{option.hint}</span>
      </button>)}
    </div>
  </fieldset>;
}

export function WarmingSettings({ value, onChange }: { value: WarmingOptions; onChange: (value: WarmingOptions) => void }) {
  const { activity, length } = warmupChoice(value);
  return <div className="space-y-4 text-xs">
    <Choice label="What to do" icon={<Compass className="h-3.5 w-3.5" />} options={ACTIVITIES} value={activity}
      onChange={(next) => onChange(warmupOptions(next, length))} />
    <Choice label="How long" icon={<Clock className="h-3.5 w-3.5" />} options={LENGTHS} value={length}
      onChange={(next) => onChange(warmupOptions(activity, next))} />
    <p className="text-[11px] leading-relaxed text-zinc-500">
      Read-only: nothing is liked, commented, shared or posted. Each account reads, scrolls and pauses at its own pace, so no two visits look the same.
    </p>
  </div>;
}
