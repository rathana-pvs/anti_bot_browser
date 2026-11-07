import React, { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, Search, Shuffle, X } from 'lucide-react';
import type { Profile } from '../../types/profile';
import { changeAccountProxy, fetchProxyChangeOptions, ProxyChangeOptions, ProxyChoice } from '../../services/api';

const place = (p: ProxyChoice) => p.geo_source === 'unknown' ? 'Location unknown'
  : [p.city, p.country_code || p.country].filter(Boolean).join(', ') || 'Location unknown';

const restLabel = (hours?: number) => !hours ? 'No rest needed'
  : hours < 1 ? `Rests about ${Math.round(hours * 60)} min` : `Rests about ${Number(hours.toFixed(1))} h`;

/** Change: move one account to another proxy already in the pool. Never while it runs. */
export const ChangeProxyDialog: React.FC<{ profile: Profile; onClose: () => void; onChanged: (message: string) => void }> = ({ profile, onClose, onChanged }) => {
  const [data, setData] = useState<ProxyChangeOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    fetchProxyChangeOptions(profile.id)
      .then(next => { if (!live) return; setData(next); setPicked(next.options.find(o => o.suggested)?.id ?? null); })
      .catch(err => live && setError(err.message || String(err)));
    return () => { live = false; };
  }, [profile.id]);

  const query = search.trim().toLowerCase();
  const shown = (data?.options || []).filter(o => !query || [o.host, String(o.port), o.city, o.country, o.country_code, o.region]
    .filter(Boolean).join(' ').toLowerCase().includes(query));
  const choice = data?.options.find(o => o.id === picked);
  const blocked = data?.running_reason;

  const submit = async () => {
    if (!choice || blocked) return;
    setSaving(true);
    setError(null);
    try {
      await changeAccountProxy(profile.id, choice.id);
      onChanged(`${profile.name} now uses ${choice.host}:${choice.port}. ${restLabel(choice.rest_hours)}.`);
      onClose();
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  };

  const current = data?.current;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div role="dialog" aria-modal="true" aria-label={`Change proxy for ${profile.name}`}
        className="flex max-h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100"><Shuffle className="h-4 w-4 text-zinc-400" />Change proxy · {profile.name}</h2>
          <button onClick={onClose} disabled={saving} aria-label="Close" className="rounded p-1 text-zinc-400 hover:text-zinc-200"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 p-5 text-xs">
          {!data && !error && <p className="flex items-center gap-2 text-zinc-400"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading proxies…</p>}
          {data && <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3">
            <p className="text-[10px] uppercase tracking-wide text-zinc-500">Now</p>
            {current ? <p className="mt-1 flex flex-wrap items-center gap-2">
              <span className="font-mono text-zinc-200">{current.host}:{current.port}</span>
              <span className="text-zinc-400">{place(current)}</span>
              {current.replaced_by && <span className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
                Replaced{current.replaced_by_address ? ` → ${current.replaced_by_address}` : ''}</span>}
            </p> : <p className="mt-1 text-zinc-400">{data.current_network.host ? `${data.current_network.host}:${data.current_network.port} (not from the pool)` : 'No proxy'}</p>}
          </div>}
          {blocked && <p role="alert" className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{blocked}</p>}
          {data && <>
            <div className="relative">
              <Search aria-hidden="true" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
              <input value={search} onChange={e => setSearch(e.target.value)} aria-label="Search proxies" placeholder="Search address or location"
                className="w-full rounded-lg border border-zinc-800 bg-zinc-950 py-2 pl-8 pr-3 text-zinc-200 outline-none focus:border-blue-500" />
            </div>
            <div role="radiogroup" aria-label="Proxies" className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800">
              {shown.length === 0 && <p className="p-4 text-center text-zinc-500">{data.options.length ? 'No proxy matches your search.' : 'No proxy has room. Import or replace proxies first.'}</p>}
              {shown.map(o => (
                <button key={o.id} role="radio" aria-checked={picked === o.id} disabled={!!blocked || saving} onClick={() => setPicked(o.id)}
                  className={`flex w-full items-center gap-3 border-b border-zinc-800/70 px-3 py-2 text-left last:border-b-0 disabled:opacity-50 ${picked === o.id ? 'bg-blue-500/10' : 'hover:bg-zinc-800/40'}`}>
                  <span className={`h-3 w-3 shrink-0 rounded-full border ${picked === o.id ? 'border-blue-400 bg-blue-400' : 'border-zinc-600'}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-zinc-200">{o.host}:{o.port}</span>
                      {o.suggested && <span className="rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">Webshare replacement</span>}
                      {o.accounts > 0 && <span className="text-[10px] text-zinc-500">{o.accounts} account(s) on it</span>}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-zinc-500">{place(o)}{o.latency_ms != null ? ` · ${o.latency_ms} ms` : ''}{o.rating ? ` · ${o.rating.grade}` : ''}</span>
                  </span>
                  <span className={`shrink-0 text-[11px] ${o.rest_hours ? 'text-amber-300' : 'text-emerald-300'}`}>{restLabel(o.rest_hours)}</span>
                </button>
              ))}
            </div>
          </>}
          {choice && !blocked && <p className="text-zinc-400">
            {profile.name} moves to {choice.host}:{choice.port} ({place(choice)}). {restLabel(choice.rest_hours)}: nothing automatic runs until then.
            {data?.phone ? ' The phone tries the new proxy first and keeps it only if traffic gets through.' : ''}
          </p>}
          {error && <p role="alert" className="text-red-400">{error}</p>}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3 text-xs">
          <button onClick={onClose} disabled={saving} className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800">Cancel</button>
          <button onClick={() => void submit()} disabled={!choice || !!blocked || saving}
            className="flex items-center gap-1.5 rounded-md bg-white px-4 py-1.5 font-semibold text-zinc-950 hover:bg-zinc-200 disabled:opacity-50">
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{saving ? 'Changing…' : 'Change proxy'}
          </button>
        </div>
      </div>
    </div>
  );
};
