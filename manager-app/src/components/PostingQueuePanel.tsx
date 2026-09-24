import React, { useState, useEffect } from 'react';
import { QueueDataResponse } from '../types/automation';
import { Profile } from '../types/profile';
import { fetchQueue, deleteBatch, deleteExecution, runExecutionNow } from '../services/api';
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

  const getStatusBadge = (status: string) => {
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
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-950 text-red-300 border border-red-800/80 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-red-400" /> Failed
          </span>
        );
      case 'uncertain':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-950 text-amber-300 border border-amber-800/80">
            ❓ Uncertain (Check FB)
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
    if (filterStatus !== 'all' && execItem.status !== filterStatus) return false;
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
      <div className="grid grid-cols-2 sm:grid-cols-6 gap-3">
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
            <option value="failed">Failed</option>
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
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {getStatusBadge(item.status)}

                  {item.status === 'pending' && (
                    <button
                      type="button"
                      onClick={() => handleRunNow(item.execution_id)}
                      className="px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-[11px] font-semibold text-white flex items-center gap-1 transition-colors"
                      title="Run immediately"
                    >
                      <Play className="w-3 h-3 fill-current" />
                      Run Now
                    </button>
                  )}

                  <button
                    type="button"
                    onClick={() => handleDeleteExecution(item.execution_id)}
                    className="p-1 rounded text-zinc-500 hover:text-red-400 transition-colors"
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
    </div>
  );
};
