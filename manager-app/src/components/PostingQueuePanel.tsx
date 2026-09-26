import React, { useState, useEffect } from 'react';
import { QueueDataResponse, QueueExecutionItem } from '../types/automation';
import { Profile } from '../types/profile';
import {
  backfillExecutionPermalink,
  fetchQueue,
  deleteBatch,
  deleteExecution,
  retryExecutionComment,
  runExecutionNow,
  resolveUncertainExecution,
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
  Calendar,
  Lock,
  X,
  RotateCcw,
  ShieldAlert,
  ExternalLink,
  Check,
  Eye,
  Search,
  MessageCircle,
} from 'lucide-react';

interface PostingQueuePanelProps {
  profiles: Profile[];
}

interface LightboxMedia {
  url: string;
  type: 'photo' | 'reel';
  name: string;
  caption?: string;
  comment?: string;
}

export const PostingQueuePanel: React.FC<PostingQueuePanelProps> = ({ profiles }) => {
  const [queueData, setQueueData] = useState<QueueDataResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [filterProfile, setFilterProfile] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [filterBatch, setFilterBatch] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);
  const [retryingCommentId, setRetryingCommentId] = useState<string | null>(null);

  // Phase 0 Safety Gate: Review & Resolve Uncertain State
  const [resolvingItem, setResolvingItem] = useState<QueueExecutionItem | null>(null);
  const [resolveNote, setResolveNote] = useState('');
  const [resolvePostUrl, setResolvePostUrl] = useState('');
  const [isResolving, setIsResolving] = useState(false);

  const loadQueue = async () => {
    setLoading(true);
    try {
      const data = await fetchQueue();
      setQueueData(data);
    } catch (err: any) {
      console.error('Failed to load queue:', err);
    } finally {
      setLoading(false);
    }
  };


  const handleResolveUncertain = async (resolution: 'published' | 'not_published') => {
    if (!resolvingItem) return;
    setIsResolving(true);
    setActionError(null);
    try {
      await resolveUncertainExecution(
        resolvingItem.execution_id,
        resolution,
        resolveNote.trim() || undefined,
        resolution === 'published' ? resolvePostUrl.trim() || undefined : undefined
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
  }, []);

  const handleRunNow = async (executionId: string) => {
    setActionError(null);
    try {
      await runExecutionNow(executionId);
      loadQueue();
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
    if (!confirm('Are you sure you want to remove this batch and all its pending executions?')) return;
    try {
      await deleteBatch(batchId);
      loadQueue();
    } catch (err: any) {
      setActionError(err.message || 'Failed to delete batch');
    }
  };

  const handlePermalinkBackfill = async (item: QueueExecutionItem) => {
    const postUrl = window.prompt('Paste the verified Facebook permalink for this published execution:');
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
    if (!confirm('Retry only the first comment? The published post will not be recreated.')) return;
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
      case 'uncertain':
      case 'needs_review':
        return (
          <span
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-950 text-amber-300 border border-amber-800/80 flex items-center gap-1 shadow-sm"
            title="Outcome uncertain. Rerun locked until operator review."
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
    if (filterProfile !== 'all' && execItem.profile_id !== filterProfile) return false;
    if (filterBatch !== 'all' && execItem.batch_id !== filterBatch) return false;
    if (filterStatus !== 'all') {
      if (filterStatus === 'failed') {
        if (execItem.status !== 'failed' && execItem.status !== 'failed_before_publish') return false;
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
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const profileName = getProfileName(execItem.profile_id).toLowerCase();
      const caption = (execItem.spun_caption || execItem.base_caption || '').toLowerCase();
      const media = (execItem.media_file || '').toLowerCase();
      if (!profileName.includes(q) && !caption.includes(q) && !media.includes(q)) {
        return false;
      }
    }
    return true;
  });

  const uncertainCount = stats.uncertain || 0;
  const failedCount = stats.failed || 0;
  const hasAttentionItems = uncertainCount > 0 || failedCount > 0;

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      {/* Top Action Error Alert */}
      {actionError && (
        <div className="p-3 bg-red-950/70 border border-red-800 text-red-200 text-xs rounded-xl flex items-center justify-between gap-2 shadow-sm animate-in fade-in duration-150">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{actionError}</span>
          </div>
          <button onClick={() => setActionError(null)} className="text-red-400 hover:text-white font-bold ml-2">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

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
            <span>All Posts</span>
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
            <span className="w-2 h-2 rounded-full bg-blue-500 animate-ping" />
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
              className={`px-3 py-1.5 rounded-xl font-medium transition-all flex items-center gap-1.5 animate-pulse ${
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
          ZONE 2: ATTENTION REQUIRED BANNER (Conditional)
          ============================================================ */}
      {hasAttentionItems && (
        <div className="p-3.5 rounded-2xl bg-amber-950/30 border border-amber-700/60 flex flex-wrap items-center justify-between gap-3 shadow-md animate-in fade-in duration-150">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-amber-500/20 text-amber-400 shrink-0">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <div>
              <div className="text-xs font-bold text-amber-200 flex items-center gap-2">
                <span>Attention Required</span>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-900/60 text-amber-300">
                  {uncertainCount + failedCount} items
                </span>
              </div>
              <div className="text-[11px] text-amber-300/80 mt-0.5 leading-snug">
                {uncertainCount > 0 && `${uncertainCount} post(s) held for Phase 0 review to prevent duplicates. `}
                {failedCount > 0 && `${failedCount} post(s) failed pre-publish and can be retried safely.`}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs">
            {uncertainCount > 0 && (
              <button
                type="button"
                onClick={() => setFilterStatus('uncertain')}
                className="px-3 py-1.5 rounded-xl bg-amber-600 hover:bg-amber-500 text-white font-semibold transition-colors shadow-sm"
              >
                Review Uncertain ({uncertainCount})
              </button>
            )}
            {failedCount > 0 && (
              <button
                type="button"
                onClick={() => setFilterStatus('failed')}
                className="px-3 py-1.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-red-300 border border-red-800/60 font-medium transition-colors"
              >
                View Failed ({failedCount})
              </button>
            )}
          </div>
        </div>
      )}

      {/* ============================================================
          ZONE 3: SINGLE-ROW FILTER BAR
          ============================================================ */}
      <div className="px-3.5 py-2.5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 flex flex-wrap items-center gap-3">
        {/* Account filter */}
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-zinc-500 font-medium">Account:</span>
          <select
            value={filterProfile}
            onChange={(e) => setFilterProfile(e.target.value)}
            className="px-2.5 py-1.5 text-xs rounded-xl bg-zinc-950 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-500"
          >
            <option value="all">All Accounts ({profiles.length})</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        {/* Batch filter with inline delete */}
        {batches.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs">
            <span className="text-zinc-500 font-medium">Batch:</span>
            <select
              value={filterBatch}
              onChange={(e) => setFilterBatch(e.target.value)}
              className="px-2.5 py-1.5 text-xs rounded-xl bg-zinc-950 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-500 max-w-[200px]"
            >
              <option value="all">All Batches ({batches.length})</option>
              {batches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.name}
                </option>
              ))}
            </select>
            {filterBatch !== 'all' && (
              <button
                type="button"
                onClick={() => handleDeleteBatch(filterBatch)}
                className="p-1.5 rounded-lg text-zinc-500 hover:text-red-400 hover:bg-zinc-800 transition-colors"
                title="Remove this batch and cancel remaining executions"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}

        {/* Search box — pushed to the right */}
        <div className="relative ml-auto min-w-[200px]">
          <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search captions or accounts..."
            className="w-full pl-8 pr-3 py-1.5 text-xs rounded-xl bg-zinc-950 border border-zinc-800 text-white placeholder-zinc-500 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* ============================================================
          ZONE 4: SCHEDULED EXECUTIONS TIMELINE TABLE (Main Content)
          ============================================================ */}
      <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold text-zinc-200 uppercase tracking-wider flex items-center gap-2">
            <Calendar className="w-4 h-4 text-emerald-400" />
            Posting Timeline ({filteredExecutions.length} of {executions.length})
          </h4>

          {filterStatus !== 'all' || filterProfile !== 'all' || filterBatch !== 'all' || searchQuery ? (
            <button
              type="button"
              onClick={() => {
                setFilterStatus('all');
                setFilterProfile('all');
                setFilterBatch('all');
                setSearchQuery('');
              }}
              className="text-xs text-blue-400 hover:text-blue-300 font-medium"
            >
              Reset Filters
            </button>
          ) : null}
        </div>

        {filteredExecutions.length === 0 ? (
          <div className="p-12 text-center text-xs text-zinc-500 border border-dashed border-zinc-800/80 rounded-xl">
            No executions match the selected filters. Use the Daily Batch Creator tab to schedule posts.
          </div>
        ) : (
          <div className="space-y-2.5 max-h-[620px] overflow-y-auto pr-1">
            {filteredExecutions.map((item) => (
              <div
                key={item.execution_id}
                className="p-3 rounded-xl bg-zinc-950/70 border border-zinc-800/80 hover:border-zinc-700 transition-all flex flex-wrap items-center justify-between gap-3 group"
              >
                {/* Left group: Time, Media Thumbnail, Details */}
                <div className="flex items-center gap-3 min-w-[240px]">
                  {/* Scheduled Time Stamp */}
                  <div className="text-center font-mono shrink-0 w-14">
                    <div className="text-xs font-bold text-white">
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
                          url: `/shared_media/${item.media_file}`,
                          type: item.post_type === 'reel' ? 'reel' : 'photo',
                          name: item.media_file || 'Media',
                          caption: item.spun_caption || item.base_caption,
                          comment: item.first_comment || undefined,
                        })
                      }
                      className="shrink-0 w-12 h-12 rounded-xl overflow-hidden border border-zinc-750 hover:border-blue-500 cursor-pointer shadow relative group/thumb bg-black flex items-center justify-center transition-all"
                      title="Click for full size preview"
                    >
                      {item.post_type === 'reel' ? (
                        <>
                          <video
                            src={`/shared_media/${item.media_file}`}
                            className="w-full h-full object-cover opacity-80"
                            preload="metadata"
                          />
                          <div className="absolute inset-0 flex items-center justify-center bg-black/40 group-hover/thumb:bg-black/20">
                            <Play className="w-4 h-4 text-white fill-white drop-shadow" />
                          </div>
                        </>
                      ) : (
                        <img
                          src={`/shared_media/${item.media_file}`}
                          alt="preview"
                          className="w-full h-full object-cover"
                        />
                      )}
                    </div>
                  ) : item.post_type === 'warming' ? (
                    <div className="w-12 h-12 rounded-xl border border-orange-900/70 bg-orange-950/30 flex items-center justify-center text-orange-400 shrink-0">
                      <Flame className="w-5 h-5" />
                    </div>
                  ) : (
                    <div className="w-12 h-12 rounded-xl border border-dashed border-zinc-800 bg-zinc-900/40 flex items-center justify-center text-zinc-600 text-[10px] shrink-0 font-medium">
                      Text
                    </div>
                  )}

                  {/* Text Details: Profile, Type, Caption, 1st Comment */}
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-zinc-200 truncate">
                        {getProfileName(item.profile_id)}
                      </span>
                      {item.post_type === 'warming' ? (
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-orange-950/80 text-orange-300 border border-orange-800/60 flex items-center gap-0.5 shrink-0">
                          <Flame className="w-2.5 h-2.5" /> Warming
                        </span>
                      ) : item.post_type === 'reel' ? (
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-purple-950/80 text-purple-300 border border-purple-800/60 flex items-center gap-0.5 shrink-0">
                          <Film className="w-2.5 h-2.5" /> Reel
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-blue-950/80 text-blue-300 border border-blue-800/60 flex items-center gap-0.5 shrink-0">
                          <ImageIcon className="w-2.5 h-2.5" /> Photo
                        </span>
                      )}
                    </div>

                    <div className="text-[11px] text-zinc-400 line-clamp-1 max-w-[380px] mt-0.5">
                      {item.post_type === 'warming'
                        ? `${item.warming_surface === 'profile' ? 'Profile' : 'News Feed'} · ${item.warming_scroll_actions ?? item.scrolls ?? 4} scrolls`
                        : (item.spun_caption || item.base_caption || 'No caption')}
                    </div>

                    {item.first_comment && (
                      <div className="text-[10px] text-zinc-500 truncate max-w-[340px]">
                        💬 {item.first_comment}
                      </div>
                    )}
                  </div>
                </div>

                {/* Right group: Badges & Action Buttons */}
                <div className="flex items-center gap-2 sm:gap-2.5">
                  {getStatusBadge(item.status, item.execution_id)}

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

                  {/* Re-run button for failed or skipped items */}
                  {(item.status === 'failed' || item.status === 'failed_before_publish' || item.status.startsWith('skipped')) && (
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

                  {/* Delete button (locked while active or uncertain) */}
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
                      title="Cannot delete while execution is running or held at safety gate"
                    >
                      <Lock className="w-3.5 h-3.5" />
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

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
        const novncUrl = wsPort ? `http://${window.location.hostname}:${wsPort}/vnc.html` : null;

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
                    <strong className="text-amber-300">Duplicate Prevention Policy:</strong> Direct rerun is locked to prevent duplicate posts on Facebook. Inspect the browser via noVNC before deciding.
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
