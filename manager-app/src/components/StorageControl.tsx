import { useEffect, useState } from 'react';
import { HardDrive, Loader2 } from 'lucide-react';
import { Button } from './ui/Button';
import { cleanStorage, fetchStorage, StorageRun, StorageStatus } from '../services/api';

const formatBytes = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;

const CATEGORIES: { key: keyof StorageRun['usage']; label: string; hint: string }[] = [
  { key: 'chrome_cache', label: 'Browser caches', hint: 'Rebuilt by Chrome. Trimmed while a browser is stopped.' },
  { key: 'evidence', label: 'Evidence screenshots', hint: 'Kept by what they prove. Unconfirmed posts and open problems are never removed.' },
  { key: 'runtime_backups', label: 'Old runtime backup', hint: 'Removed once the new runtime is installed.' },
  { key: 'browser_data', label: 'Logins and browser data', hint: 'Never touched.' },
  { key: 'runtime', label: 'App runtime', hint: 'Needed to run automation.' },
  { key: 'media', label: 'Media and Reel Studio', hint: 'Your files. Never touched.' },
  { key: 'queue', label: 'Queue and archive', hint: 'Finished batches older than 30 days move to a monthly archive.' },
];

/** Settings → Storage: what uses space, and housekeeping that keeps it from growing. */
export function StorageControl() {
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [preview, setPreview] = useState<StorageRun | null>(null);
  const [busy, setBusy] = useState<'preview' | 'clean' | null>(null);
  const [error, setError] = useState('');

  const load = () => fetchStorage().then(setStatus).catch(reason => setError(reason.message || 'Couldn’t load storage'));
  useEffect(() => { void load(); }, []);

  const act = async (dryRun: boolean) => {
    setBusy(dryRun ? 'preview' : 'clean');
    setError('');
    try {
      const run = await cleanStorage(dryRun);
      if (dryRun) setPreview(run); else { setPreview(null); await load(); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Couldn’t clean storage'); }
    finally { setBusy(null); }
  };

  const last = status?.last_run;
  const usage = preview?.usage || last?.usage;
  const free = status?.free_bytes || 0;
  const tone = !status ? 'text-zinc-400' : free < status.critical_disk_bytes ? 'text-red-300' : free < status.low_disk_bytes ? 'text-amber-300' : 'text-emerald-300';
  return <div className="space-y-4 text-xs">
    <div className="flex items-center gap-2">
      <HardDrive className="h-4 w-4 text-zinc-400" />
      <span className="text-zinc-300">Free disk space: <span className={tone}>{status ? formatBytes(free) : '…'}</span></span>
    </div>
    {status && free < status.critical_disk_bytes && <p role="alert" className="text-red-300">The disk is nearly full, so new queue browsers wait until there is space again.</p>}
    <p className="leading-5 text-zinc-400">
      Housekeeping runs once a day while the computer is calm, and sooner when free space drops below {formatBytes(status?.low_disk_bytes || 0)}.
      It removes only what can be rebuilt or is no longer needed.
    </p>
    {usage && <dl className="divide-y divide-zinc-800/70 rounded-xl border border-zinc-800">
      {CATEGORIES.map(category => <div key={category.key} className="flex items-baseline justify-between gap-3 px-3 py-2" title={category.hint}>
        <dt className="min-w-0"><span className="text-zinc-200">{category.label}</span><span className="block truncate text-[11px] text-zinc-500">{category.hint}</span></dt>
        <dd className="shrink-0 tabular-nums text-zinc-300">{formatBytes(usage[category.key] || 0)}</dd>
      </div>)}
    </dl>}
    {preview && <p className="text-amber-200">Clean now would free about {formatBytes(preview.freed_bytes)}. Nothing has been removed yet.</p>}
    {!preview && last && <p className="text-zinc-500">Last cleaned {new Date(last.ran_at).toLocaleString()}: freed {formatBytes(last.freed_bytes)}.</p>}
    {!last && !preview && <p className="text-zinc-500">Not cleaned yet. Preview shows what would be freed.</p>}
    <div className="flex flex-wrap gap-2">
      <Button size="sm" variant="outline" onClick={() => act(true)} disabled={busy !== null}>
        {busy === 'preview' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Preview
      </Button>
      <Button size="sm" onClick={() => act(false)} disabled={busy !== null}>
        {busy === 'clean' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Clean now
      </Button>
    </div>
    {error && <p role="alert" className="text-red-400">{error}</p>}
  </div>;
}
