import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock, Copy, Download, ExternalLink, Eye, Loader2, MessageCircle, Play, Plus,
  RotateCcw, Search, ShieldAlert, Square, Trash2, User, X,
} from 'lucide-react';
import { DailyBatch, QueueDataResponse, QueueExecutionItem } from '../types/automation';
import { Profile } from '../types/profile';
import { AppendBatchPostsDialog } from './AppendBatchPostsDialog';
import { MediaLightbox, LightboxMedia } from './MediaLightbox';
import { stopLive } from '../services/live';
import { useAppDialog } from './ui/AppDialogProvider';
import { ActionMenu, ActionMenuItem } from './ui/ActionMenu';
import { useContextMenu } from './ui/ContextMenu';
import { writeHostClipboardText } from '../services/clipboard';
import { Button } from './ui/Button';
import { EmptyState } from './ui/EmptyState';
import { Input } from './ui/Input';
import { Select } from './ui/Select';
import { cn } from './ui/cn';
import { QueueRow, QueueRowActions } from './queue/QueueRow';
import { QueueDetailDrawer, DrawerActions } from './queue/QueueDetailDrawer';
import { SupportReportDialog } from './queue/SupportReportDialog';
import {
  ACTIVE_STATUSES, QueueSectionKey, QueueTypeFilter, UNCERTAIN_STATUSES, WAITING_STATUSES, commentNeedsRetry, hasOpenRecovery, isVideoFile,
  itemKind, kindLabel, matchesSearch, sectionOf,
} from './queue/queueModel';
import {
  backfillExecutionPermalink, cancelAllQueuePosts, cancelNetworkRecovery, deleteBatch, deleteExecution, fetchQueue,
  getSharedMediaUrl, rerunBatch, resolveUncertainExecution, retryExecutionComment, runExecutionNow,
} from '../services/api';

interface PostingQueuePanelProps {
  profiles: Profile[];
  /** Reports how many items need the user, so the parent can badge its tab. */
  onAttentionChange?: (count: number) => void;
  /** Opens the profile's browser so the user can watch a running job. */
  onWatchProfile?: (profileId: string) => void;
  onOpenAccount?: (profileId: string) => void;
  onCreate?: () => void;
  /** Set from other pages to show one account's items; a new `key` re-applies it. */
  focusAccount?: { profileId: string; key: number } | null;
}

const TYPE_FILTERS: { value: QueueTypeFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'photo', label: 'Posts' },
  { value: 'reel', label: 'Reels' },
  { value: 'live', label: 'Live' },
  { value: 'warming', label: 'Warm-up' },
];
const ALL_BATCHES = '__all__';
const NEXT_PAGE = 25;
const FINISHED_PAGE = 25;

const sortTime = (item: QueueExecutionItem) => new Date(item.published_at || item.stage_updated_at || item.scheduled_at).getTime() || 0;
const scheduledTime = (item: QueueExecutionItem) => new Date(item.scheduled_at).getTime() || 0;

export const PostingQueuePanel: React.FC<PostingQueuePanelProps> = ({ profiles, onAttentionChange, onWatchProfile, onOpenAccount, onCreate, focusAccount }) => {
  const { showConfirm, showPrompt, showToast } = useAppDialog();
  const [queueData, setQueueData] = useState<QueueDataResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [typeFilter, setTypeFilter] = useState<QueueTypeFilter>('all');
  const [chosenBatch, setBatchFilter] = useState<string | null>(null);
  const batches = useMemo(() => [...(queueData?.batches || [])].sort((a, b) =>
    (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0)), [queueData?.batches]);
  // Follow the newest batch until the user explicitly chooses another view.
  const batchFilter = chosenBatch ?? batches[0]?.batch_id ?? ALL_BATCHES;
  const [search, setSearch] = useState('');
  const [accountFilter, setAccountFilter] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nextLimit, setNextLimit] = useState(NEXT_PAGE);
  const [finishedLimit, setFinishedLimit] = useState(FINISHED_PAGE);
  const [finishedOpen, setFinishedOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [lightbox, setLightbox] = useState<LightboxMedia | null>(null);
  const [appendBatch, setAppendBatch] = useState<DailyBatch | null>(null);
  const [report, setReport] = useState<{ open: boolean; item: QueueExecutionItem | null }>({ open: false, item: null });
  const lastAttention = useRef(0);
  const menu = useContextMenu();

  const fail = useCallback((err: unknown, fallback: string) => {
    showToast(err instanceof Error && err.message ? err.message : fallback, 'error');
  }, [showToast]);

  const loadQueue = useCallback(async () => {
    try {
      setQueueData(await fetchQueue());
    } catch (err) {
      console.error('Failed to load queue:', err);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    loadQueue();
    const interval = setInterval(loadQueue, 5000);
    return () => clearInterval(interval);
  }, [loadQueue]);

  useEffect(() => { setNextLimit(NEXT_PAGE); setFinishedLimit(FINISHED_PAGE); }, [typeFilter, batchFilter, search, accountFilter]);

  // Arriving from an account: show only its items, with nothing else filtered out.
  useEffect(() => {
    if (!focusAccount) return;
    setAccountFilter(focusAccount.profileId);
    setTypeFilter('all');
    setBatchFilter(ALL_BATCHES);
    setSearch('');
    setSelectedId(null);
  }, [focusAccount?.key]);

  const profileById = useMemo(() => new Map(profiles.map(p => [p.id, p])), [profiles]);
  const profileName = useCallback((id: string) => profileById.get(id)?.name || id, [profileById]);
  const executions = queueData?.executions || [];

  const filtered = useMemo(() => executions.filter(item =>
    (typeFilter === 'all' || itemKind(item) === typeFilter)
    && (batchFilter === ALL_BATCHES || item.batch_id === batchFilter)
    && (!accountFilter || item.profile_id === accountFilter)
    && matchesSearch(item, search.trim(), profileName(item.profile_id))), [executions, typeFilter, batchFilter, search, accountFilter, profileName]);

  const sections = useMemo(() => {
    const result: Record<QueueSectionKey, QueueExecutionItem[]> = { attention: [], now: [], next: [], finished: [] };
    for (const item of filtered) result[sectionOf(item)].push(item);
    result.attention.sort((a, b) => sortTime(b) - sortTime(a));
    result.now.sort((a, b) => scheduledTime(a) - scheduledTime(b));
    result.next.sort((a, b) => scheduledTime(a) - scheduledTime(b));
    result.finished.sort((a, b) => sortTime(b) - sortTime(a));
    return result;
  }, [filtered]);

  // The tab badge and warning toast always count the whole queue, not the current filter.
  const attentionTotal = useMemo(() => executions.filter(item => sectionOf(item) === 'attention').length, [executions]);
  useEffect(() => {
    onAttentionChange?.(attentionTotal);
    if (loaded && attentionTotal > lastAttention.current) {
      showToast(`${attentionTotal} item${attentionTotal === 1 ? '' : 's'} in the queue need${attentionTotal === 1 ? 's' : ''} you.`, 'warning');
    }
    lastAttention.current = attentionTotal;
  }, [attentionTotal, loaded, onAttentionChange, showToast]);

  const selected = selectedId ? executions.find(item => item.execution_id === selectedId) || null : null;
  const remaining = executions.filter(item => WAITING_STATUSES.has(item.status) || ACTIVE_STATUSES.has(item.status));
  const lastScheduled = remaining.reduce<QueueExecutionItem | null>((last, item) => !last || scheduledTime(item) > scheduledTime(last) ? item : last, null);
  const hasStoppable = remaining.length > 0 || executions.some(hasOpenRecovery);

  const batchStats = useCallback((batchId: string) => {
    const items = executions.filter(item => item.batch_id === batchId);
    return {
      total: items.length,
      done: items.filter(item => ['published', 'completed'].includes(item.status)).length,
      active: items.filter(item => ACTIVE_STATUSES.has(item.status)).length,
      waiting: items.filter(item => WAITING_STATUSES.has(item.status)).length,
      attention: items.filter(item => sectionOf(item) === 'attention').length,
      recovering: items.filter(hasOpenRecovery).length,
    };
  }, [executions]);
  const batchOptions = useMemo(() => [{ value: ALL_BATCHES, label: 'All batches' }, ...batches.map(batch => {
    const stats = batchStats(batch.batch_id);
    return { value: batch.batch_id, label: `${batch.name} · ${stats.done}/${stats.total} done` };
  })], [batches, batchStats]);
  const activeBatch = batches.find(batch => batch.batch_id === batchFilter) || null;

  // ---- actions ----------------------------------------------------------

  const withBusy = async (item: QueueExecutionItem, task: () => Promise<void>) => {
    setBusyId(item.execution_id);
    try { await task(); } finally { setBusyId(null); await loadQueue(); }
  };

  const runNow = async (item: QueueExecutionItem) => {
    if (UNCERTAIN_STATUSES.has(item.status) && !await showConfirm(
      'This post may already be on Facebook. Running it again could post it twice. Check the page first, or record the outcome instead.',
      { title: 'Re-run an unconfirmed post?', confirmLabel: 'Re-run anyway', variant: 'danger' },
    )) return;
    await startNow(item);
  };
  const startNow = (item: QueueExecutionItem) => withBusy(item, async () => {
    try {
      const result = await runExecutionNow(item.execution_id);
      showToast(result.message || (result.queued ? 'Retry queued.' : 'Started.'), 'success');
    } catch (err) { fail(err, 'Couldn’t start this item.'); }
  });

  const retryComment = async (item: QueueExecutionItem) => {
    if (!await showConfirm('Retry only the first comment? The post itself won’t be posted again.', { title: 'Retry first comment', confirmLabel: 'Retry comment' })) return;
    await withBusy(item, async () => {
      try { await retryExecutionComment(item.execution_id); } catch (err) { fail(err, 'Couldn’t retry the comment.'); }
    });
  };

  const stopLiveItem = (item: QueueExecutionItem) => withBusy(item, async () => {
    try { await stopLive(item.profile_id, item.execution_id); } catch (err) { fail(err, 'Couldn’t stop Live.'); }
  });

  const cancelRecovery = (item: QueueExecutionItem) => withBusy(item, async () => {
    try { await cancelNetworkRecovery(item.execution_id); } catch (err) { fail(err, 'Couldn’t cancel recovery.'); }
  });

  const addPermalink = async (item: QueueExecutionItem) => {
    const postUrl = await showPrompt('Paste the Facebook link for this post:', {
      title: 'Add post link', confirmLabel: 'Add link', placeholder: 'https://www.facebook.com/...',
    });
    if (!postUrl?.trim()) return;
    await withBusy(item, async () => {
      try {
        await backfillExecutionPermalink(item.execution_id, postUrl.trim(), 1.0, 'Operator-supplied verified permalink', 'dashboard_manual_backfill');
      } catch (err) { fail(err, 'Couldn’t save the link.'); }
    });
  };

  const resolve: DrawerActions['resolve'] = async (item, outcome) => {
    if (item.post_type === 'live' && !await showConfirm('Check Facebook and confirm this broadcast has ended before recording its outcome.', {
      title: 'Has the broadcast ended?', confirmLabel: 'Broadcast has ended',
    })) return;
    try {
      await resolveUncertainExecution(item.execution_id, outcome, undefined,
        undefined, item.post_type === 'live' ? true : undefined);
      showToast(outcome === 'published' ? 'Marked as posted.' : 'Marked as not posted. You can retry it now.', 'success');
    } catch (err) {
      fail(err, 'Couldn’t save the outcome.');
    } finally {
      await loadQueue();
    }
  };

  const remove = async (item: QueueExecutionItem) => {
    if (!await showConfirm('Remove this item from the queue?', { title: 'Remove item', confirmLabel: 'Remove', variant: 'danger' })) return;
    try {
      await deleteExecution(item.execution_id);
      setSelectedId(null);
    } catch (err) { fail(err, 'Couldn’t remove this item.'); }
    await loadQueue();
  };

  const preview = (item: QueueExecutionItem) => {
    if (!item.media_file) return;
    const video = item.post_type === 'reel' || item.post_type === 'live' || isVideoFile(item.media_file);
    setLightbox({
      url: getSharedMediaUrl(item.media_file), type: video ? 'reel' : 'photo', name: item.media_file,
      caption: item.spun_caption || item.base_caption, comment: item.first_comment || undefined,
    });
  };

  const stopAndCancel = async (batch?: DailyBatch) => {
    const confirmed = await showConfirm(
      `${batch ? `Stop running work and cancel everything still waiting in “${batch.name}”? Other batches keep going.` : 'Stop running work and cancel everything still waiting in every batch?'} Items hidden by filters are included. Posts already sent can’t be undone, and an interrupted post may need review. Cloud jobs aren’t affected.`,
      { title: batch ? 'Stop and cancel this batch?' : 'Stop and cancel everything?', confirmLabel: batch ? 'Stop and cancel batch' : 'Stop and cancel all', variant: 'danger' },
    );
    if (!confirmed) return;
    setBatchBusy(true);
    try {
      const result = await cancelAllQueuePosts(batch?.batch_id);
      showToast(result.message, 'success');
    } catch (err) { fail(err, 'Couldn’t cancel the queue.'); }
    finally { setBatchBusy(false); await loadQueue(); }
  };

  const rerun = async (batch: DailyBatch) => {
    setBatchBusy(true);
    try {
      const result = await rerunBatch(batch.batch_id);
      showToast(result.message, result.queued ? 'success' : 'info');
    } catch (err) { fail(err, 'Couldn’t re-run this batch.'); }
    finally { setBatchBusy(false); await loadQueue(); }
  };

  const removeBatch = async (batch: DailyBatch) => {
    if (!await showConfirm(`Remove “${batch.name}” and everything still waiting in it?`, { title: 'Remove batch', confirmLabel: 'Remove batch', variant: 'danger' })) return;
    try {
      await deleteBatch(batch.batch_id);
      setBatchFilter(null);
    } catch (err) { fail(err, 'Couldn’t remove this batch.'); }
    await loadQueue();
  };

  const rowActions: QueueRowActions = {
    open: (item) => setSelectedId(current => current === item.execution_id ? null : item.execution_id),
    runNow, retryComment, stopLive: stopLiveItem, preview,
    watch: (item) => onWatchProfile?.(item.profile_id),
  };
  const drawerActions: DrawerActions = {
    runNow, retryComment, stopLive: stopLiveItem, cancelRecovery, addPermalink, resolve, remove, preview,
    report: (item) => setReport({ open: true, item }),
    openImage: (url, label) => setLightbox({ url, type: 'photo', name: label }),
  };

  const menuItems: ActionMenuItem[] = [];
  if (activeBatch) {
    const stats = batchStats(activeBatch.batch_id);
    if (stats.attention > 0 || stats.waiting > 0 || stats.done < stats.total) {
      menuItems.push({ label: 'Re-run safe retries in batch', icon: <RotateCcw className="h-3.5 w-3.5" />, onSelect: () => rerun(activeBatch), disabled: batchBusy,
        hint: 'Published posts stay done. Unconfirmed posts need review first.' });
    }
    if (stats.attention === 0 && stats.active + stats.waiting > 0 && !isLiveBatch(activeBatch)) {
      menuItems.push({ label: 'Add posts to batch', icon: <Plus className="h-3.5 w-3.5" />, onSelect: () => setAppendBatch(activeBatch) });
    }
    menuItems.push(
      { label: 'Stop and cancel batch', icon: <X className="h-3.5 w-3.5" />, danger: true, disabled: batchBusy || stats.active + stats.waiting + stats.recovering === 0, onSelect: () => stopAndCancel(activeBatch) },
      { label: 'Remove batch', icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, onSelect: () => removeBatch(activeBatch) },
    );
  } else {
    menuItems.push({ label: 'Stop and cancel all', icon: <X className="h-3.5 w-3.5" />, danger: true, disabled: batchBusy || !hasStoppable, onSelect: () => stopAndCancel() });
  }
  menuItems.push({ label: 'Export diagnostics', icon: <Download className="h-3.5 w-3.5" />, onSelect: () => setReport({ open: true, item: null }) });

  const filtersActive = typeFilter !== 'all' || batchFilter !== ALL_BATCHES || search.trim() !== '' || accountFilter !== null;
  // Right-click mirrors what the row and its detail panel offer; nothing is right-click only.
  const itemMenu = (item: QueueExecutionItem): ActionMenuItem[] => {
    const uncertain = UNCERTAIN_STATUSES.has(item.status);
    const icon = 'h-3.5 w-3.5';
    const items: ActionMenuItem[] = [{ label: 'View details', icon: <Eye className={icon} />, onSelect: () => setSelectedId(item.execution_id) }];
    if (uncertain) items.push({ label: item.post_type === 'live' ? 'Confirm outcome…' : 'Was it posted?…', icon: <ShieldAlert className={icon} />, onSelect: () => setSelectedId(item.execution_id) });
    if (item.status === 'pending') items.push({ label: 'Run now', icon: <Play className={icon} />, onSelect: () => runNow(item) });
    if (['failed', 'failed_before_publish'].includes(item.status)) items.push({ label: 'Retry', icon: <RotateCcw className={icon} />, onSelect: () => runNow(item) });
    if (uncertain && item.post_type !== 'live') items.push({ label: 'Re-run anyway', icon: <RotateCcw className={icon} />, onSelect: () => runNow(item) });
    if (item.status.startsWith('skipped')) items.push({ label: 'Re-run', icon: <RotateCcw className={icon} />, onSelect: () => runNow(item) });
    if (commentNeedsRetry(item)) items.push({ label: 'Retry comment only', icon: <MessageCircle className={icon} />, onSelect: () => retryComment(item) });
    if (item.post_type === 'live' && item.status === 'running') items.push({ label: 'Stop Live', icon: <Square className={icon} />, onSelect: () => stopLiveItem(item) });
    if (item.post_url) {
      const url = item.post_url;
      items.push(
        { label: 'View on Facebook', icon: <ExternalLink className={icon} />, divider: true, onSelect: () => { window.open(url, '_blank', 'noreferrer'); } },
        { label: 'Copy post link', icon: <Copy className={icon} />, onSelect: () => {
          writeHostClipboardText(url).then(() => showToast('Post link copied.', 'success')).catch(() => showToast('Clipboard access was blocked.', 'error'));
        } },
      );
    }
    if (ACTIVE_STATUSES.has(item.status) && onWatchProfile) items.push({ label: 'Watch browser', icon: <Eye className={icon} />, divider: !item.post_url, onSelect: () => onWatchProfile(item.profile_id) });
    if (onOpenAccount) items.push({ label: 'Open account', icon: <User className={icon} />, divider: !item.post_url && !ACTIVE_STATUSES.has(item.status), onSelect: () => onOpenAccount(item.profile_id) });
    if (!['running', 'uncertain', 'needs_review'].includes(item.status)) items.push({ label: 'Remove from queue', icon: <Trash2 className={icon} />, danger: true, divider: true, onSelect: () => remove(item) });
    return items;
  };

  const renderRows = (items: QueueExecutionItem[]) => <div className="divide-y divide-zinc-800/70">
    {items.map(item => <QueueRow key={item.execution_id} item={item} profileName={profileName(item.profile_id)}
      selected={item.execution_id === selectedId} actions={rowActions} busy={busyId === item.execution_id}
      onContextMenu={(e) => menu.open(e, itemMenu(item), `${profileName(item.profile_id)} · ${kindLabel(item)}`)}
      onMenuKey={menu.keyboardTrigger(() => itemMenu(item), `${profileName(item.profile_id)} · ${kindLabel(item)}`)} />)}
  </div>;

  return (
    <div className="mx-auto flex h-full min-h-0 max-w-7xl flex-col gap-3">
      {/* Toolbar: filters on the left, queue summary and overflow actions on the right */}
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Filter by type" className="flex rounded-control border border-zinc-800 bg-zinc-950 p-0.5">
          {TYPE_FILTERS.map(option => <button key={option.value} type="button" role="tab" aria-selected={typeFilter === option.value}
            onClick={() => setTypeFilter(option.value)}
            className={cn('rounded-md px-2.5 py-1 text-xs transition-colors', typeFilter === option.value ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
            {option.label}
          </button>)}
        </div>
        <Select value={batchFilter} onValueChange={setBatchFilter} options={batchOptions} ariaLabel="Filter by batch" className="min-h-8 w-full shrink-0 whitespace-nowrap py-1 sm:w-80 [&>span:first-child]:truncate" />
        <div className="relative w-full sm:w-56">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Account or caption" aria-label="Search the queue" className="min-h-8 py-1 pl-8" />
        </div>
        {accountFilter && <span className="inline-flex items-center gap-1 rounded-full border border-blue-800 bg-blue-950/40 py-0.5 pl-2.5 pr-1 text-xs text-blue-200">
          Account: {profileName(accountFilter)}
          <button type="button" aria-label="Show all accounts" onClick={() => setAccountFilter(null)} className="rounded-full p-0.5 hover:bg-blue-900"><X className="h-3 w-3" /></button>
        </span>}
        {filtersActive && <button type="button" className="text-xs text-blue-400 hover:text-blue-300" onClick={() => { setTypeFilter('all'); setBatchFilter(ALL_BATCHES); setSearch(''); setAccountFilter(null); }}>Clear filters</button>}
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-zinc-400">
            {remaining.length > 0
              ? <>{remaining.length} left{lastScheduled ? <> · last at {new Date(lastScheduled.scheduled_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</> : null}</>
              : 'Nothing waiting'}
          </span>
          <ActionMenu items={menuItems} ariaLabel={activeBatch ? `Actions for ${activeBatch.name}` : 'Queue actions'} label={activeBatch ? 'Batch' : undefined} />
        </div>
      </div>

      {!loaded ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-xs text-zinc-500"><Loader2 className="h-4 w-4 animate-spin" />Loading queue…</div>
      ) : executions.length === 0 ? (
        <EmptyState icon={<Clock className="h-6 w-6" />} title="Your queue is empty"
          description="Go to Post to schedule posts, reels, Live broadcasts, or warm-ups."
          action={onCreate && <Button onClick={onCreate}><Plus className="h-3.5 w-3.5" />New post</Button>} />
      ) : (
        <div className={cn('grid min-h-0 flex-1 gap-4', selected ? 'lg:grid-cols-[minmax(0,1fr)_22rem]' : 'grid-cols-1')}>
          <div className="min-h-0 space-y-4 overflow-y-auto pb-4 pr-1">
            {filtered.length === 0 && <EmptyState title="Nothing matches these filters" description="Try another type, batch, or search." />}

            {sections.attention.length > 0 && <Section title="Needs you" count={sections.attention.length} tone="warning" icon={<AlertTriangle className="h-3.5 w-3.5" />}>
              {renderRows(sections.attention)}
            </Section>}

            {sections.now.length > 0 && <Section title="Now" count={sections.now.length} tone="accent" icon={<Loader2 className="h-3.5 w-3.5 animate-spin" />}>
              {renderRows(sections.now)}
            </Section>}

            {sections.next.length > 0 && <Section title="Up next" count={sections.next.length} icon={<Clock className="h-3.5 w-3.5" />}>
              {renderRows(sections.next.slice(0, nextLimit))}
              {sections.next.length > nextLimit && <ShowMore remaining={sections.next.length - nextLimit} onClick={() => setNextLimit(nextLimit + NEXT_PAGE * 2)} />}
            </Section>}

            {sections.finished.length > 0 && <Section title="Finished" count={sections.finished.length} icon={<CheckCircle2 className="h-3.5 w-3.5" />}
              collapsible open={finishedOpen} onToggle={() => setFinishedOpen(!finishedOpen)}>
              {renderRows(sections.finished.slice(0, finishedLimit))}
              {sections.finished.length > finishedLimit && <ShowMore remaining={sections.finished.length - finishedLimit} onClick={() => setFinishedLimit(finishedLimit + FINISHED_PAGE * 2)} />}
            </Section>}
          </div>

          {selected && <>
            {/* Below lg the drawer floats over the list instead of taking a column. */}
            <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setSelectedId(null)} />
            <div className="fixed inset-y-4 right-4 z-50 w-[min(22rem,calc(100vw-2rem))] lg:static lg:z-auto lg:w-auto lg:min-h-0">
              <QueueDetailDrawer item={selected} profile={profileById.get(selected.profile_id)} profileName={profileName(selected.profile_id)}
                actions={drawerActions} onClose={() => setSelectedId(null)} />
            </div>
          </>}
        </div>
      )}

      {appendBatch && <AppendBatchPostsDialog batch={appendBatch} onClose={() => setAppendBatch(null)} onAppended={loadQueue} />}
      <SupportReportDialog open={report.open} item={report.item} profileName={report.item ? profileName(report.item.profile_id) : undefined}
        onClose={() => setReport({ open: false, item: null })}
        onSaved={(location) => { setReport({ open: false, item: null }); showToast(`Support ZIP saved to ${location}. Review it before sending.`, 'success'); }}
        onError={(message) => showToast(message, 'error')} />
      <MediaLightbox media={lightbox} onClose={() => setLightbox(null)} />
      {menu.element}
    </div>
  );
};

const isLiveBatch = (batch: DailyBatch) => batch.posts.length > 0 && batch.posts.every(post => post.type === 'live');

function Section({ title, count, icon, tone, children, collapsible, open = true, onToggle }: {
  title: string; count: number; icon: React.ReactNode; tone?: 'warning' | 'accent'; children: React.ReactNode;
  collapsible?: boolean; open?: boolean; onToggle?: () => void;
}) {
  const heading = <span className={cn('flex items-center gap-1.5 text-xs font-semibold',
    tone === 'warning' ? 'text-amber-300' : tone === 'accent' ? 'text-blue-300' : 'text-zinc-300')}>
    {icon}{title}<span className="font-normal text-zinc-500">· {count}</span>
  </span>;
  return <section className="space-y-1.5">
    {collapsible
      ? <button type="button" aria-expanded={open} onClick={onToggle} className="flex items-center gap-1 hover:text-white">
        {open ? <ChevronDown className="h-3.5 w-3.5 text-zinc-500" /> : <ChevronRight className="h-3.5 w-3.5 text-zinc-500" />}{heading}
      </button>
      : heading}
    {open && <div aria-label={title} role="group" className={cn('overflow-hidden rounded-xl border bg-zinc-950/50',
      tone === 'warning' ? 'border-amber-800/60' : 'border-zinc-800')}>{children}</div>}
  </section>;
}

function ShowMore({ remaining, onClick }: { remaining: number; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="w-full border-t border-zinc-800/70 py-2 text-xs text-zinc-400 hover:bg-zinc-900/60 hover:text-zinc-200">
    Show more · {remaining} hidden
  </button>;
}
