import { useEffect, useState } from 'react';
import { Flame } from 'lucide-react';
import { Checkbox } from './ui/Checkbox';
import { fetchAutoWarming, fetchProfiles, updateAutoWarming } from '../services/api';
import { AutoWarmingSummary } from '../types/automation';

/** The global Auto warming switch, with what it did today. */
export function AutoWarmingControl() {
  const [summary, setSummary] = useState<AutoWarmingSummary | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => fetchAutoWarming().then(value => { if (!cancelled) { setSummary(value); setError(null); } })
      .catch(reason => { if (!cancelled) setError(reason.message || 'Couldn’t load auto warming'); });
    load();
    fetchProfiles().then(profiles => { if (!cancelled) setNames(Object.fromEntries(profiles.map(p => [p.id, p.name]))); }).catch(() => undefined);
    const timer = setInterval(load, 15000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  const toggle = async (enabled: boolean) => {
    setSaving(true);
    setError(null);
    try { setSummary(await updateAutoWarming(enabled)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Couldn’t update auto warming'); }
    finally { setSaving(false); }
  };

  const name = (id: string) => names[id] || id;
  const last = summary?.last_choice;
  return <div className="space-y-2 text-xs">
    <label htmlFor="auto-warming" className="flex cursor-pointer items-start gap-2.5">
      <Checkbox id="auto-warming" checked={Boolean(summary?.enabled)} disabled={!summary || saving} onCheckedChange={toggle} />
      <span>
        <span className="flex items-center gap-1.5 font-medium text-zinc-200"><Flame className="h-3.5 w-3.5 text-amber-400" />Auto warming</span>
        <span className="mt-1 block leading-5 text-zinc-400">
          Short read-only visits that keep accounts active: feed, notifications, Reels and the Page itself. They use spare
          capacity only, skip accounts that need attention, and always give way to posts.
        </span>
      </span>
    </label>
    {summary?.enabled && <p className="pl-6 text-[11px] text-zinc-500">
      {summary.visits_today} visit{summary.visits_today === 1 ? '' : 's'} today
      {summary.running.length > 0 && ` · now: ${summary.running.map(name).join(', ')}`}
      {last && ` · last chosen: ${name(last.profile_id)} (${last.reason})`}
    </p>}
    {error && <p role="alert" className="pl-6 text-red-400">{error}</p>}
  </div>;
}
