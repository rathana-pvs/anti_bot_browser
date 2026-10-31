import { useState } from 'react';
import {
  AlertTriangle, Bug, Check, ExternalLink, Eye, Link2, Lock, MessageCircle, Play, RotateCcw, Square, Trash2, X,
} from 'lucide-react';
import { QueueExecutionItem } from '../../types/automation';
import { Profile } from '../../types/profile';
import { NetworkRecoveryStatus } from '../NetworkRecoveryStatus';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { QueueThumb } from './QueueRow';
import { EvidenceViewer } from './EvidenceViewer';
import {
  ACTIVE_STATUSES, FAILED_STATUSES, UNCERTAIN_STATUSES, commentNeedsRetry, describe, formatTime,
  humanStage, isInterruptedRetry, kindLabel, recoveryCancellable, statusLabel,
} from './queueModel';

export interface DrawerActions {
  runNow: (item: QueueExecutionItem) => void;
  retryComment: (item: QueueExecutionItem) => void;
  stopLive: (item: QueueExecutionItem) => void;
  cancelRecovery: (item: QueueExecutionItem) => void;
  addPermalink: (item: QueueExecutionItem) => void;
  resolve: (item: QueueExecutionItem, outcome: 'published' | 'not_published') => Promise<void>;
  remove: (item: QueueExecutionItem) => void;
  report: (item: QueueExecutionItem) => void;
  preview: (item: QueueExecutionItem) => void;
  openImage: (url: string, label: string) => void;
}

function browserViewerUrl(profile?: Profile) {
  const wsPort = profile?.container?.ws_port || (profile?.container?.vnc_port ? profile.container.vnc_port + 180 : null);
  if (!wsPort) return null;
  const host = (!window.location.hostname || window.location.hostname === 'tauri.localhost' || window.location.protocol === 'tauri:')
    ? '127.0.0.1' : window.location.hostname;
  return `http://${host}:${wsPort}/vnc.html`;
}

export function QueueDetailDrawer({ item, profile, profileName, actions, onClose }: {
  item: QueueExecutionItem; profile?: Profile; profileName: string; actions: DrawerActions; onClose: () => void;
}) {
  const [resolving, setResolving] = useState(false);

  const uncertain = UNCERTAIN_STATUSES.has(item.status);
  const failed = FAILED_STATUSES.has(item.status);
  const canResolve = uncertain || (recoveryCancellable(item) && (failed || uncertain));
  const canRerun = !(item.post_type === 'live' && uncertain)
    && (item.status === 'failed' || item.status === 'failed_before_publish' || isInterruptedRetry(item) || uncertain || item.status.startsWith('skipped'));
  const canRemove = !['running', 'uncertain', 'needs_review'].includes(item.status);
  const viewer = browserViewerUrl(profile);
  const resolve = async (outcome: 'published' | 'not_published') => {
    setResolving(true);
    try { await actions.resolve(item, outcome); } finally { setResolving(false); }
  };

  return <aside aria-label="Queue item details" className="flex max-h-full flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950">
    <header className="flex items-start gap-2 border-b border-zinc-800 p-4">
      <div className="min-w-0 flex-1">
        <h3 className="truncate text-sm font-semibold text-zinc-100">{profileName} · {kindLabel(item)}</h3>
        <p className="mt-0.5 truncate text-xs text-zinc-500">{[item.batch_name, formatTime(item.published_at || item.scheduled_at)].filter(Boolean).join(' · ')}</p>
      </div>
      <IconButton aria-label="Close details" onClick={onClose} className="h-8 w-8"><X className="h-4 w-4" /></IconButton>
    </header>

    <div className="flex-1 space-y-4 overflow-y-auto p-4 text-xs">
      <EvidenceViewer item={item} onOpen={(url, label) => actions.openImage(url, label)}
        fallback={item.media_file ? <div className="mx-auto w-32"><QueueThumb item={item} size="lg" onPreview={() => actions.preview(item)} /></div> : null} />

      <section className={`rounded-xl border p-3 ${uncertain ? 'border-amber-800/70 bg-amber-950/20' : failed ? 'border-red-900/70 bg-red-950/20' : 'border-zinc-800 bg-zinc-900/40'}`}>
        <p className="font-semibold text-zinc-200">{statusLabel(item.status)}{item.stage && !['published', 'cancelled'].includes(item.status) ? ` · ${humanStage(item.stage)}` : ''}</p>
        <p className="mt-1 leading-relaxed text-zinc-400">{describe(item)}</p>
        {item.error && !describe(item).includes(item.error) && <p className="mt-2 break-words text-red-300">{item.error}</p>}
        <NetworkRecoveryStatus recovery={item.network_recovery} details />
      </section>

      {canResolve && <section className="space-y-3 rounded-xl border border-amber-800/60 p-3">
        <div>
          <p className="font-semibold text-amber-200">Check Facebook, then record what happened</p>
          <p className="mt-1 text-zinc-400">Recording the real outcome keeps this from being posted twice. {item.post_type === 'live' ? 'Make sure the broadcast has ended.' : ''}</p>
        </div>
        {viewer
          ? <a href={viewer} target="_blank" rel="noreferrer" className="flex min-h-8 items-center justify-center gap-2 rounded-control border border-border text-zinc-200 hover:bg-elevated"><Eye className="h-3.5 w-3.5" />Open browser</a>
          : <p className="text-zinc-500">The browser isn’t running. Start the profile to look at the page.</p>}
        <div className="grid grid-cols-1 gap-2">
          <Button size="sm" className="bg-emerald-600 hover:bg-emerald-500" loading={resolving} onClick={() => resolve('published')}><Check className="h-3.5 w-3.5" />Yes, it’s posted</Button>
          <Button size="sm" variant="outline" loading={resolving} onClick={() => resolve('not_published')}><RotateCcw className="h-3.5 w-3.5" />No, allow retry</Button>
        </div>
      </section>}

      <div className="flex flex-wrap gap-2">
        {item.status === 'pending' && <Button size="sm" onClick={() => actions.runNow(item)}><Play className="h-3 w-3" />Run now</Button>}
        {canRerun && <Button size="sm" variant="outline" onClick={() => actions.runNow(item)}><RotateCcw className="h-3.5 w-3.5" />{failed || isInterruptedRetry(item) ? 'Retry' : uncertain ? 'Re-run anyway' : 'Re-run now'}</Button>}
        {commentNeedsRetry(item) && <Button size="sm" variant="outline" onClick={() => actions.retryComment(item)} loading={item.comment_retry_status === 'running'}><MessageCircle className="h-3.5 w-3.5" />Retry comment only</Button>}
        {item.post_type === 'live' && item.status === 'running' && <Button size="sm" variant="outline" onClick={() => actions.stopLive(item)}><Square className="h-3 w-3" />Stop Live</Button>}
        {item.post_url && <a href={item.post_url} target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center gap-1.5 rounded-control border border-border px-3 text-blue-300 hover:bg-elevated"><ExternalLink className="h-3.5 w-3.5" />View on Facebook</a>}
        {item.status === 'published' && !item.post_url && <Button size="sm" variant="outline" onClick={() => actions.addPermalink(item)}><Link2 className="h-3.5 w-3.5" />Add post link</Button>}
        {recoveryCancellable(item) && <Button size="sm" variant="ghost" onClick={() => actions.cancelRecovery(item)}>Cancel recovery</Button>}
      </div>

      <CommentAndLinkNotes item={item} />

      <Timeline item={item} />

      <details className="text-zinc-500">
        <summary className="cursor-pointer">Technical details</summary>
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          <dt>Retries</dt><dd className="text-zinc-300">{item.retry_count || 0}</dd>
          {item.media_file && <><dt>Media</dt><dd className="break-all text-zinc-300">{item.media_file}</dd></>}
          {item.warming_surface && <><dt>Warm-up</dt><dd className="text-zinc-300">{item.warming_surface === 'profile' ? 'Profile' : 'News feed'}{item.warming_surface_fallback ? ' (fallback)' : ''}{item.warming_duration_seconds != null ? ` · ${item.warming_duration_seconds.toFixed(0)}s` : ''}</dd></>}
          <dt>ID</dt><dd className="break-all font-mono text-[10px] text-zinc-400">{item.execution_id}</dd>
        </dl>
      </details>
    </div>

    <footer className="flex items-center justify-between gap-2 border-t border-zinc-800 p-3">
      <Button size="sm" variant="ghost" onClick={() => actions.report(item)}><Bug className="h-3.5 w-3.5" />Report a problem</Button>
      {canRemove
        ? <Button size="sm" variant="ghost" className="hover:text-red-300" onClick={() => actions.remove(item)}><Trash2 className="h-3.5 w-3.5" />Remove</Button>
        : <span className="flex items-center gap-1 text-[11px] text-zinc-600" title="Can’t remove while running or unresolved"><Lock className="h-3 w-3" />Locked</span>}
    </footer>
  </aside>;
}

function CommentAndLinkNotes({ item }: { item: QueueExecutionItem }) {
  const notes: { tone: 'ok' | 'warn'; text: string }[] = [];
  if (item.first_comment_status === 'submitted_verified') notes.push({ tone: 'ok', text: 'First comment verified.' });
  if (item.first_comment_status === 'submission_pending') notes.push({ tone: 'warn', text: 'First comment may still be sending. Check before acting.' });
  if (item.first_comment_status === 'submitted_unverified') notes.push({ tone: 'warn', text: 'First comment was sent but not verified. Check it on Facebook.' });
  if (item.post_url && item.permalink_status === 'recovered') notes.push({ tone: 'ok', text: 'Post link was found on a second check.' });
  if (!item.post_url && item.permalink_status === 'rejected_invalid') notes.push({ tone: 'warn', text: 'The captured post link was invalid.' });
  if (!item.post_url && item.permalink_status === 'missing_after_recovery') notes.push({ tone: 'warn', text: 'The post link couldn’t be found.' });
  if (!notes.length) return null;
  return <ul className="space-y-1">{notes.map(note => <li key={note.text} className={`flex items-start gap-1.5 ${note.tone === 'ok' ? 'text-emerald-300' : 'text-amber-300'}`}>
    {note.tone === 'ok' ? <Check className="mt-0.5 h-3 w-3 shrink-0" /> : <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />}{note.text}</li>)}</ul>;
}

function Timeline({ item }: { item: QueueExecutionItem }) {
  const stages = item.stage_history || [];
  const logs = item.logs || [];
  if (!stages.length && !logs.length) return null;
  return <details className="text-zinc-400" open={ACTIVE_STATUSES.has(item.status)}>
    <summary className="cursor-pointer text-zinc-300">Timeline{logs.length ? ` and logs (${logs.length})` : ''}</summary>
    {stages.length > 0 && <ol className="mt-2 space-y-1 border-l border-zinc-800 pl-3">
      {stages.slice(-15).map((entry, index) => <li key={`${entry.timestamp}-${index}`}>
        <span className="font-mono text-[10px] text-zinc-600">{new Date(entry.timestamp).toLocaleTimeString()}</span>{' '}
        <span className="text-zinc-300">{humanStage(entry.stage)}</span>{entry.reason ? <span className="text-zinc-500"> · {entry.reason}</span> : null}
      </li>)}
    </ol>}
    {logs.length > 0 && <pre className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-black/60 p-2 font-mono text-[10px] leading-relaxed text-zinc-400">{logs.slice(-60).join('\n')}</pre>}
  </details>;
}
