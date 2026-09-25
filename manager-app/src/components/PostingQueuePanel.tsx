import React, { useState, useEffect } from 'react';
import { QueueDataResponse, QueueExecutionItem } from '../types/automation';
import { Profile } from '../types/profile';
import { fetchQueue, deleteBatch, deleteExecution, runExecutionNow, resolveUncertainExecution } from '../services/api';
import {
  Clock,
  Play,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Layers,
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
  const [actionError, setActionError] = useState<string | null>(null);
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);

  // Phase 0 & Phase 1: Review & Resolve Uncertain State
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
        resolution === 'published' ? (resolvePostUrl.trim() || undefined) : undefined
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
      case 'running':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-950 text-blue-300 border border-blue-800/80 flex items-center gap-1 animate-pulse">
            <Loader2 className="w-3 h-3 text-blue-400 animate-spin" /> Running
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
            <span>Failed (Pre-publish)</span>
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
            title="State ambiguous or semantic fallback gated. Rerun locked until operator review."
          >
            <ShieldAlert className="w-3 h-3 text-amber-400" /> {status === 'needs_review' ? 'Needs Review' : 'Uncertain (Check FB)'}
          </span>
        );
      default:
        if (status.startsWith('skipped')) {
          return (
            <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-zinc-900 text-zinc-500 border border-zinc-800 flex items-center gap-1">
              <Lock className="w-3 h-3" /> Skipped (Stopped)
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
  const filteredExecutions = executions.filter((execItem) => {
    if (filterProfile !== 'all' && execItem.profile_id !== filterProfile) return false;
    if (filterStatus !== 'all') {
      if (filterStatus === 'failed') {
        if (execItem.status !== 'failed' && execItem.status !== 'failed_before_publish') return false;
      } else if (filterStatus === 'uncertain') {
        if (execItem.status !== 'uncertain' && execItem.status !== 'needs_review') return false;
      } else if (execItem.status !== filterStatus) {
        return false;
      }
    }
    return true;
  });

  return (
    <div className="space-y-6">
      {actionError && (
        <div className="p-3 bg-red-950/70 border border-red-800 text-red-200 text-xs rounded-lg flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
          <span>{actionError}</span>
        </div>
      )}

      {/* Top Telemetry Stats Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-7 gap-3">
        <div className="p-3 rounded-xl bg-zinc-900/60 border border-zinc-800 text-center">
          <div className="text-[11px] text-zinc-500 uppercase font-medium tracking-wider">Total</div>
          <div className="text-xl font-bold text-white mt-0.5 font-mono">{queueData?.stats?.total || 0}</div>
        </div>
        <div className="p-3 rounded-xl bg-zinc-900/60 border border-zinc-800 text-center">
          <div className="text-[11px] text-zinc-400 uppercase font-medium tracking-wider">Pending</div>
          <div className="text-xl font-bold text-zinc-300 mt-0.5 font-mono">{queueData?.stats?.pending || 0}</div>
        </div>
        <div className="p-3 rounded-xl bg-blue-950/20 border border-blue-900/40 text-center">
          <div className="text-[11px] text-blue-400 uppercase font-medium tracking-wider">Running</div>
          <div className="text-xl font-bold text-blue-300 mt-0.5 font-mono">{queueData?.stats?.running || 0}</div>
        </div>
        <div className="p-3 rounded-xl bg-emerald-950/20 border border-emerald-900/40 text-center">
          <div className="text-[11px] text-emerald-400 uppercase font-medium tracking-wider">Published</div>
          <div className="text-xl font-bold text-emerald-400 mt-0.5 font-mono">{queueData?.stats?.published || 0}</div>
        </div>
        <div className="p-3 rounded-xl bg-red-950/20 border border-red-900/40 text-center">
          <div className="text-[11px] text-red-400 uppercase font-medium tracking-wider">Failed</div>
          <div className="text-xl font-bold text-red-400 mt-0.5 font-mono">{queueData?.stats?.failed || 0}</div>
        </div>
        <div
          className={`p-3 rounded-xl border text-center transition-all ${
            (queueData?.stats?.uncertain || 0) > 0
              ? 'bg-amber-950/40 border-amber-600/70 shadow-lg shadow-amber-950/30'
              : 'bg-zinc-900/60 border-zinc-800'
          }`}
        >
          <div className="text-[11px] text-amber-400 uppercase font-medium tracking-wider flex items-center justify-center gap-1">
            <ShieldAlert className="w-3 h-3" /> Uncertain
          </div>
          <div className="text-xl font-bold text-amber-300 mt-0.5 font-mono">{queueData?.stats?.uncertain || 0}</div>
        </div>
        <div className="p-3 rounded-xl bg-zinc-900/60 border border-zinc-800 text-center">
          <div className="text-[11px] text-zinc-500 uppercase font-medium tracking-wider">Skipped</div>
          <div className="text-xl font-bold text-zinc-500 mt-0.5 font-mono">{queueData?.stats?.skipped || 0}</div>
        </div>
      </div>

      {/* Filter and Control Bar */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="text-xs text-zinc-400 font-medium">Filter Profile:</span>
          <select
            value={filterProfile}
            onChange={(e) => setFilterProfile(e.target.value)}
            className="px-2.5 py-1 text-xs rounded bg-zinc-950 border border-zinc-700 text-zinc-200 focus:outline-none focus:border-blue-500"
          >
            <option value="all">All Accounts</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>

          <span className="text-xs text-zinc-400 font-medium ml-2">Status:</span>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-2.5 py-1 text-xs rounded bg-zinc-950 border border-zinc-700 text-zinc-200 focus:outline-none focus:border-blue-500"
          >
            <option value="all">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="running">Running</option>
            <option value="published">Published</option>
            <option value="failed">Failed (Safe Retry)</option>
            <option value="uncertain">Uncertain (Review)</option>
          </select>
        </div>

        <button
          type="button"
          onClick={loadQueue}
          disabled={loading}
          className="px-3 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-zinc-300 flex items-center gap-1.5 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Active Batches Section */}
      {(queueData?.batches || []).length > 0 && (
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
            <Layers className="w-3.5 h-3.5 text-blue-400" />
            Active Batches ({queueData?.batches?.length})
          </h4>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {queueData?.batches?.map((batch) => (
              <div
                key={batch.batch_id}
                className="p-3.5 rounded-xl bg-zinc-950/80 border border-zinc-800 flex items-center justify-between gap-3"
              >
                <div>
                  <div className="text-xs font-semibold text-white">{batch.name}</div>
                  <div className="text-[11px] text-zinc-500 font-mono mt-0.5">
                    {batch.target_profiles.length} Profiles · Window: {batch.schedule_window.start_time} - {batch.schedule_window.end_time} · {batch.schedule_window.profile_stagger_minutes}m stagger
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => handleDeleteBatch(batch.batch_id)}
                  className="p-1.5 rounded text-zinc-500 hover:text-red-400 hover:bg-zinc-800 transition-colors"
                  title="Delete batch"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Executions Timeline Table */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
          <Calendar className="w-3.5 h-3.5 text-emerald-400" />
          Scheduled Executions Timeline ({filteredExecutions.length})
        </h4>

        {filteredExecutions.length === 0 ? (
          <div className="p-8 text-center text-xs text-zinc-500">
            No scheduled executions found. Use the Batch Post Creator tab to prepare and schedule posts.
          </div>
        ) : (
          <div className="space-y-2.5 max-h-[600px] overflow-y-auto pr-1">
            {filteredExecutions.map((item) => (
              <div
                key={item.execution_id}
                className="p-3 rounded-lg bg-zinc-950/80 border border-zinc-800/80 hover:border-zinc-700 transition-all flex flex-wrap items-center justify-between gap-3"
              >
                <div className="flex items-center gap-3 min-w-[200px]">
                  <div className="text-center font-mono shrink-0">
                    <div className="text-xs font-bold text-white">
                      {formatScheduledTime(item.scheduled_at)}
                    </div>
                    <div className="text-[9px] text-zinc-500">
                      {new Date(item.scheduled_at).toLocaleDateString([], { month: 'numeric', day: 'numeric' })}
                    </div>
                  </div>

                  <div className="w-px h-8 bg-zinc-800" />

                  {item.media_file && (
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
                      className="shrink-0 w-11 h-11 rounded-lg overflow-hidden border border-zinc-700 hover:border-blue-400 cursor-pointer shadow relative group bg-black flex items-center justify-center transition-all"
                      title="Click to view big size"
                    >
                      {item.post_type === 'reel' ? (
                        <>
                          <video
                            src={`/shared_media/${item.media_file}`}
                            className="w-full h-full object-cover opacity-80"
                            preload="metadata"
                          />
                          <div className="absolute inset-0 flex items-center justify-center bg-black/40 group-hover:bg-black/20">
                            <Play className="w-3.5 h-3.5 text-white fill-white" />
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
                  )}

                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-semibold text-zinc-200">
                        {getProfileName(item.profile_id)}
                      </span>
                      {item.post_type === 'reel' ? (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-purple-950 text-purple-300 border border-purple-800/60 flex items-center gap-0.5">
                          <Film className="w-2.5 h-2.5" /> Reel
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-blue-950 text-blue-300 border border-blue-800/60 flex items-center gap-0.5">
                          <ImageIcon className="w-2.5 h-2.5" /> Photo
                        </span>
                      )}
                    </div>

                    <div className="text-[11px] text-zinc-400 line-clamp-1 max-w-[360px] mt-0.5">
                      {item.spun_caption || item.base_caption || 'No caption'}
                    </div>

                    {item.first_comment && (
                      <div className="text-[10px] text-zinc-500 truncate max-w-[320px]">
                        💬 1st comment: {item.first_comment}
                      </div>
                    )}

                    {item.stage && (
                      <div className="text-[10px] text-zinc-500 font-mono mt-0.5">
                        Stage: <span className="text-zinc-400">{item.stage}</span>
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 sm:gap-3">
                  {getStatusBadge(item.status, item.execution_id)}

                  {item.review_status && (
                    <span
                      className="text-[10px] text-zinc-400 font-mono px-1.5 py-0.5 bg-zinc-900 border border-zinc-800 rounded hidden sm:inline-block"
                      title={`Review note: ${item.review_note || 'None'}`}
                    >
                      {item.review_status === 'resolved_published' ? '✅ Confirmed Pub' : '🔄 Unlocked Retry'}
                    </span>
                  )}

                  {item.post_url && (
                    <a
                      href={item.post_url}
                      target="_blank"
                      rel="noreferrer"
                      className="px-2.5 py-1 rounded text-[11px] font-semibold bg-blue-950/80 hover:bg-blue-900 text-blue-300 hover:text-blue-100 border border-blue-800/80 hover:border-blue-600 flex items-center gap-1.5 transition-all shadow-sm group"
                      title={`Verified Facebook Permalink (Match Confidence: ${Math.round((item.post_match_confidence || 1.0) * 100)}%)`}
                    >
                      <ExternalLink className="w-3 h-3 text-blue-400 group-hover:scale-110 transition-transform" />
                      <span>View on FB</span>
                    </a>
                  )}

                  {item.status === 'pending' && (
                    <button
                      type="button"
                      onClick={() => handleRunNow(item.execution_id)}
                      className="px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-[11px] font-semibold text-white flex items-center gap-1 transition-colors cursor-pointer shadow-sm"
                      title="Run immediately"
                    >
                      <Play className="w-3 h-3 fill-current" />
                      Run Now
                    </button>
                  )}

                  {(item.status === 'uncertain' || item.status === 'needs_review') && (
                    <button
                      type="button"
                      onClick={() => {
                        setResolvingItem(item);
                        setResolveNote('');
                        setResolvePostUrl(item.post_url || '');
                      }}
                      className="px-2.5 py-1 rounded bg-amber-600 hover:bg-amber-500 text-[11px] font-semibold text-white flex items-center gap-1.5 transition-colors cursor-pointer shadow-sm animate-pulse hover:animate-none"
                      title="Review state on Facebook and resolve outcome"
                    >
                      <ShieldAlert className="w-3.5 h-3.5" />
                      Review & Resolve
                    </button>
                  )}

                  {(item.status === 'failed' || item.status === 'failed_before_publish' || item.status.startsWith('skipped')) && (
                    <button
                      type="button"
                      onClick={() => handleRunNow(item.execution_id)}
                      className="px-2.5 py-1 rounded bg-rose-600/90 hover:bg-rose-500 text-[11px] font-semibold text-white flex items-center gap-1 transition-colors cursor-pointer shadow-sm"
                      title="Re-run this post now"
                    >
                      <RotateCcw className="w-3 h-3" />
                      Re-run
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => handleDeleteExecution(item.execution_id)}
                    className="p-1 rounded text-zinc-500 hover:text-red-400 transition-colors cursor-pointer"
                    title="Remove from queue"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Big Size Media Lightbox Modal */}
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

      {/* Review & Resolve Uncertain Modal (Phase 0 P0 Guard) */}
      {resolvingItem && (() => {
        const targetProfile = profiles.find((p) => p.id === resolvingItem.profile_id);
        const wsPort = targetProfile?.container?.ws_port || (targetProfile?.container?.vnc_port ? targetProfile.container.vnc_port + 180 : null);
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
                {/* Duplicate publication prevention banner */}
                <div className="p-3 bg-amber-950/30 border border-amber-800/50 rounded-xl text-xs text-amber-200/90 leading-relaxed flex items-start gap-2.5">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <strong className="text-amber-300">Duplicate Prevention Policy:</strong> Direct or automatic rerun is locked to prevent publishing duplicate posts on Facebook. Inspect the browser via noVNC before deciding.
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
                    <span className="font-mono text-[11px] text-amber-300 font-semibold">{resolvingItem.stage || 'publish_clicked'}</span>
                  </div>
                  {resolvingItem.error && (
                    <div className="py-1">
                      <span className="text-zinc-400 block mb-1">Error / Timeout Details:</span>
                      <div className="p-2 rounded bg-black/60 border border-red-900/40 text-red-300 font-mono text-[11px] break-words">
                        {resolvingItem.error}
                      </div>
                    </div>
                  )}
                  {resolvingItem.media_file && (
                    <div className="flex justify-between items-center py-1 border-b border-zinc-800/60">
                      <span className="text-zinc-400">Media File:</span>
                      <span className="font-mono text-[11px] text-blue-300 truncate max-w-[280px]">{resolvingItem.media_file}</span>
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

                {/* Step 1: Manual Visual Verification */}
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
                    Open the live container display in a new tab. Check whether the {resolvingItem.post_type || 'post'} appeared on the Facebook page/profile.
                  </p>
                </div>

                {/* Step 2: Operator Resolution Note */}
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

                {/* Step 3: Verified Facebook Permalink (optional) */}
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
                  {/* Resolution: Confirmed NOT Published */}
                  <button
                    type="button"
                    onClick={() => handleResolveUncertain('not_published')}
                    disabled={isResolving}
                    className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-red-950 hover:text-red-200 border border-zinc-700 hover:border-red-700 text-xs font-semibold text-zinc-200 flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    title="Mark as not published. Unlocks retry as a safe pre-publish failure."
                  >
                    {isResolving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5 text-red-400" />}
                    <span>Confirmed NOT Published (Allow Retry)</span>
                  </button>

                  {/* Resolution: Confirmed Published */}
                  <button
                    type="button"
                    onClick={() => handleResolveUncertain('published')}
                    disabled={isResolving}
                    className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors disabled:opacity-50 shadow-sm"
                    title="Mark as successfully published. Locks execution as published."
                  >
                    {isResolving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
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
