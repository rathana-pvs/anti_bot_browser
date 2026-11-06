import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, X } from 'lucide-react';
import { fetchProxyCheck, ProxyCheckRun, startProxyCheck, stopProxyCheck } from '../services/api';
import { GRADE_LABEL, GRADE_TONE, ProxyGrade, ProxyItem, proxyWarnings } from '../types/proxy';
import { cn } from './ui/cn';

const POLL_MS = 1000;

/** The backend's proxy check run: start, stop and follow it, refreshing the list as results land. */
export function useProxyCheckRun(onRefresh: () => void) {
  const [run, setRun] = useState<ProxyCheckRun>({ state: 'idle' });
  const [showSummary, setShowSummary] = useState(false);
  const lastDone = useRef(-1);
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;

  const poll = useCallback(async () => {
    try {
      const next = await fetchProxyCheck();
      setRun(next);
      if (next.done !== undefined && next.done !== lastDone.current) {
        lastDone.current = next.done;
        refresh.current();
      }
      return next;
    } catch {
      return null;
    }
  }, []);

  // Pick up a run that was already going, e.g. after switching pages.
  useEffect(() => { void poll(); }, [poll]);
  useEffect(() => {
    if (run.state !== 'running') return;
    const timer = window.setInterval(async () => {
      const next = await poll();
      if (next && next.state !== 'running') { setShowSummary(true); refresh.current(); }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [run.state, poll]);

  const start = async (ids?: string[], force = false) => {
    lastDone.current = -1;
    setShowSummary(false);
    setRun(await startProxyCheck(ids, force));
  };
  const stop = async () => setRun(await stopProxyCheck());
  return { run, running: run.state === 'running', start, stop, showSummary, dismiss: () => setShowSummary(false) };
}

const mbps = (value: number | null | undefined) => value == null ? '—' : `${Number(value.toFixed(1))}`;

/** Below this a working proxy stays in Available, but its speed shows in yellow. */
export const SLOW_MBPS = 1;

/** One direction's speed: yellow under 1 Mbps, red when nothing got through (a timeout). */
function Speed({ arrow, value, label }: { arrow: string; value: number | null | undefined; label: string }) {
  const tone = value == null ? 'text-red-300' : value < SLOW_MBPS ? 'text-amber-300' : 'text-zinc-300';
  const title = value == null ? `${label}: nothing got through (timed out)` : value < SLOW_MBPS ? `${label}: slow, under ${SLOW_MBPS} Mbps` : undefined;
  return <span className={tone} title={title}>{arrow} {mbps(value)}</span>;
}

/** Progress while a run goes, and what it found once it ends. */
export function CheckRunBar({ run, showSummary, onStop, onDismiss, onCheckSkipped }: {
  run: ProxyCheckRun; showSummary: boolean; onStop: () => void; onDismiss: () => void; onCheckSkipped: () => void;
}) {
  const baseline = run.baseline;
  if (run.state === 'running') {
    const total = run.total || 0;
    const percent = total ? Math.round(((run.done || 0) / total) * 100) : 0;
    return <div className="rounded-lg border border-blue-900/50 bg-blue-950/20 px-3 py-2 text-[11px] text-blue-200" aria-live="polite">
      <div className="flex flex-wrap items-center gap-3">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        <span>{`Checking ${run.done}/${total} · ${run.running_ids?.length || 0} running (up to ${run.at_once} at once)`}</span>
        {baseline && <span className="text-blue-300/70">Your internet: ↓{mbps(baseline.download_mbps)} ↑{mbps(baseline.upload_mbps)} Mbps</span>}
        <button type="button" onClick={onStop} className="ml-auto text-blue-300 hover:text-white">Stop after the running checks</button>
      </div>
      <div className="mt-2 h-1 overflow-hidden rounded bg-blue-950"><div className="h-full bg-blue-400 transition-all" style={{ width: `${percent}%` }} /></div>
    </div>;
  }
  if (!showSummary || run.state === 'idle') return null;
  const grades = run.grades || { good: 0, slow: 0, timeout: 0, dead: 0 };
  return <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-[11px] text-zinc-300" role="status">
    <span className="font-medium text-zinc-100">{run.state === 'stopped' ? 'Check stopped' : 'Check finished'} · {run.done} checked</span>
    {(Object.keys(GRADE_LABEL) as ProxyGrade[]).filter(g => grades[g]).map(g =>
      <span key={g} className={GRADE_TONE[g]}>{grades[g]} {GRADE_LABEL[g].toLowerCase()}</span>)}
    {baseline && <span className="text-zinc-500">
      Your internet ↓{mbps(baseline.download_mbps)} ↑{mbps(baseline.upload_mbps)} Mbps</span>}
    {!!run.skipped && <button type="button" onClick={onCheckSkipped} className="text-blue-300 hover:text-white">
      {run.skipped} that worked in the last 24 h were skipped · Check them too</button>}
    <button type="button" onClick={onDismiss} aria-label="Dismiss" className="ml-auto rounded p-0.5 text-zinc-500 hover:text-white"><X className="h-3.5 w-3.5" /></button>
  </div>;
}

/** The Quality column: working or not, its speeds (yellow under 1 Mbps), and anything to watch. */
export function ProxyQualityCell({ proxy, checking }: { proxy: ProxyItem; checking: boolean }) {
  if (checking) return <span className="inline-flex items-center gap-1.5 text-[11px] text-blue-300"><Loader2 className="h-3 w-3 animate-spin" />Checking…</span>;
  const { rating, check } = proxy;
  if (!rating || !check) {
    const legacy = proxy.speed_test;
    return <div className="text-[11px] text-zinc-500">
      <p>{legacy ? <><Speed arrow="↓" label="Download" value={legacy.download.success ? legacy.download.mbps : null} /> · <Speed arrow="↑" label="Upload" value={legacy.upload.success ? legacy.upload.mbps : null} /> Mbps</> : 'Not checked yet'}</p>
      <p className="mt-1 text-[10px]">{legacy ? 'Older speed test. Check again for full details.' : 'Run a check to test it.'}</p>
    </div>;
  }
  const warnings = proxyWarnings(proxy);
  const history = (check.history || []).map(h =>
    `${new Date(h.at).toLocaleString()}: ${GRADE_LABEL[h.grade]} · ↓${mbps(h.down)} ↑${mbps(h.up)} Mbps${h.facebook_ms != null ? ` · Facebook ${h.facebook_ms} ms` : ''}${h.exit_ip ? ` · ${h.exit_ip}` : ''}`);
  const title = [check.error, ...history.reverse()].filter(Boolean).join('\n');
  return <div className="min-w-0 text-[11px]" title={title}>
    <p className={cn('font-medium', GRADE_TONE[rating.grade])}>{GRADE_LABEL[rating.grade]}</p>
    {rating.grade === 'dead'
      ? <p className="mt-1 max-w-56 text-[10px] text-red-300/80">{check.error || 'No traffic got through'}</p>
      : <>
        <p className="mt-1 text-zinc-400"><Speed arrow="↓" label="Download" value={rating.download_mbps} /> · <Speed arrow="↑" label="Upload" value={rating.upload_mbps} /> Mbps{check.facebook_ms != null ? ` · FB ${check.facebook_ms} ms` : ''}</p>
      </>}
    {warnings.length > 0 && <p className="mt-1 flex items-center gap-1 text-[10px] text-amber-300"><AlertTriangle className="h-3 w-3" aria-hidden="true" />{warnings.join(' · ')}</p>}
    <p className="mt-1 text-[10px] text-zinc-600">{new Date(check.checked_at).toLocaleString()}</p>
  </div>;
}
