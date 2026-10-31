import { useState } from 'react';
import { ChevronDown, ChevronRight, ExternalLink, Sparkles } from 'lucide-react';
import { Profile } from '../../types/profile';
import { QueueExecutionItem } from '../../types/automation';
import { cleanProfileEvidence } from '../../services/api';
import { useStorageCleaning } from '../StorageControl';
import { Button } from '../ui/Button';
import { cn } from '../ui/cn';
import { EvidenceViewer } from '../queue/EvidenceViewer';
import { describe, formatTime, kindLabel, statusLabel, FAILED_STATUSES, UNCERTAIN_STATUSES } from '../queue/queueModel';

const PAGE = 30;

/** This account's job history, with the screenshots each run saved. */
export function AccountActivityTab({ profile, recent, onOpenImage, onToast }: {
  profile: Profile; recent: QueueExecutionItem[];
  onOpenImage: (url: string, label: string) => void; onToast: (message: string, tone: 'success' | 'error') => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [cleaning, setCleaning] = useState(false);
  const storageCleaning = useStorageCleaning();
  const finished = recent.filter(item => !['pending', 'ready'].includes(item.status));

  const clean = async () => {
    setCleaning(true);
    try {
      const result = await cleanProfileEvidence(profile.id, 3);
      onToast(result.deleted_runs > 0
        ? `Freed ${result.freed_formatted} from ${result.deleted_runs} old runs. Kept ${result.kept_runs}.`
        : `Nothing to clean. All ${result.kept_runs} runs are from the last 3 days.`, 'success');
    } catch (error) {
      onToast(error instanceof Error ? error.message : 'Couldn’t clean evidence.', 'error');
    } finally { setCleaning(false); }
  };

  return <div className="mx-auto max-w-5xl space-y-4 text-xs">
    <section className="overflow-hidden rounded-xl border border-zinc-800">
      <h3 className="border-b border-zinc-800 bg-zinc-950/60 px-4 py-2.5 text-sm font-semibold text-zinc-100">Recent jobs</h3>
      {!finished.length && <p className="px-4 py-8 text-center text-zinc-500">No finished jobs for this account yet.</p>}
      <ul className="divide-y divide-zinc-800/70">
        {finished.slice(0, limit).map(item => {
          const open = expanded === item.execution_id;
          const tone = FAILED_STATUSES.has(item.status) ? 'text-red-300' : UNCERTAIN_STATUSES.has(item.status) ? 'text-amber-300'
            : item.status === 'published' || item.status === 'completed' ? 'text-emerald-300' : 'text-zinc-400';
          return <li key={item.execution_id}>
            <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : item.execution_id)}
              className="grid w-full grid-cols-[1rem_6.5rem_6rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 text-left hover:bg-zinc-900/60">
              {open ? <ChevronDown className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronRight className="h-3.5 w-3.5 text-zinc-500" />}
              <span className="font-mono text-zinc-400">{formatTime(item.published_at || item.scheduled_at)}</span>
              <span className="text-zinc-200">{kindLabel(item)}</span>
              <span className="truncate text-zinc-500">{describe(item)}</span>
              <span className={tone}>{statusLabel(item.status)}</span>
            </button>
            {open && <div className="grid gap-4 px-4 pb-4 pl-11 md:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
              <EvidenceViewer item={item} onOpen={onOpenImage} fallback={<p className="text-zinc-500">No screenshots were saved for this run.</p>} />
              <div className="space-y-2 text-zinc-400">
                {item.batch_name && <p>Batch: <span className="text-zinc-200">{item.batch_name}</span></p>}
                {item.error && <p className="break-words text-red-300">{item.error}</p>}
                {item.post_url && <a href={item.post_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-blue-300 hover:text-blue-200"><ExternalLink className="h-3.5 w-3.5" />View on Facebook</a>}
              </div>
            </div>}
          </li>;
        })}
      </ul>
      {finished.length > limit && <button type="button" onClick={() => setLimit(limit + PAGE)} className="w-full border-t border-zinc-800 py-2 text-zinc-400 hover:bg-zinc-900/60">Show more · {finished.length - limit} hidden</button>}
    </section>

    <section className={cn('flex flex-wrap items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-950/40 p-4')}>
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-zinc-100">Storage</h3>
        <p className="mt-0.5 text-zinc-500">{profile.disk_usage ? `${profile.disk_usage} used. ` : ''}Screenshots older than 3 days can be removed; recent ones are kept.</p>
      </div>
      <Button size="sm" variant="outline" loading={cleaning} disabled={storageCleaning}
        title={storageCleaning ? 'Storage cleanup is running. This waits until it finishes.' : undefined} onClick={clean}><Sparkles className="h-3.5 w-3.5" />Clean old evidence</Button>
    </section>
  </div>;
}
