import { useEffect, useState } from 'react';
import { CalendarClock } from 'lucide-react';
import { fetchPostingPlan, PostingPlanSettings, updatePostingPlan } from '../services/api';
import { Select } from './ui/Select';

const hours = (values: number[]) => values.map(v => ({ value: String(v), label: `${v} h` }));

/** Spacing for batches scheduled with Follow active hours. Start now and exact times ignore it. */
export function PostingPlanControl() {
  const [plan, setPlan] = useState<PostingPlanSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { fetchPostingPlan().then(setPlan).catch(err => setError(err.message || String(err))); }, []);

  const save = async (changes: Partial<PostingPlanSettings>) => {
    if (!plan) return;
    const next = { ...plan, ...changes };
    // Keep the range valid while one end moves.
    if (next.max_gap_hours < next.min_gap_hours) {
      if ('min_gap_hours' in changes) next.max_gap_hours = next.min_gap_hours;
      else next.min_gap_hours = next.max_gap_hours;
    }
    setSaving(true);
    setError(null);
    try { setPlan(await updatePostingPlan(next)); }
    catch (err: any) { setError(err.message || String(err)); }
    finally { setSaving(false); }
  };

  return <div className="space-y-3 text-xs">
    <div>
      <p className="flex items-center gap-1.5 font-medium text-zinc-200"><CalendarClock className="h-3.5 w-3.5 text-sky-400" />Planned posting</p>
      <p className="mt-1 leading-5 text-zinc-400">
        For batches scheduled with Follow active hours, the app picks each account’s time in its own day, a random gap apart from its other posts, and up to a daily limit. Start now and an exact scheduled time ignore these.
      </p>
    </div>
    {plan && <div className="grid grid-cols-3 gap-2">
      <label className="space-y-1"><span className="block text-zinc-400">Shortest gap</span>
        <Select value={String(plan.min_gap_hours)} disabled={saving} ariaLabel="Shortest gap between an account's posts"
          options={hours([1, 1.5, 2, 3, 4, 6])} onValueChange={v => void save({ min_gap_hours: Number(v) })} /></label>
      <label className="space-y-1"><span className="block text-zinc-400">Longest gap</span>
        <Select value={String(plan.max_gap_hours)} disabled={saving} ariaLabel="Longest gap between an account's posts"
          options={hours([2, 3, 4, 5, 6, 8])} onValueChange={v => void save({ max_gap_hours: Number(v) })} /></label>
      <label className="space-y-1"><span className="block text-zinc-400">Posts a day</span>
        <Select value={String(plan.max_posts_per_day)} disabled={saving} ariaLabel="Most posts per account per day"
          options={Array.from({ length: 10 }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }))}
          onValueChange={v => void save({ max_posts_per_day: Number(v) })} /></label>
    </div>}
    {error && <p role="alert" className="text-red-400">{error}</p>}
  </div>;
}
