import { useEffect, useState } from 'react';
import { Flame } from 'lucide-react';
import { fetchAutoWarming, fetchProfiles } from '../services/api';
import { AutoWarmingSummary } from '../types/automation';
import { setVisibleInterval } from '../services/visibleInterval';

/** Auto warming at a glance. It is switched on or off for each device on the Devices page. */
export function AutoWarmingControl() {
  const [summary, setSummary] = useState<AutoWarmingSummary | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => fetchAutoWarming().then(value => { if (!cancelled) { setSummary(value); setError(null); } })
      .catch(reason => { if (!cancelled) setError(reason.message || 'Couldn’t load auto warming'); });
    load();
    fetchProfiles().then(profiles => { if (!cancelled) setNames(Object.fromEntries(profiles.map(p => [p.id, p.name]))); }).catch(() => undefined);
    const stop = setVisibleInterval(load, 15000);
    return () => { cancelled = true; stop(); };
  }, []);

  const name = (id: string) => names[id] || id;
  const devices = summary?.devices ?? [];
  const on = devices.filter(device => device.enabled);
  const last = summary?.last_choice;
  return <div className="space-y-2 text-xs">
    <p className="flex items-center gap-1.5 font-medium text-zinc-200"><Flame className="h-3.5 w-3.5 text-amber-400" />Auto warming</p>
    <p className="leading-5 text-zinc-400">
      Short read-only visits that keep accounts active. Turn it on or off for each device on the Devices page;
      it then picks which account and when, uses spare capacity only and always gives way to posts.
    </p>
    {summary && <p className="text-[11px] text-zinc-500">
      {devices.length === 0 ? 'No devices yet.' : `On for ${on.length} of ${devices.length} device${devices.length === 1 ? '' : 's'}${on.length ? `: ${on.map(device => device.label).join(', ')}` : ''}`}
      {` · ${summary.visits_today} visit${summary.visits_today === 1 ? '' : 's'} today`}
      {summary.running.length > 0 && ` · now: ${summary.running.map(name).join(', ')}`}
      {last && ` · last chosen: ${name(last.profile_id)} (${last.reason})`}
    </p>}
    {error && <p role="alert" className="text-red-400">{error}</p>}
  </div>;
}
