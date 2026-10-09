import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { DailyBatch, QueueDataResponse, QueueExecutionItem } from '../types/automation';
import { Profile } from '../types/profile';
import { AppendBatchPostsDialog } from './AppendBatchPostsDialog';
import { stopLive } from '../services/live';
import { Checkbox } from './ui/Checkbox';
import { useAppDialog } from './ui/AppDialogProvider';
import {
  backfillExecutionPermalink,
  downloadSupportBundle,
  fetchQueue,
  deleteBatch,
  deleteExecution,
  retryExecutionComment,
  runExecutionNow,
  resolveUncertainExecution,
  getSharedMediaUrl,
} from '../services/api';
import {
  Clock,
  Play,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Flame,
  Film,
  Image as ImageIcon,
  Lock,
  X,
  RotateCcw,
  ShieldAlert,
  ExternalLink,
  Check,
  Eye,
  Search,
  MessageCircle,
  Bug,
  Download,
  ChevronDown,
  ChevronRight,
  Layers3,
  Users,
  Plus,
} from 'lucide-react';

interface PostingQueuePanelProps {
  profiles: Profile[];
  postTypeFilter?: 'posts' | 'live';
}

interface LightboxMedia {
  url: string;
  type: 'photo' | 'reel';
  name: string;
  caption?: string;
  comment?: string;
}

export const PostingQueuePanel: React.FC<PostingQueuePanelProps> = ({ profiles, postTypeFilter = 'posts' }) => {
  const { showConfirm, showPrompt, showToast } = useAppDialog();
  const [queueData, setQueueData] = useState<QueueDataResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const setActionError = useCallback((message: string | null) => {
    if (message) showToast(message, 'error');
  }, [showToast]);
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);
  const [retryingCommentId, setRetryingCommentId] = useState<string | null>(null);
  const [reportDialogOpen, setReportDialogOpen] = useState(false);
  const [reportingItem, setReportingItem] = useState<QueueExecutionItem | null>(null);
  const [reportDescription, setReportDescription] = useState('');
  const [reportIncludeEvidence, setReportIncludeEvidence] = useState(false);
  const [reportIncludeContent, setReportIncludeContent] = useState(false);
  const [isGeneratingReport, setIsGeneratingReport] = useState(false);
  const setReportSuccess = useCallback((message: string | null) => {
    if (message) showToast(message, 'success');
  }, [showToast]);
  const [expandedBatchId, setExpandedBatchId] = useState<string | null>(null);
  const [expandedProfileKey, setExpandedProfileKey] = useState<string | null>(null);
  const [expandedExecutionId, setExpandedExecutionId] = useState<string | null>(null);
  const [batchPage, setBatchPage] = useState(1);
  const [profilePage, setProfilePage] = useState(1);
  const [executionPage, setExecutionPage] = useState(1);

  useEffect(() => {
    setBatchPage(1);
    setProfilePage(1);
    setExecutionPage(1);
  }, [filterStatus]);
  const [appendBatch, setAppendBatch] = useState<DailyBatch | null>(null);

  // Phase 0 Safety Gate: Review & Resolve Uncertain State
  const [stoppingLiveId, setStoppingLiveId] = useState<string | null>(null);
  const [resolvingItem, setResolvingItem] = useState<QueueExecutionItem | null>(null);
  const [resolveNote, setResolveNote] = useState('');
  const [resolvePostUrl, setResolvePostUrl] = useState('');
  const [isResolving, setIsResolving] = useState(false);

  const loadQueue = async () => {
    setLoading(true);
    try {
      const data = await fetchQueue();
      const include = (type?: string) => postTypeFilter === 'live' ? type === 'live' : type !== 'live';
      const executions = data.executions.filter(item => include(item.post_type));
      const batches = data.batches.map(batch => ({...batch, posts: batch.posts.filter(post => include(post.type))})).filter(batch => batch.posts.length > 0);
      const count = (...statuses: string[]) => executions.filter(item => statuses.includes(item.status)).length;
      setQueueData({...data, executions, batches, stats: {total: executions.length, pending: count('pending', 'ready'), running: count('running', 'preparing'), published: count('published'), completed: count('completed'), failed: count('failed', 'failed_before_publish', 'failed_after_publish'), uncertain: count('uncertain', 'needs_review'), skipped: executions.filter(item => item.status.startsWith('skipped')).length}});
    } catch (err: any) {
      console.error('Failed to load queue:', err);
      setActionError(err.message || 'Could not refresh the queue.');
    } finally {
      setLoading(false);
    }
  };


  const handleResolveUncertain = async (resolution: 'published' | 'not_published') => {
    if (!resolvingItem) return;
    if (resolvingItem.post_type === 'live' && !await showConfirm('Check Facebook and confirm this broadcast has ended before resolving its outcome.', { title: 'End confirmation required', confirmLabel: 'Broadcast has ended' })) return;
    setIsResolving(true);
    setActionError(null);
    try {
      await resolveUncertainExecution(
        resolvingItem.execution_id,
        resolution,
        resolveNote.trim() || undefined,
        resolution === 'published' ? resolvePostUrl.trim() || undefined : undefined,
        resolvingItem.post_type === 'live' ? true : undefined
      );
      setResolvingItem(null);
      setResolveNote('');
      setResolvePostUrl('');
      await loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to resolve uncertain execution');
    } finally {
      setIsResolving(false);
    }
  };

  useEffect(() => {
    loadQueue();
    const interval = setInterval(loadQueue, 5000);
    return () => clearInterval(interval);
  }, [postTypeFilter]);

  const handleRunNow = async (executionId: string) => {
    setActionError(null);
    try {
      const result = await runExecutionNow(executionId);
      setReportSuccess(result.message || (result.queued ? 'Retry queued.' : 'Execution started.'));
      await loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to dispatch execution');
    }
  };

  const handleDeleteExecution = async (executionId: string) => {
    try {
      await deleteExecution(executionId);
      loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to delete execution');
    }
  };

  const handleDeleteBatch = async (batchId: string) => {
    if (!await showConfirm('Remove this batch and all of its pending executions?', {
      title: 'Remove batch', confirmLabel: 'Remove batch', variant: 'danger',
    })) return;
    try {
      await deleteBatch(batchId);
      loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to delete batch');
    }
  };

  const handlePermalinkBackfill = async (item: QueueExecutionItem) => {
    const postUrl = await showPrompt('Paste the verified Facebook permalink for this published execution:', {
      title: 'Attach verified permalink',
      confirmLabel: 'Attach permalink',
      placeholder: 'https://www.facebook.com/...',
    });
    if (!postUrl?.trim()) return;
    setActionError(null);
    try {
      await backfillExecutionPermalink(
        item.execution_id,
        postUrl.trim(),
        1.0,
        'Operator-supplied verified permalink',
        'dashboard_manual_backfill'
      );
      await loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to attach verified permalink');
    }
  };

  const handleCommentRetry = async (item: QueueExecutionItem) => {
    if (!await showConfirm('Retry only the first comment? The published post will not be recreated.', {
      title: 'Retry first comment', confirmLabel: 'Retry comment',
    })) return;
    setRetryingCommentId(item.execution_id);
    setActionError(null);
    try {
      await retryExecutionComment(item.execution_id);
      await loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to start comment-only retry');
    } finally {
      setRetryingCommentId(null);
    }
  };

  const openReportDialog = (item: QueueExecutionItem | null) => {
    setReportingItem(item);
    setReportDescription('');
    setReportIncludeEvidence(false);
    setReportIncludeContent(false);
    setReportSuccess(null);
    setReportDialogOpen(true);
  };

  const handleDownloadReport = async () => {
    setIsGeneratingReport(true);
    setActionError(null);
    try {
      const savedLocation = await downloadSupportBundle({
        executionId: reportingItem?.execution_id,
        description: reportDescription.trim(),
        includeEvidence: Boolean(reportingItem) && reportIncludeEvidence,
        includeContent: Boolean(reportingItem) && reportIncludeContent,
      });
      setReportDialogOpen(false);
      setReportSuccess(`Support ZIP saved to ${savedLocation}. Review it before sending it to support.`);
    } catch (err: any) {
      setActionError(err.message || 'Failed to create support bundle');
    } finally {
      setIsGeneratingReport(false);
    }
  };

  const getProfileName = (id: string) => {
    const p = profiles.find((prof) => prof.id === id);
    return p ? p.name : id;
  };

  const formatScheduledTime = (isoString: string) => {
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (_) {
      return isoString;
    }
  };

  const getStatusBadge = (status: string, executionId?: string) => {
    switch (status) {
      case 'published':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800/80 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-emerald-400" /> Published
          </span>
        );
      case 'completed':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-orange-950 text-orange-300 border border-orange-800/80 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-orange-400" /> Completed
          </span>
        );
      case 'running':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-950 text-blue-300 border border-blue-800/80 flex items-center gap-1 animate-pulse">
            <Loader2 className="w-3 h-3 text-blue-400 animate-spin" /> Running
          </span>
        );
      case 'preparing':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-violet-950 text-violet-300 border border-violet-800/80 flex items-center gap-1 animate-pulse">
            <Loader2 className="w-3 h-3 text-violet-400 animate-spin" /> Preparing
          </span>
        );
      case 'ready':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-950 text-cyan-300 border border-cyan-800/80 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-cyan-400" /> Ready
          </span>
        );
      case 'failed':
      case 'failed_before_publish':
        return executionId ? (
          <button
            type="button"
            onClick={() => handleRunNow(executionId)}
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-950 hover:bg-red-900/90 text-red-300 hover:text-red-100 border border-red-800/80 hover:border-red-600 flex items-center gap-1 transition-all cursor-pointer shadow-sm group"
            title="Pre-publish failure. Safe to retry."
          >
            <AlertTriangle className="w-3 h-3 text-red-400 group-hover:scale-110 transition-transform" />
            <span>Failed</span>
            <RotateCcw className="w-2.5 h-2.5 text-red-400/80 group-hover:rotate-180 transition-transform ml-0.5" />
          </button>
        ) : (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-950 text-red-300 border border-red-800/80 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-red-400" /> Failed
          </span>
        );
      case 'failed_after_publish':
        return (
          <span
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-950 text-red-300 border border-red-800/80 flex items-center gap-1"
            title="Post was clicked, but the Reel was not found after final verification. Automatic rerun is blocked."
          >
            <AlertTriangle className="w-3 h-3 text-red-400" /> Failed after Post
          </span>
        );
      case 'uncertain':
      case 'needs_review':
        return (
          <span
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-950 text-amber-300 border border-amber-800/80 flex items-center gap-1 shadow-sm"
            title="Outcome was not verified. Direct rerun is available."
          >
            <ShieldAlert className="w-3 h-3 text-amber-400" /> {status === 'needs_review' ? 'Needs Review' : 'Uncertain'}
          </span>
        );
      default:
        if (status.startsWith('skipped')) {
          return (
            <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-zinc-900 text-zinc-500 border border-zinc-800 flex items-center gap-1">
              <Lock className="w-3 h-3" /> Skipped
            </span>
          );
        }
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-zinc-900 text-zinc-400 border border-zinc-800 flex items-center gap-1">
            <Clock className="w-3 h-3 text-zinc-500" /> Pending
          </span>
        );
    }
  };

  const executions = queueData?.executions || [];
  const batches = queueData?.batches || [];
  const stats = queueData?.stats || {
    total: 0,
    pending: 0,
    running: 0,
    published: 0,
    completed: 0,
    failed: 0,
    uncertain: 0,
    skipped: 0,
  };

  // Filter executions
  const filteredExecutions = executions.filter((execItem) => {
    if (filterStatus !== 'all') {
      if (filterStatus === 'failed') {
        if (!['failed', 'failed_before_publish', 'failed_after_publish'].includes(execItem.status)) return false;
      } else if (filterStatus === 'uncertain') {
        if (execItem.status !== 'uncertain' && execItem.status !== 'needs_review') return false;
      } else if (filterStatus === 'pending') {
        if (execItem.status !== 'pending' && execItem.status !== 'ready') return false;
      } else if (filterStatus === 'running') {
        if (execItem.status !== 'running' && execItem.status !== 'preparing') return false;
      } else if (execItem.status !== filterStatus) {
        return false;
      }
    }
    return true;
  });

  const uncertainCount = stats.uncertain || 0;
  const failedCount = stats.failed || 0;
  useEffect(() => {
    if (uncertainCount > 0 || failedCount > 0) {
      showToast([
        uncertainCount > 0 ? `${uncertainCount} item(s) need outcome review.` : '',
        failedCount > 0 ? `${failedCount} item(s) failed. Open their details to check the outcome.` : '',
      ].filter(Boolean).join(' '), 'warning');
    }
  }, [uncertainCount, failedCount, showToast]);
  const activeStatuses = new Set(['running', 'preparing']);
  const attentionStatuses = new Set(['failed', 'failed_before_publish', 'failed_after_publish', 'uncertain', 'needs_review']);
  const finishedStatuses = new Set(['published', 'completed']);
  // Index once so large queues do not repeatedly scan every execution for each account.
  const batchGroups = useMemo(() => {
    const allByBatch = new Map<string, QueueExecutionItem[]>();
    for (const item of executions) {
      const key = item.batch_id || '';
      const items = allByBatch.get(key) || [];
      items.push(item);
      allByBatch.set(key, items);
    }
    const byBatch = new Map<string, QueueExecutionItem[]>();
    for (const item of filteredExecutions) {
      const key = item.batch_id || '';
      const items = byBatch.get(key) || [];
      items.push(item);
      byBatch.set(key, items);
    }
    return batches.map((batch) => {
      const batchExecutions = byBatch.get(batch.batch_id) || [];
      const allBatchExecutions = allByBatch.get(batch.batch_id) || [];
      const byProfile = new Map<string, QueueExecutionItem[]>();
      for (const item of batchExecutions) {
        const items = byProfile.get(item.profile_id) || [];
        items.push(item);
        byProfile.set(item.profile_id, items);
      }
      const profileIds = [...new Set([
        ...(batch.profile_execution_order || batch.target_profiles || []),
        ...byProfile.keys(),
      ])];
      const profileGroups = profileIds.map((profileId) => ({
        profileId,
        executions: byProfile.get(profileId) || [],
      })).filter((group) => group.executions.length > 0);
      return {
        batch,
        executions: batchExecutions,
        total: allBatchExecutions.length,
        profileGroups,
        published: allBatchExecutions.filter((item) => finishedStatuses.has(item.status)).length,
        active: allBatchExecutions.filter((item) => activeStatuses.has(item.status)).length,
        attention: allBatchExecutions.filter((item) => attentionStatuses.has(item.status)).length,
        pending: allBatchExecutions.filter((item) => item.status === 'pending' || item.status === 'ready').length,
      };
    }).filter((group) => group.executions.length > 0).sort((left, right) => {
      const rank = (group: typeof left) => (
        group.active > 0 ? 0 : group.attention > 0 ? 1 : group.pending > 0 ? 2 : 3
      );
      return rank(left) - rank(right);
    });
  }, [queueData, filterStatus, profiles]);
  const visibleBatchPage = Math.min(batchPage, Math.max(1, Math.ceil(batchGroups.length / 12)));

  return (
    <div className="space-y-3 max-w-7xl mx-auto min-w-0">
      {/* Top Action Error Alert */}
      {/* ============================================================
          ZONE 1: COMPACT STATUS BAR (Clickable Filters)
          ============================================================ */}
      <div className="p-2.5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {/* Total */}
          <button
            type="button"
            onClick={() => setFilterStatus('all')}
            className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
              filterStatus === 'all'
                ? 'bg-zinc-800 text-white border border-zinc-700 font-semibold shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
            }`}
          >
            <span>{postTypeFilter === 'live' ? 'All broadcasts' : 'All Posts'}</span>
            <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-zinc-950/80 text-zinc-300">
              {stats.total}
            </span>
          </button>

          {/* Running */}
          <button
            type="button"
            onClick={() => setFilterStatus(filterStatus === 'running' ? 'all' : 'running')}
            className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
              filterStatus === 'running'
                ? 'bg-blue-950/80 text-blue-300 border border-blue-700 font-semibold shadow-sm'
                : 'text-blue-400 hover:bg-blue-950/40'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-blue-500 " />
            <span>Running</span>
            <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-blue-950/90 text-blue-300">
              {stats.running}
            </span>
          </button>

          {/* Pending */}
          <button
            type="button"
            onClick={() => setFilterStatus(filterStatus === 'pending' ? 'all' : 'pending')}
            className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
              filterStatus === 'pending'
                ? 'bg-zinc-800 text-zinc-200 border border-zinc-600 font-semibold shadow-sm'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60'
            }`}
          >
            <Clock className="w-3.5 h-3.5 text-zinc-500" />
            <span>Pending</span>
            <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-zinc-950/80 text-zinc-400">
              {stats.pending}
            </span>
          </button>

          {/* Published */}
          <button
            type="button"
            onClick={() => setFilterStatus(filterStatus === 'published' ? 'all' : 'published')}
            className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
              filterStatus === 'published'
                ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-700 font-semibold shadow-sm'
                : 'text-emerald-400 hover:bg-emerald-950/40'
            }`}
          >
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            <span>Published</span>
            <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-emerald-950/90 text-emerald-300">
              {stats.published}
            </span>
          </button>

          {/* Completed warming sessions */}
          {(stats.completed || 0) > 0 && (
            <button
              type="button"
              onClick={() => setFilterStatus(filterStatus === 'completed' ? 'all' : 'completed')}
              className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
                filterStatus === 'completed'
                  ? 'bg-orange-950/80 text-orange-300 border border-orange-700 font-semibold shadow-sm'
                  : 'text-orange-400 hover:bg-orange-950/40'
              }`}
            >
              <Flame className="w-3.5 h-3.5 text-orange-400" />
              <span>Warmed</span>
              <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-orange-950/90 text-orange-300">
                {stats.completed || 0}
              </span>
            </button>
          )}

          {/* Failed */}
          {failedCount > 0 && (
            <button
              type="button"
              onClick={() => setFilterStatus(filterStatus === 'failed' ? 'all' : 'failed')}
              className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
                filterStatus === 'failed'
                  ? 'bg-red-950 text-red-200 border border-red-700 font-semibold shadow-sm'
                  : 'text-red-400 hover:bg-red-950/40'
              }`}
            >
              <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
              <span>Failed</span>
              <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-red-950/90 text-red-300 font-bold">
                {failedCount}
              </span>
            </button>
          )}

          {/* Uncertain */}
          {uncertainCount > 0 && (
            <button
              type="button"
              onClick={() => setFilterStatus(filterStatus === 'uncertain' ? 'all' : 'uncertain')}
              className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 ${
                filterStatus === 'uncertain'
                  ? 'bg-amber-950 text-amber-200 border border-amber-600 font-semibold shadow-sm'
                  : 'text-amber-400 hover:bg-amber-950/40'
              }`}
            >
              <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
              <span>Needs Review</span>
              <span className="font-mono text-[11px] px-1.5 py-0.2 rounded bg-amber-900/80 text-amber-200 font-bold">
                {uncertainCount}
              </span>
            </button>
          )}
        </div>

        {/* Right side controls: Refresh */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => openReportDialog(null)}
            className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs font-medium flex items-center gap-1.5 transition-colors shadow-sm"
            title="Export sanitized diagnostics when the problem is not tied to one execution"
          >
            <Download className="w-3.5 h-3.5 text-cyan-400" />
            <span>Export diagnostics</span>
          </button>
          <button
            type="button"
            onClick={loadQueue}
            disabled={loading}
            className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-zinc-300 text-xs font-medium flex items-center gap-1.5 transition-colors shadow-sm"
            title="Refresh queue status"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-blue-400' : 'text-zinc-400'}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* ============================================================
          ZONE 4: BATCHES -> PROFILE LANES -> EXECUTION DETAILS
          ============================================================ */}
      <div className="p-3 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold text-zinc-200 uppercase tracking-wider flex items-center gap-2">
            <Layers3 className="w-4 h-4 text-emerald-400" />
            {batchGroups.length} batches <span className="text-zinc-500 font-normal normal-case tracking-normal">· {filteredExecutions.length} matching {postTypeFilter === 'live' ? 'broadcasts' : 'posts'}</span>
          </h4>

          {filterStatus !== 'all' ? (
            <button
              type="button"
              onClick={() => {
                setFilterStatus('all');
              }}
              className="text-xs text-blue-400 hover:text-blue-300 font-medium"
            >
              Reset Filters
            </button>
          ) : null}
        </div>

        {filteredExecutions.length === 0 ? (
          <div className="p-12 text-center text-xs text-zinc-500 border border-dashed border-zinc-800/80 rounded-xl">
            {loading && !queueData
              ? 'Loading queue…'
              : executions.length === 0
                ? postTypeFilter === 'live' ? 'Your Live queue is empty. Create a broadcast on New broadcast.' : 'Your queue is empty. Create a daily batch to schedule posts.'
                : 'No posts match these filters. Try a different search or reset the filters.'}
          </div>
        ) : (
          <div className="space-y-2">
            {batchGroups.slice((visibleBatchPage - 1) * 12, visibleBatchPage * 12).map((group) => {
              const batchId = group.batch.batch_id;
              const batchExpanded = expandedBatchId === batchId;
              const visibleProfilePage = Math.min(profilePage, Math.max(1, Math.ceil(group.profileGroups.length / 12)));
              const batchStatus = group.attention > 0
                ? 'Needs attention'
                : group.active > 0
                  ? 'Running'
                  : group.pending > 0
                    ? (queueData?.scheduler?.batch_owner_id && queueData.scheduler.batch_owner_id !== batchId
                      ? 'Waiting for active batch'
                      : 'Queued')
                    : 'Completed';
              return (
                <section
                  key={batchId}
                  className={`rounded-xl bg-zinc-950/55 border overflow-hidden ${batchExpanded ? 'border-blue-800/60' : 'border-zinc-800/90'}`}
                >
                  <div className="flex items-center gap-2.5 px-3 py-2.5 bg-zinc-900/70">
                    <button
                      type="button"
                      aria-expanded={batchExpanded}
                      onClick={() => {
                        setExpandedBatchId(batchExpanded ? null : batchId);
                        setExpandedProfileKey(null);
                        setExpandedExecutionId(null);
                        setProfilePage(1);
                        setExecutionPage(1);
                      }}
                      className="flex-1 min-w-0 text-left flex items-center gap-3"
                    >
                      {batchExpanded
                        ? <ChevronDown className="w-4 h-4 text-zinc-400 shrink-0" />
                        : <ChevronRight className="w-4 h-4 text-zinc-400 shrink-0" />}
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2 min-w-0">
                          <span className="text-sm font-semibold text-white truncate max-w-[240px] sm:max-w-[360px]" title={group.batch.name}>{group.batch.name}</span>
                          <span className={`px-2 py-0.5 rounded text-[10px] font-semibold border ${
                            group.attention > 0
                              ? 'bg-amber-950 text-amber-300 border-amber-800'
                              : group.active > 0
                                ? 'bg-blue-950 text-blue-300 border-blue-800'
                                : group.pending > 0
                                  ? 'bg-zinc-900 text-zinc-400 border-zinc-700'
                                  : 'bg-emerald-950 text-emerald-300 border-emerald-800'
                          }`}>{batchStatus}</span>
                          <span className="text-[10px] text-zinc-500">
                            {group.batch.schedule_window?.start_now ? 'Sequential · now' : 'Sequential · scheduled'}
                          </span>
                        </div>
                        <div className="sm:hidden mt-1 text-[10px] text-zinc-500">{group.published}/{group.total} done · {group.profileGroups.length} accounts{group.active > 0 ? ` · ${group.active} active` : ''}</div>
                      </div>
                      <div className="hidden sm:flex items-center gap-4 text-[11px] text-zinc-400 shrink-0">
                        <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5" /> {group.profileGroups.length}</span>
                        <span>{group.published}/{group.total} done</span>
                        {group.active > 0 && <span className="text-blue-300">{group.active} active</span>}
                        {group.attention > 0 && <span className="text-amber-300">{group.attention} attention</span>}
                      </div>
                    </button>
                    {postTypeFilter !== 'live' && group.attention === 0
                      && (group.active > 0 || group.pending > 0) && (
                      <button
                        type="button"
                        onClick={() => setAppendBatch(group.batch)}
                        className="px-2.5 py-1.5 rounded-lg bg-emerald-950/80 hover:bg-emerald-900 text-emerald-300 border border-emerald-800/80 text-[11px] font-semibold flex items-center gap-1.5 shrink-0"
                        title="Append posts to the end of this running batch"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        <span className="hidden sm:inline">Add posts</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleDeleteBatch(batchId)}
                      className="p-2 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-zinc-800 transition-colors shrink-0"
                      title="Remove this batch"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>

                  {batchExpanded && (
                    <div className="border-t border-zinc-800/70 p-3 space-y-2">
                      <div className="flex items-center justify-between gap-2 text-[11px] text-zinc-500 px-1">
                        <span>Accounts in execution order</span>
                        <span>{group.published} done · {group.pending} pending · {group.total} total · {group.executions.length} matching</span>
                      </div>
                      {group.profileGroups.slice((visibleProfilePage - 1) * 12, visibleProfilePage * 12).map((profileGroup) => {
                        const profileKey = `${batchId}:${profileGroup.profileId}`;
                        const profileActive = profileGroup.executions.filter((item) => activeStatuses.has(item.status)).length;
                        const profileReady = profileGroup.executions.filter((item) => item.status === 'ready').length;
                        const profileAttention = profileGroup.executions.filter((item) => attentionStatuses.has(item.status)).length;
                        const profileDone = profileGroup.executions.filter((item) => finishedStatuses.has(item.status)).length;
                        const profileExpanded = expandedProfileKey === profileKey;
                        const visibleExecutionPage = Math.min(executionPage, Math.max(1, Math.ceil(profileGroup.executions.length / 20)));
                        return (
                          <div
                            key={profileKey}
                            className="rounded-lg border border-zinc-800/80 bg-black/20 overflow-hidden"
                          >
                            <button
                              type="button"
                              aria-expanded={profileExpanded}
                              onClick={() => {
                                setExpandedProfileKey(profileExpanded ? null : profileKey);
                                setExpandedExecutionId(null);
                                setExecutionPage(1);
                              }}
                              className="w-full px-3 py-2.5 flex items-center gap-2.5 text-left hover:bg-zinc-900/70 transition-colors"
                            >
                              {profileExpanded
                                ? <ChevronDown className="w-3.5 h-3.5 text-zinc-500" />
                                : <ChevronRight className="w-3.5 h-3.5 text-zinc-500" />}
                              <span className="text-xs font-semibold text-zinc-200 flex-1 truncate">
                                {getProfileName(profileGroup.profileId)}
                              </span>
                              <span className="text-[10px] text-zinc-500">
                                {profileDone}/{profileGroup.executions.length} done
                              </span>
                              {profileActive > 0 && (
                                <span className="px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800 text-[10px] font-semibold">
                                  {profileActive} active
                                </span>
                              )}
                              {profileReady > 0 && (
                                <span className="px-2 py-0.5 rounded bg-cyan-950 text-cyan-300 border border-cyan-800 text-[10px] font-semibold" title="Preparation completed; waiting for a publishing slot. The profile may be stopped until publishing starts.">
                                  {profileReady} ready · waiting
                                </span>
                              )}
                              {profileAttention > 0 && (
                                <span className="px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 text-[10px] font-semibold">
                                  {profileAttention} attention
                                </span>
                              )}
                            </button>

                            {profileExpanded && (
                              <div className="border-t border-zinc-800/70 divide-y divide-zinc-800/60">
                                {profileGroup.executions.slice((visibleExecutionPage - 1) * 20, visibleExecutionPage * 20).map((item) => (
              <div
                key={item.execution_id}
                className="px-3 py-3 bg-zinc-950/40 hover:bg-zinc-900/40 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto] items-center gap-3 group"
              >
                {/* Left group: Time, Media Thumbnail, Details */}
                <div className="flex items-center gap-3 min-w-0">
                  {/* Scheduled Time Stamp */}
                  <div className="text-center font-mono shrink-0 w-14">
                    <div className="text-xs font-bold text-white whitespace-nowrap">
                      {formatScheduledTime(item.scheduled_at)}
                    </div>
                    <div className="text-[10px] text-zinc-500">
                      {new Date(item.scheduled_at).toLocaleDateString([], { month: 'numeric', day: 'numeric' })}
                    </div>
                  </div>

                  <div className="w-px h-8 bg-zinc-800 shrink-0" />

                  {/* Media Thumbnail */}
                  {item.media_file ? (
                    <div
                      onClick={() =>
                        setLightboxMedia({
                          url: getSharedMediaUrl(item.media_file),
                          type: item.post_type === 'reel' || item.post_type === 'live' ? 'reel' : 'photo',
                          name: item.media_file || 'Media',
                          caption: item.spun_caption || item.base_caption,
                          comment: item.first_comment || undefined,
                        })
                      }
                      className="shrink-0 w-10 h-10 rounded-lg overflow-hidden border border-zinc-750 hover:border-blue-500 cursor-pointer shadow relative group/thumb bg-black flex items-center justify-center transition-all"
                      title="Click for full size preview"
                    >
                      {item.post_type === 'reel' || item.post_type === 'live' ? (
                        <>
                          <video
                            src={getSharedMediaUrl(item.media_file)}
                            className="w-full h-full object-cover opacity-80"
                            preload="none"
                          />
                          <div className="absolute inset-0 flex items-center justify-center bg-black/40 group-hover/thumb:bg-black/20">
                            <Play className="w-4 h-4 text-white fill-white drop-shadow" />
                          </div>
                        </>
                      ) : (
                        <img
                          src={getSharedMediaUrl(item.media_file)}
                          alt="preview"
                          loading="lazy"
                          className="w-full h-full object-cover"
                          onError={(e) => {
                            (e.target as HTMLElement).style.display = 'none';
                          }}
                        />
                      )}
                    </div>
                  ) : item.post_type === 'warming' ? (
                    <div className="w-10 h-10 rounded-lg border border-orange-900/70 bg-orange-950/30 flex items-center justify-center text-orange-400 shrink-0">
                      <Flame className="w-5 h-5" />
                    </div>
                  ) : (
                    <div className="w-10 h-10 rounded-lg border border-dashed border-zinc-800 bg-zinc-900/40 flex items-center justify-center text-zinc-600 text-[10px] shrink-0 font-medium">
                      Text
                    </div>
                  )}

                  {/* Text Details: Profile, Type, Caption, 1st Comment */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-zinc-200 truncate">
                        {getProfileName(item.profile_id)}
                      </span>
                      {item.post_type === 'warming' ? (
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-orange-950/80 text-orange-300 border border-orange-800/60 flex items-center gap-0.5 shrink-0">
                          <Flame className="w-2.5 h-2.5" /> Warming
                        </span>
                      ) : item.post_type === 'reel' || item.post_type === 'live' ? (
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-purple-950/80 text-purple-300 border border-purple-800/60 flex items-center gap-0.5 shrink-0">
                          <Film className="w-2.5 h-2.5" /> {item.post_type === 'live' ? 'Live' : 'Reel'}
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-blue-950/80 text-blue-300 border border-blue-800/60 flex items-center gap-0.5 shrink-0">
                          <ImageIcon className="w-2.5 h-2.5" /> Photo
                        </span>
                      )}
                    </div>

                    <div className="text-xs text-zinc-400 truncate mt-0.5">
                      {item.post_type === 'live' ? `${item.stage || item.status} · ${Math.floor((item.elapsed_seconds || 0) / 60)}:${String(Math.floor((item.elapsed_seconds || 0) % 60)).padStart(2, '0')} / ${Math.floor((item.duration_seconds || 0) / 60)}:${String(Math.floor((item.duration_seconds || 0) % 60)).padStart(2, '0')}` : item.post_type === 'warming'
                        ? `${item.warming_surface ? (item.warming_surface === 'profile' ? 'Profile' : 'News Feed') : (item.warming_options?.surface === 'profile' ? 'Profile' : item.warming_options?.surface === 'news_feed' ? 'News Feed' : 'Feed / Profile')} · ${item.warming_scroll_actions != null ? `${item.warming_scroll_actions} scrolls` : item.warming_options?.random_scrolls ? `${item.warming_options.min_scrolls}–${item.warming_options.max_scrolls} cycles` : `${item.scrolls ?? 4} cycles`}`
                        : (item.spun_caption || item.base_caption || 'No caption')}
                    </div>

                    {expandedExecutionId === item.execution_id && item.first_comment && (
                      <div className="text-[10px] text-zinc-500 truncate max-w-[340px]">
                        💬 {item.first_comment}
                      </div>
                    )}
                  </div>
                </div>

                {/* Right group: Badges & Action Buttons */}
                <div className="flex flex-wrap items-center gap-2 ml-auto">
                  {getStatusBadge(item.status)}
                  <button
                    type="button"
                    aria-expanded={expandedExecutionId === item.execution_id}
                    onClick={() => setExpandedExecutionId(expandedExecutionId === item.execution_id ? null : item.execution_id)}
                    className="px-2 py-1 rounded-lg text-xs text-zinc-400 hover:text-white hover:bg-zinc-800 flex items-center gap-1"
                  >
                    {expandedExecutionId === item.execution_id ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                    Details
                  </button>
                  {expandedExecutionId === item.execution_id && <>

                  {item.warming_surface && (
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold border flex items-center gap-1 ${
                        item.warming_surface_fallback
                          ? 'bg-amber-950 text-amber-300 border-amber-800/80'
                          : 'bg-violet-950 text-violet-300 border-violet-800/80'
                      }`}
                      title={[
                        `Requested: ${item.warming_surface_requested === 'profile' ? 'Profile' : 'News Feed'}`,
                        `Used: ${item.warming_surface === 'profile' ? 'Profile' : 'News Feed'}`,
                        item.warming_surface_fallback ? 'Fallback was required' : 'Primary selection loaded successfully',
                        item.warming_duration_seconds != null ? `Duration: ${item.warming_duration_seconds.toFixed(1)} seconds` : null,
                        item.warming_scroll_actions != null ? `Scroll actions: ${item.warming_scroll_actions}` : null,
                      ].filter(Boolean).join(' · ')}
                    >
                      <Flame className="w-3 h-3" />
                      {item.warming_surface === 'profile' ? 'Profile warm-up' : 'Feed warm-up'}
                      {item.warming_surface_fallback ? ' · fallback' : ''}
                    </span>
                  )}

                  {item.first_comment_status === 'submitted_verified' && (
                    <span
                      className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-300 border border-emerald-800/80 flex items-center gap-1"
                      title="The first comment was visibly verified after submission."
                    >
                      <MessageCircle className="w-3 h-3 text-emerald-400" /> Comment verified
                    </span>
                  )}

                  {['submitted_unverified', 'submission_pending'].includes(item.first_comment_status || '') && (
                    <span
                      className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-950 text-amber-300 border border-amber-800/80 flex items-center gap-1"
                      title="A comment submission may have occurred. Review it before taking any further action."
                    >
                      <AlertTriangle className="w-3 h-3 text-amber-400" />
                      {item.first_comment_status === 'submission_pending' ? 'Comment pending' : 'Review comment'}
                    </span>
                  )}

                  {item.first_comment_status === 'failed_input_not_found'
                    && [null, undefined, 'failed_to_start', 'failed_before_submission', 'running'].includes(item.comment_retry_status)
                    && (
                    <button
                      type="button"
                      onClick={() => handleCommentRetry(item)}
                      disabled={item.comment_retry_status === 'running' || retryingCommentId === item.execution_id}
                      className="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-950 hover:bg-red-900 disabled:opacity-60 disabled:cursor-not-allowed text-red-300 border border-red-800/80 flex items-center gap-1 transition-all"
                      title="No comment was submitted because the input was not found. Retry the comment only."
                    >
                      {item.comment_retry_status === 'running' || retryingCommentId === item.execution_id
                        ? <Loader2 className="w-3 h-3 animate-spin" />
                        : <MessageCircle className="w-3 h-3" />}
                      {item.comment_retry_status === 'running' ? 'Retrying comment' : 'Retry comment'}
                    </button>
                  )}

                  {item.post_url && item.permalink_status === 'recovered' && (
                    <span
                      className="px-2 py-0.5 rounded text-[10px] font-semibold bg-cyan-950 text-cyan-300 border border-cyan-800/80 flex items-center gap-1"
                      title="The initial permalink check missed this URL; it was captured by the bounded recovery pass."
                    >
                      <Search className="w-3 h-3 text-cyan-400" /> Recovered link
                    </span>
                  )}

                  {!item.post_url && ['missing_after_recovery', 'rejected_invalid'].includes(item.permalink_status || '') && (
                    <span
                      className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-950 text-amber-300 border border-amber-800/80 flex items-center gap-1"
                      title={item.permalink_status === 'rejected_invalid'
                        ? 'Automation returned a URL that failed manager validation.'
                        : 'The bounded permalink recovery pass completed without a confident URL.'}
                    >
                      <AlertTriangle className="w-3 h-3 text-amber-400" /> Link unavailable
                    </span>
                  )}

                  {/* View on Facebook link */}
                  {item.post_url && (
                    <a
                      href={item.post_url}
                      target="_blank"
                      rel="noreferrer"
                      className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-blue-950/80 hover:bg-blue-900 text-blue-300 hover:text-blue-100 border border-blue-800/80 flex items-center gap-1.5 transition-all shadow-sm group"
                      title="View published post on Facebook"
                    >
                      <ExternalLink className="w-3 h-3 text-blue-400 group-hover:scale-110 transition-transform" />
                      <span>View on FB</span>
                    </a>
                  )}

                  {/* Add verified link button (if published but url missing) */}
                  {item.status === 'published' && !item.post_url && (
                    <button
                      type="button"
                      onClick={() => handlePermalinkBackfill(item)}
                      className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-950/80 hover:bg-amber-900 text-amber-300 border border-amber-800/80 flex items-center gap-1.5 transition-all"
                      title="Attach verified Facebook permalink"
                    >
                      <ExternalLink className="w-3 h-3" />
                      <span>Add link</span>
                    </button>
                  )}

                  </>}

                  {item.post_type === 'live' && item.status === 'running' && <button type="button"
                    disabled={stoppingLiveId === item.execution_id || ['stopping', 'ending', 'ended'].includes(item.stage || '')}
                    onClick={async () => {
                      setStoppingLiveId(item.execution_id); setActionError(null);
                      try { await stopLive(item.profile_id, item.execution_id); await loadQueue(); }
                      catch (error) { setActionError(error instanceof Error ? error.message : 'Could not stop Live'); }
                      finally { setStoppingLiveId(null); }
                    }} className="rounded-lg border border-rose-800 bg-rose-950 px-3 py-1 text-xs text-rose-200 disabled:opacity-50">Stop Live</button>}

                  {/* Run Now button for pending items */}
                  {item.status === 'pending' && (
                    <button
                      type="button"
                      onClick={() => handleRunNow(item.execution_id)}
                      className="px-3 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white flex items-center gap-1 transition-colors shadow-sm"
                      title="Execute immediately without waiting for scheduled time"
                    >
                      <Play className="w-3 h-3 fill-current" />
                      <span>Run Now</span>
                    </button>
                  )}

                  {/* Review & Resolve button for Phase 0 safety gated items */}
                  {(item.status === 'uncertain' || item.status === 'needs_review') && (
                    <button
                      type="button"
                      onClick={() => {
                        setResolvingItem(item);
                        setResolveNote('');
                        setResolvePostUrl(item.post_url || '');
                      }}
                      className="px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors shadow-sm animate-pulse hover:animate-none"
                      title="Review state on Facebook and confirm publication outcome"
                    >
                      <ShieldAlert className="w-3.5 h-3.5" />
                      <span>Review & Resolve</span>
                    </button>
                  )}

                  {/* Re-run button for every non-active retryable outcome */}
                  {!(item.post_type === 'live' && ['uncertain', 'needs_review'].includes(item.status)) && (item.status === 'failed' || item.status === 'failed_before_publish' || item.status === 'uncertain' || item.status === 'needs_review' || item.status.startsWith('skipped')) && (
                    <button
                      type="button"
                      onClick={() => handleRunNow(item.execution_id)}
                      className="px-3 py-1 rounded-lg bg-rose-600/90 hover:bg-rose-500 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors shadow-sm"
                      title="Re-run this post now"
                    >
                      <RotateCcw className="w-3 h-3" />
                      <span>Re-run</span>
                    </button>
                  )}

                  {expandedExecutionId === item.execution_id && <>
                  <button
                    type="button"
                    onClick={() => openReportDialog(item)}
                    className="p-1.5 rounded-lg text-zinc-500 hover:text-cyan-300 hover:bg-cyan-950/40 transition-colors"
                    title="Report a problem with this execution"
                  >
                    <Bug className="w-3.5 h-3.5" />
                  </button>

                  {/* Delete button remains locked while active or unresolved. */}
                  {!['running', 'uncertain', 'needs_review'].includes(item.status) ? (
                    <button
                      type="button"
                      onClick={() => handleDeleteExecution(item.execution_id)}
                      className="p-1.5 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-zinc-800 transition-colors"
                      title="Remove from queue"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  ) : (
                    <span
                      className="p-1.5 text-zinc-600 cursor-not-allowed"
                      title="Cannot delete while execution is running or unresolved"
                    >
                      <Lock className="w-3.5 h-3.5" />
                    </span>
                  )}
                  </>}
                </div>
                {expandedExecutionId === item.execution_id && (
                  <div className="col-span-full w-full rounded-lg bg-zinc-900/70 p-3 text-xs space-y-2 text-zinc-400 break-words">
                    <div className="flex flex-wrap gap-x-6 gap-y-1">
                      <span>Stage: <span className="text-zinc-200">{item.stage || 'Not started'}</span></span>
                      <span>Retries: <span className="text-zinc-200">{item.retry_count || 0}</span></span>
                    </div>
                    {item.media_file && <p>Media: <span className="text-zinc-200">{item.media_file}</span></p>}
                    {item.post_type === 'live' && item.title && <p className="font-medium text-zinc-200">{item.title}</p>}
                    {item.post_type === 'live' && item.pinned_comment && <p className="whitespace-pre-wrap text-zinc-400">Pinned comment: {item.pinned_comment}</p>}
                    {(item.spun_caption || item.base_caption) && <p className="whitespace-pre-wrap text-zinc-200">{item.spun_caption || item.base_caption}</p>}
                    {item.error && <p className="text-red-300">{item.error}</p>}
                    <p className="text-[10px] font-mono text-zinc-600">{item.execution_id}</p>
                  </div>
                )}
                                  </div>
                                ))}
                                <QueuePagination page={visibleExecutionPage} total={profileGroup.executions.length} pageSize={20} label="posts" onChange={(page) => { setExecutionPage(page); setExpandedExecutionId(null); }} />
                              </div>
                            )}
                          </div>
                        );
                      })}
                      <QueuePagination page={visibleProfilePage} total={group.profileGroups.length} pageSize={12} label="accounts" onChange={(page) => { setProfilePage(page); setExpandedProfileKey(null); }} />
                    </div>
                  )}
                </section>
              );
            })}
            <QueuePagination page={visibleBatchPage} total={batchGroups.length} pageSize={12} label="batches" onChange={(page) => { setBatchPage(page); setExpandedBatchId(null); }} />
          </div>
        )}
      </div>

      {appendBatch && (
        <AppendBatchPostsDialog
          batch={appendBatch}
          onClose={() => setAppendBatch(null)}
          onAppended={loadQueue}
        />
      )}

      {/* ============================================================
          SANITIZED SUPPORT REPORT MODAL
          ============================================================ */}
      {reportDialogOpen && (
        <div
          className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4 sm:p-6"
          onClick={() => !isGeneratingReport && setReportDialogOpen(false)}
        >
          <div
            className="relative max-w-lg w-full bg-zinc-950 border border-cyan-900/70 rounded-2xl overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-150"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-4 border-b border-zinc-800 bg-cyan-950/20">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
                  <Bug className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-white">
                    {reportingItem ? 'Report This Execution' : 'Export Diagnostics'}
                  </h3>
                  <p className="text-[11px] text-zinc-400">
                    Creates a local ZIP to send to product support.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => !isGeneratingReport && setReportDialogOpen(false)}
                disabled={isGeneratingReport}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 disabled:opacity-50 transition-colors"
                title="Close dialog"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-4 space-y-4">
              {reportingItem && (
                <div className="p-3 rounded-xl bg-zinc-900/70 border border-zinc-800 text-xs space-y-1.5">
                  <div className="flex justify-between gap-4">
                    <span className="text-zinc-500">Execution</span>
                    <span className="font-mono text-[10px] text-zinc-300 break-all">{reportingItem.execution_id}</span>
                  </div>
                  <div className="flex justify-between gap-4">
                    <span className="text-zinc-500">Profile</span>
                    <span className="text-zinc-200">{getProfileName(reportingItem.profile_id)}</span>
                  </div>
                  <div className="flex justify-between gap-4">
                    <span className="text-zinc-500">Status</span>
                    <span className="text-zinc-200">{reportingItem.status} · {reportingItem.stage || 'unknown stage'}</span>
                  </div>
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-zinc-300 mb-1.5">
                  What happened? <span className="text-zinc-600 font-normal">(optional)</span>
                </label>
                <textarea
                  value={reportDescription}
                  onChange={(e) => setReportDescription(e.target.value.slice(0, 4000))}
                  rows={4}
                  placeholder="Example: The Reel published, but the first comment did not appear."
                  className="w-full px-3 py-2.5 rounded-xl bg-black/50 border border-zinc-800 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-cyan-600 resize-y"
                />
                <div className="text-right text-[10px] text-zinc-600 mt-1">{reportDescription.length}/4000</div>
              </div>

              {reportingItem && (
                <div className="space-y-2.5">
                  <label htmlFor="report-include-evidence" className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-900/50 border border-zinc-800 cursor-pointer hover:border-zinc-700">
                    <Checkbox
                      id="report-include-evidence"
                      checked={reportIncludeEvidence}
                      onCheckedChange={setReportIncludeEvidence}
                      className="mt-0.5 data-[state=checked]:border-cyan-500 data-[state=checked]:bg-cyan-600"
                    />
                    <span>
                      <span className="block text-xs font-medium text-zinc-200">Include relevant evidence screenshots</span>
                      <span className="block text-[10px] text-amber-400/90 mt-0.5">
                        Optional and off by default. Screenshots may show Facebook page content or account names.
                      </span>
                    </span>
                  </label>
                  <label htmlFor="report-include-content" className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-900/50 border border-zinc-800 cursor-pointer hover:border-zinc-700">
                    <Checkbox
                      id="report-include-content"
                      checked={reportIncludeContent}
                      onCheckedChange={setReportIncludeContent}
                      className="mt-0.5 data-[state=checked]:border-cyan-500 data-[state=checked]:bg-cyan-600"
                    />
                    <span>
                      <span className="block text-xs font-medium text-zinc-200">Include caption, comment, media filename, and permalink</span>
                      <span className="block text-[10px] text-zinc-500 mt-0.5">
                        Useful for content-matching problems; excluded by default.
                      </span>
                    </span>
                  </label>
                </div>
              )}

              <div className="p-3 rounded-xl bg-emerald-950/20 border border-emerald-900/60 text-[11px] text-emerald-200/80 leading-relaxed">
                Browser data, cookies, passwords, proxy credentials, access tokens, and email addresses are never included. Logs and JSON metadata are sanitized automatically.
              </div>
            </div>

            <div className="p-4 border-t border-zinc-800 bg-zinc-900/60 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setReportDialogOpen(false)}
                disabled={isGeneratingReport}
                className="px-4 py-2 rounded-xl text-xs font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDownloadReport}
                disabled={isGeneratingReport}
                className="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-cyan-600 hover:bg-cyan-500 disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2 transition-colors shadow-sm"
              >
                {isGeneratingReport ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                {isGeneratingReport ? 'Creating ZIP…' : 'Download Support ZIP'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================
          MEDIA LIGHTBOX MODAL
          ============================================================ */}
      {lightboxMedia && (
        <div
          className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4 sm:p-6"
          onClick={() => setLightboxMedia(null)}
        >
          <div
            className="relative max-w-4xl w-full max-h-[90vh] bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-150"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Lightbox Header */}
            <div className="flex items-center justify-between p-3.5 border-b border-zinc-800 bg-zinc-900/80">
              <div className="flex items-center gap-2.5">
                {lightboxMedia.type === 'reel' ? (
                  <span className="px-2.5 py-0.5 rounded text-[11px] font-semibold bg-purple-950 text-purple-300 border border-purple-800/80 flex items-center gap-1.5">
                    <Film className="w-3.5 h-3.5 text-purple-400" /> Reel Preview
                  </span>
                ) : (
                  <span className="px-2.5 py-0.5 rounded text-[11px] font-semibold bg-blue-950 text-blue-300 border border-blue-800/80 flex items-center gap-1.5">
                    <ImageIcon className="w-3.5 h-3.5 text-blue-400" /> Photo Preview
                  </span>
                )}
                <span className="text-xs text-zinc-300 font-mono truncate max-w-md">
                  {lightboxMedia.name}
                </span>
              </div>

              <button
                type="button"
                onClick={() => setLightboxMedia(null)}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                title="Close preview"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Lightbox Media Body */}
            <div className="flex-1 flex items-center justify-center bg-black/95 p-4 overflow-hidden min-h-[350px]">
              {lightboxMedia.type === 'reel' ? (
                <video
                  src={lightboxMedia.url}
                  controls
                  autoPlay
                  className="max-h-[65vh] max-w-full rounded-lg shadow-xl"
                />
              ) : (
                <img
                  src={lightboxMedia.url}
                  alt={lightboxMedia.name}
                  className="max-h-[65vh] max-w-full object-contain rounded-lg shadow-xl"
                />
              )}
            </div>

            {/* Lightbox Footer Info */}
            {(lightboxMedia.caption || lightboxMedia.comment) && (
              <div className="p-3.5 border-t border-zinc-800 bg-zinc-900/80 space-y-1.5 text-xs">
                {lightboxMedia.caption && (
                  <div className="text-zinc-200">
                    <strong className="text-zinc-400 font-medium">Caption:</strong>{' '}
                    <span>{lightboxMedia.caption}</span>
                  </div>
                )}
                {lightboxMedia.comment && (
                  <div className="text-blue-400 text-[11px]">
                    <strong className="text-zinc-400 font-medium">1st Comment:</strong>{' '}
                    <span>{lightboxMedia.comment}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============================================================
          REVIEW & RESOLVE UNCERTAIN MODAL (Phase 0 Safety Gate)
          ============================================================ */}
      {resolvingItem && (() => {
        const targetProfile = profiles.find((p) => p.id === resolvingItem.profile_id);
        const wsPort =
          targetProfile?.container?.ws_port ||
          (targetProfile?.container?.vnc_port ? targetProfile.container.vnc_port + 180 : null);
        const viewerHost = (!window.location.hostname || window.location.hostname === 'tauri.localhost' || window.location.protocol === 'tauri:')
          ? '127.0.0.1'
          : window.location.hostname;
        const novncUrl = wsPort ? `http://${viewerHost}:${wsPort}/vnc.html` : null;

        return (
          <div
            className="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4 sm:p-6"
            onClick={() => !isResolving && setResolvingItem(null)}
          >
            <div
              className="relative max-w-xl w-full bg-zinc-950 border border-amber-800/60 rounded-2xl overflow-hidden shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-150"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Modal Header */}
              <div className="flex items-center justify-between p-4 border-b border-zinc-800 bg-amber-950/20">
                <div className="flex items-center gap-2.5">
                  <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400">
                    <ShieldAlert className="w-5 h-5" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-white flex items-center gap-2">
                      Review Uncertain Execution
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-amber-900/60 text-amber-300 border border-amber-700/50">
                        Phase 0 Safety Gate
                      </span>
                    </h3>
                    <p className="text-[11px] text-zinc-400">
                      Publish action was dispatched, but completion could not be deterministically confirmed.
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => !isResolving && setResolvingItem(null)}
                  disabled={isResolving}
                  className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                  title="Close dialog"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Modal Body */}
              <div className="p-4 space-y-4 max-h-[75vh] overflow-y-auto">
                {/* Duplicate prevention notice */}
                <div className="p-3 bg-amber-950/30 border border-amber-800/50 rounded-xl text-xs text-amber-200/90 leading-relaxed flex items-start gap-2.5">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <strong className="text-amber-300">Unverified outcome:</strong> You may resolve the recorded result or close this dialog and re-run the execution directly.
                  </div>
                </div>

                {/* Execution Details Card */}
                <div className="bg-zinc-900/60 rounded-xl border border-zinc-800 p-3.5 space-y-2 text-xs">
                  <div className="flex justify-between items-center py-1 border-b border-zinc-800/60">
                    <span className="text-zinc-400">Target Profile:</span>
                    <span className="font-semibold text-white">{getProfileName(resolvingItem.profile_id)}</span>
                  </div>
                  <div className="flex justify-between items-center py-1 border-b border-zinc-800/60">
                    <span className="text-zinc-400">Execution ID:</span>
                    <span className="font-mono text-[10px] text-zinc-300">{resolvingItem.execution_id}</span>
                  </div>
                  <div className="flex justify-between items-center py-1 border-b border-zinc-800/60">
                    <span className="text-zinc-400">Last Recorded Stage:</span>
                    <span className="font-mono text-[11px] text-amber-300 font-semibold">
                      {resolvingItem.stage || 'publish_clicked'}
                    </span>
                  </div>
                  {resolvingItem.error && (
                    <div className="py-1">
                      <span className="text-zinc-400 block mb-1">Error / Timeout:</span>
                      <div className="p-2 rounded bg-black/60 border border-red-900/40 text-red-300 font-mono text-[11px] break-words">
                        {resolvingItem.error}
                      </div>
                    </div>
                  )}
                  {resolvingItem.media_file && (
                    <div className="flex justify-between items-center py-1 border-b border-zinc-800/60">
                      <span className="text-zinc-400">Media File:</span>
                      <span className="font-mono text-[11px] text-blue-300 truncate max-w-[280px]">
                        {resolvingItem.media_file}
                      </span>
                    </div>
                  )}
                  {(resolvingItem.spun_caption || resolvingItem.base_caption) && (
                    <div className="py-1">
                      <span className="text-zinc-400 block mb-0.5">Post Caption:</span>
                      <div className="text-[11px] text-zinc-300 line-clamp-2 italic bg-zinc-950/60 p-2 rounded border border-zinc-800/60">
                        "{resolvingItem.spun_caption || resolvingItem.base_caption}"
                      </div>
                    </div>
                  )}
                </div>

                {/* Step 1: Live noVNC inspection */}
                <div className="p-3.5 bg-blue-950/20 border border-blue-800/40 rounded-xl space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-blue-300 flex items-center gap-1.5">
                      <Eye className="w-3.5 h-3.5" /> Step 1: Inspect Facebook Feed via noVNC
                    </span>
                    {novncUrl ? (
                      <a
                        href={novncUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-500 text-[11px] font-semibold text-white flex items-center gap-1.5 transition-colors shadow-sm"
                      >
                        <span>Open Live noVNC</span>
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    ) : (
                      <span className="text-[10px] text-zinc-500">Container not running</span>
                    )}
                  </div>
                  <p className="text-[11px] text-zinc-400">
                    Open live container display in a new tab. Check whether the {resolvingItem.post_type || 'post'} appeared on the Facebook page/profile.
                  </p>
                </div>

                {/* Step 2: Operator Audit Note */}
                <div className="space-y-1.5">
                  <label className="text-xs font-semibold text-zinc-300 flex items-center justify-between">
                    <span>Step 2: Operator Audit Note (optional)</span>
                    <span className="text-[10px] text-zinc-500 font-normal">Audit Log</span>
                  </label>
                  <textarea
                    value={resolveNote}
                    onChange={(e) => setResolveNote(e.target.value)}
                    placeholder="e.g., Verified profile feed via noVNC — post is visible."
                    rows={2}
                    className="w-full px-3 py-2 text-xs rounded-xl bg-zinc-900 border border-zinc-700 text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500 resize-none"
                  />
                </div>

                {/* Step 3: Verified Post Permalink */}
                <div className="space-y-1.5">
                  <label className="text-xs font-semibold text-zinc-300 flex items-center justify-between">
                    <span>Step 3: Confirmed Post Permalink (optional)</span>
                    <span className="text-[10px] text-zinc-500 font-normal">Direct Link</span>
                  </label>
                  <input
                    type="url"
                    value={resolvePostUrl}
                    onChange={(e) => setResolvePostUrl(e.target.value)}
                    placeholder="https://www.facebook.com/.../posts/..."
                    className="w-full px-3 py-2 text-xs rounded-xl bg-zinc-900 border border-zinc-700 text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-amber-500 font-mono text-[11px]"
                  />
                </div>
              </div>

              {/* Modal Actions */}
              <div className="p-4 border-t border-zinc-800 bg-zinc-900/60 flex flex-wrap items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => setResolvingItem(null)}
                  disabled={isResolving}
                  className="px-3 py-1.5 rounded-lg border border-zinc-700 hover:bg-zinc-800 text-xs text-zinc-300 font-medium transition-colors"
                >
                  Cancel
                </button>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleResolveUncertain('not_published')}
                    disabled={isResolving}
                    className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-red-950 hover:text-red-200 border border-zinc-700 hover:border-red-700 text-xs font-semibold text-zinc-200 flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    title="Mark as not published. Unlocks retry as a safe pre-publish failure."
                  >
                    {isResolving ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <RotateCcw className="w-3.5 h-3.5 text-red-400" />
                    )}
                    <span>Confirmed NOT Published (Allow Retry)</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleResolveUncertain('published')}
                    disabled={isResolving}
                    className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors disabled:opacity-50 shadow-sm"
                    title="Mark as successfully published. Locks execution as published."
                  >
                    {isResolving ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Check className="w-3.5 h-3.5" />
                    )}
                    <span>Mark as Published</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
};

function QueuePagination({ page, total, pageSize, label, onChange }: {
  page: number; total: number; pageSize: number; label: string; onChange: (page: number) => void;
}) {
  const pages = Math.ceil(total / pageSize);
  if (pages <= 1) return null;
  return (
    <nav aria-label={`${label} pagination`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-3 text-xs text-zinc-500">
      <span>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total} {label}</span>
      <div className="flex items-center gap-3">
        <button type="button" disabled={page === 1} onClick={() => onChange(page - 1)} className="px-3 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed">Previous</button>
        <span>{page} / {pages}</span>
        <button type="button" disabled={page === pages} onClick={() => onChange(page + 1)} className="px-3 py-1.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-not-allowed">Next</button>
      </div>
    </nav>
  );
}
