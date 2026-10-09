import { GroupedProfileSelector } from './GroupedProfileSelector';
import React, { useState, useEffect } from 'react';
import { Profile } from '../types/profile';
import { CreateBatchParams } from '../types/automation';
import { createBatch, uploadMediaFiles, getSharedMediaUrl } from '../services/api';
import {
  UploadCloud,
  Film,
  Image as ImageIcon,
  Trash2,
  Calendar,
  ShieldCheck,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  FileCode,
  X,
  Play,
  Paperclip,
  Zap,
  Edit2,
  Check,
  ChevronDown,
  ChevronRight,
  Users,
  Plus,
} from 'lucide-react';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';

interface BatchPostCreatorProps {
  profiles: Profile[];
  onBatchCreated: () => void;
  mode?: 'posts' | 'live';
}

interface DraftPostRow {
  id: string;
  type: 'photo' | 'reel' | 'live';
  media_file: string;
  media_name: string;
  preview_url?: string;
  title?: string;
  pinned_comment?: string;
  caption: string;
  first_comment: string;
}

interface LightboxMedia {
  url: string;
  type: 'photo' | 'reel';
  name: string;
  caption?: string;
  comment?: string;
}

export const BatchPostCreator: React.FC<BatchPostCreatorProps> = ({
  profiles,
  onBatchCreated,
  mode = 'posts',
}) => {
  const liveMode = mode === 'live';
  const storageKey = (name: string) => `${liveMode ? 'live_creator' : 'batch_creator'}_${name}`;
  const [loopVideo, setLoopVideo] = useState(() => localStorage.getItem(storageKey('loop')) === 'true');
  const [durationMinutes, setDurationMinutes] = useState(() => localStorage.getItem(storageKey('duration_minutes')) || '');
  const [muted, setMuted] = useState(() => localStorage.getItem(storageKey('muted')) === 'true');

  // Campaign Name with inline editing
  const [batchName, setBatchName] = useState(() => {
    return (
      localStorage.getItem(storageKey('batch_name')) ||
      `${liveMode ? 'Live Batch' : 'Daily Batch'} ${new Date().toLocaleDateString()}`
    );
  });
  const [isEditingName, setIsEditingName] = useState(false);

  // Selected Target Profiles
  const [savedProfileIds, setSelectedProfileIds] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(storageKey('profiles'));
      if (saved) { const ids = JSON.parse(saved); if (Array.isArray(ids)) return ids.filter((id): id is string => typeof id === "string"); }
    } catch (_) {}
    return profiles.filter((p) => p.status === 'running').map((p) => p.id);
  });
  const [showProfileModal, setShowProfileModal] = useState(false);

  // Execution & Timing Settings
  const [executionMode, setExecutionMode] = useState<'now' | 'scheduled'>(() => {
    return localStorage.getItem(storageKey('start_now')) === 'true' ? 'now' : 'now';
  });

  const [startTime, setStartTime] = useState(() => {
    return localStorage.getItem(storageKey('start_time')) || '09:00';
  });

  const [endTime] = useState(() => {
    return localStorage.getItem(storageKey('end_time')) || '21:00';
  });

  const [staggerSeconds, setStaggerSeconds] = useState(() => {
    const saved = localStorage.getItem(storageKey('stagger_seconds'));
    if (saved !== null) {
      const parsed = parseInt(saved, 10);
      return Number.isNaN(parsed) ? 60 : Math.max(0, parsed);
    }
    return 60;
  });

  const [iterationDelaySeconds, setIterationDelaySeconds] = useState(() => {
    const saved = localStorage.getItem(storageKey('iteration_delay_seconds'));
    if (saved !== null) {
      const parsed = parseInt(saved, 10);
      return Number.isNaN(parsed) ? 60 : Math.max(0, parsed);
    }
    return 60;
  });

  const [preparationMode, setPreparationMode] = useState<'off' | 'brief' | 'extended'>(() => {
    const saved = localStorage.getItem(storageKey('preparation_mode'));
    return saved === 'off' || saved === 'brief' || saved === 'extended' ? saved : 'brief';
  });

  // Global AI Caption Spin Toggle
  const [aiSpinAll, setAiSpinAll] = useState(() => {
    const saved = localStorage.getItem(storageKey('ai_spin'));
    return saved !== null ? saved === 'true' : true;
  });

  // Collapsible Advanced Settings
  const [showAdvanced, setShowAdvanced] = useState(false);

  const videoUploadType = liveMode ? 'live' : 'reel';

  // Draft Posts
  const [posts, setPosts] = useState<DraftPostRow[]>(() => {
    try {
      const saved = localStorage.getItem(storageKey('draft_posts'));
      if (saved) return JSON.parse(saved).filter((post: DraftPostRow) => liveMode ? post.type === 'live' : post.type !== 'live');
    } catch (_) {}
    return [];
  });

  const hasLive = liveMode || posts.some(post => post.type === 'live');
  const selectedProfileIds = savedProfileIds.filter(id => profiles.some(p => p.id === id && (!hasLive || p.live?.configured)));

  // UI state
  const [isUploading, setIsUploading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);

  useEffect(() => {
    localStorage.setItem(storageKey('loop'), String(loopVideo));
    localStorage.setItem(storageKey('duration_minutes'), durationMinutes);
    localStorage.setItem(storageKey('muted'), String(muted));
  }, [loopVideo, durationMinutes, muted]);

  // Auto-save to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(storageKey('draft_posts'), JSON.stringify(posts));
    } catch (_) {}
  }, [posts]);

  useEffect(() => {
    localStorage.setItem(storageKey('batch_name'), batchName);
  }, [batchName]);

  useEffect(() => {
    localStorage.setItem(storageKey('profiles'), JSON.stringify(savedProfileIds));
  }, [savedProfileIds]);

  useEffect(() => {
    localStorage.setItem(storageKey('start_time'), startTime);
    localStorage.setItem(storageKey('end_time'), endTime);
    localStorage.setItem(storageKey('stagger_seconds'), String(staggerSeconds));
    localStorage.setItem(storageKey('iteration_delay_seconds'), String(iterationDelaySeconds));
    localStorage.setItem(storageKey('ai_spin'), String(aiSpinAll));
    localStorage.setItem(storageKey('start_now'), String(executionMode === 'now'));
    localStorage.setItem(storageKey('preparation_mode'), preparationMode);
  }, [startTime, endTime, staggerSeconds, iterationDelaySeconds, aiSpinAll, executionMode, preparationMode]);

  // Bulk Media Upload Handler
  const handleBulkMediaUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    setErrorMsg(null);
    setIsUploading(true);

    try {
      const formData = new FormData();
      Array.from(e.target.files).forEach((file) => {
        formData.append('files', file);
      });

      const res = await uploadMediaFiles(formData);
      const newRows: DraftPostRow[] = res.files.map((file, idx) => ({
        id: `draft_${Date.now()}_${idx}`,
        type: liveMode ? 'live' : file.type === 'reel' ? videoUploadType : file.type,
        media_file: file.filename,
        media_name: file.original_name || file.filename,
        preview_url: getSharedMediaUrl(file.filename),
        caption: '',
        first_comment: '',
      }));

      setPosts((prev) => [...prev, ...newRows]);
      setSuccessMsg(`Added ${res.files.length} media item(s) to draft!`);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to upload media files');
    } finally {
      setIsUploading(false);
      e.target.value = '';
    }
  };

  // Single Row Media Replace/Upload Handler
  const handleRowMediaUpload = async (rowId: string, file: File) => {
    setErrorMsg(null);
    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('files', file);
      const res = await uploadMediaFiles(formData);
      if (res.files && res.files.length > 0) {
        const uploaded = res.files[0];
        setPosts((prev) =>
          prev.map((row) =>
            row.id === rowId
              ? {
                  ...row,
                  media_file: uploaded.filename,
                  media_name: uploaded.original_name || uploaded.filename,
                  preview_url: getSharedMediaUrl(uploaded.filename),
                  type: liveMode || row.type === 'live' && uploaded.type === 'reel' ? 'live' : uploaded.type,
                }
              : row
          )
        );
        setSuccessMsg(`Attached ${uploaded.original_name} to post!`);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to attach media to post');
    } finally {
      setIsUploading(false);
    }
  };

  // JSON File Content Importer Handler
  const handleJsonUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    setErrorMsg(null);
    const file = e.target.files[0];
    const reader = new FileReader();

    reader.onload = (event) => {
      try {
        const text = event.target?.result as string;
        const parsed = JSON.parse(text);
        let items: any[] = [];

        if (Array.isArray(parsed)) {
          items = parsed;
        } else if (parsed && typeof parsed === 'object') {
          items = parsed.posts || parsed.items || parsed.data || [parsed];
        }

        if (!items || items.length === 0) {
          setErrorMsg('JSON file does not contain an array of posts.');
          return;
        }

        const importedRows: DraftPostRow[] = items.map((item, idx) => {
          const caption = item.caption || item.text || item.description || item.content || '';
          const comment = item.comment || item.first_comment || item.link || item.url || '';
          const rawType = (item.type || item.media_type || '').toLowerCase();
          const type: 'photo' | 'reel' | 'live' =
            liveMode ? 'live' : rawType === 'reel' || rawType === 'video' ? 'reel' : 'photo';
          const mediaFile = item.media_file || item.media || item.file || '';

          return {
            id: `json_${Date.now()}_${idx}`,
            type,
            media_file: mediaFile,
            media_name: mediaFile || '',
            preview_url: mediaFile ? getSharedMediaUrl(mediaFile) : undefined,
            caption,
            first_comment: comment,
          };
        });

        setPosts((prev) => [...prev, ...importedRows]);
        setSuccessMsg(`📄 Imported ${importedRows.length} posts from ${file.name}!`);
      } catch (err: any) {
        setErrorMsg(`Failed to parse JSON: ${err.message}`);
      } finally {
        e.target.value = '';
      }
    };

    reader.readAsText(file);
  };

  // Add empty row
  const handleAddEmptyRow = () => {
    setPosts((prev) => [
      ...prev,
      {
        id: `draft_${Date.now()}_${prev.length}`,
        type: liveMode ? 'live' : 'photo',
        media_file: '',
        media_name: '',
        caption: '',
        first_comment: '',
      },
    ]);
  };

  const handleRemoveRow = (id: string) => {
    setPosts((prev) => prev.filter((p) => p.id !== id));
  };

  const handleUpdateRow = (id: string, updates: Partial<DraftPostRow>) => {
    setPosts((prev) => prev.map((p) => (p.id === id ? { ...p, ...updates } : p)));
  };

  const handleRemoveMediaFromRow = (id: string) => {
    setPosts((prev) =>
      prev.map((p) =>
        p.id === id ? { ...p, media_file: '', media_name: '', preview_url: undefined } : p
      )
    );
  };

  // Submit Batch
  const handleSubmitBatch = async () => {
    setErrorMsg(null);
    setSuccessMsg(null);

    if (selectedProfileIds.length === 0) {
      setErrorMsg('Please select at least one target account.');
      return;
    }
    if (posts.length === 0) {
      setErrorMsg('Please add at least one post before launching.');
      return;
    }

    if (posts.some(p => p.type === 'live' && (!(p.title || '').trim() || !p.caption.trim()))) {
      setErrorMsg('Enter a title and caption for each Live video.');
      return;
    }
    if (posts.some(p => p.type === 'live' && !p.media_file)) {
      setErrorMsg('Attach a video to every Live row.'); return;
    }
    if (liveMode && ((loopVideo && !durationMinutes) || (durationMinutes !== '' && (!Number.isInteger(Number(durationMinutes)) || Number(durationMinutes) < 1 || Number(durationMinutes) > 240)))) {
      setErrorMsg('Set a duration between 1 and 240 minutes. Loop requires a duration.'); return;
    }
    const emptyRow = posts.find((p) => !p.caption.trim() && !p.media_file);
    if (emptyRow) {
      setErrorMsg('Each post must have either an attached image/video or a caption.');
      return;
    }

    setIsSubmitting(true);
    const startNow = executionMode === 'now';

    try {
      const payload: CreateBatchParams = {
        name: batchName.trim() || `${liveMode ? 'Live Batch' : 'Daily Batch'} ${new Date().toLocaleDateString()}`,
        target_profiles: selectedProfileIds,
        start_now: startNow,
        schedule_window: {
          start_time: startTime,
          end_time: endTime,
          profile_stagger_seconds: staggerSeconds,
          batch_iteration_delay_seconds: iterationDelaySeconds,
          session_preparation_mode: liveMode ? 'off' : preparationMode,
          start_now: startNow,
        },
        posts: posts.map((p) => ({
          type: p.type,
          ...(p.type === 'live' ? {title: (p.title || '').trim(), pinned_comment: (p.pinned_comment || '').trim(), loop: loopVideo, muted, max_duration_seconds: durationMinutes ? Number(durationMinutes) * 60 : undefined} : {}),
          media_file: p.media_file,
          base_caption: p.caption.trim(),
          first_comment: p.type === 'live' ? undefined : p.first_comment.trim() || undefined,
          ai_spin: p.type === 'live' ? false : aiSpinAll,
        })),
      };

      const res = await createBatch(payload);
      setSuccessMsg(
        `✅ Successfully launched batch "${res.batch.name}" with ${res.total_executions} scheduled posts!`
      );
      setPosts([]);
      localStorage.removeItem(storageKey('draft_posts'));
      onBatchCreated();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to schedule campaign');
    } finally {
      setIsSubmitting(false);
    }
  };

  const totalExecutions = selectedProfileIds.length * posts.length;
  const runningProfilesCount = profiles.filter((p) => p.status === 'running').length;


  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      {/* Notifications */}
      {errorMsg && (
        <div className="p-3 bg-red-950/70 border border-red-800 text-red-200 text-xs rounded-xl flex items-center justify-between gap-2 shadow-sm animate-in fade-in duration-150">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{errorMsg}</span>
          </div>
          <button onClick={() => setErrorMsg(null)} className="text-red-400 hover:text-white font-bold ml-2">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {successMsg && (
        <div className="p-3 bg-emerald-950/70 border border-emerald-800 text-emerald-200 text-xs rounded-xl flex items-center justify-between gap-2 shadow-sm animate-in fade-in duration-150">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>{successMsg}</span>
          </div>
          <button onClick={() => setSuccessMsg(null)} className="text-emerald-400 hover:text-white font-bold ml-2">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Main 2-Column Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        {/* ============================================================
            LEFT COLUMN: CONTENT STUDIO (65% width)
            ============================================================ */}
        <div className="lg:col-span-7 xl:col-span-8 space-y-4">

          {/* Content Header & Upload Area */}
          <div className="p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
                  <span>{liveMode ? 'Live videos' : 'Content Studio'}</span>
                  <span className="text-xs font-normal text-zinc-400">
                    ({posts.length} {liveMode ? posts.length === 1 ? 'video' : 'videos' : posts.length === 1 ? 'post' : 'posts'} drafted)
                  </span>
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  {liveMode ? 'Upload videos to broadcast with each profile’s saved streaming settings.' : 'Upload videos or photos. Videos are posted as Reels.'}
                </p>
              </div>

              {/* Action buttons: Import JSON & Add Manual */}
              <div className="flex items-center gap-2">
                <label className={`${liveMode ? 'hidden' : ''} px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-xs text-zinc-300 flex items-center gap-1.5 cursor-pointer transition-colors shadow-sm`} title="Import posts from a JSON file">
                  <FileCode className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Import JSON</span>
                  <input
                    type="file"
                    accept=".json,application/json"
                    onChange={handleJsonUpload}
                    className="hidden"
                  />
                </label>

                <button
                  type="button"
                  onClick={handleAddEmptyRow}
                  className="px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-xs text-zinc-300 flex items-center gap-1.5 transition-colors shadow-sm"
                  title="Add a manual post without uploading media"
                >
                  <Plus className="w-3.5 h-3.5 text-blue-400" />
                  <span>{liveMode ? 'Add video' : 'Add Post'}</span>
                </button>
              </div>
            </div>

            {/* Drag & Drop Upload Zone */}
            <label className="border-2 border-dashed border-zinc-750 hover:border-blue-500 rounded-xl p-6 flex flex-col items-center justify-center cursor-pointer transition-all bg-zinc-950/40 hover:bg-zinc-900/40 group">
              <input
                type="file"
                multiple
                accept={liveMode ? ".mp4,.mov,.webm" : "image/*,video/mp4,video/quicktime,video/webm"}
                onChange={handleBulkMediaUpload}
                className="hidden"
                disabled={isUploading}
              />
              {isUploading ? (
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <Loader2 className="w-6 h-6 text-blue-400 animate-spin" />
                  <span>Uploading media to shared storage...</span>
                </div>
              ) : (
                <>
                  <UploadCloud className="w-8 h-8 text-zinc-400 group-hover:text-blue-400 transition-colors mb-2" />
                  <span className="text-xs font-semibold text-zinc-200">
                    {liveMode ? 'Drop videos here, or click to browse' : 'Drop videos or photos here, or click to browse'}
                  </span>
                  <span className="text-[11px] text-zinc-400 mt-1">
                    .mp4 / .mov / .webm videos assigned as <strong className="text-purple-400 font-medium">{videoUploadType === 'live' ? 'Live' : 'Reels'}</strong> {!liveMode && <>· images auto-assigned as <strong className="text-blue-400 font-medium">Photos</strong></>}
                  </span>
                </>
              )}
            </label>
          </div>

          {/* Posts List */}
          {posts.length > 0 ? (
            <div className="space-y-3">
              {posts.map((post, idx) => (
                <div
                  key={post.id}
                  className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800/80 hover:border-zinc-700 transition-all space-y-3 group"
                >
                  {/* Row Header: Number, Type Badge, Filename, Delete */}
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="w-5 h-5 rounded-full bg-zinc-800 text-[10px] font-mono text-zinc-300 flex items-center justify-center font-bold">
                        {idx + 1}
                      </span>

                      <Select value={post.type} ariaLabel={`Post ${idx + 1} type`} className="w-28 min-h-7 text-xs"
                        disabled={liveMode} options={liveMode ? [{value: 'live', label: 'Live'}] : [{value: 'photo', label: 'Photo'}, {value: 'reel', label: 'Reel'}]}
                        onValueChange={value => handleUpdateRow(post.id, {type: value as DraftPostRow['type'], ...(value === 'live' ? {caption: '', first_comment: ''} : {})})} />

                      {post.media_file ? (
                        <span className="text-[11px] text-zinc-400 font-mono truncate max-w-[220px]">
                          {post.media_name || post.media_file}
                        </span>
                      ) : (
                        <span className="text-[11px] text-amber-400/90 font-medium">
                          No media attached
                        </span>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => handleRemoveRow(post.id)}
                      className="p-1 rounded text-zinc-500 hover:text-red-400 transition-colors"
                      title="Delete post"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>

                  {/* Row Body: Thumbnail & Inputs */}
                  <div className="flex gap-3">
                    {/* Media Thumbnail */}
                    <div className="shrink-0">
                      {post.media_file ? (
                        <div className="relative group/thumb">
                          {post.type !== 'photo' ? (
                            <div
                              onClick={() =>
                                setLightboxMedia({
                                  url: post.preview_url || getSharedMediaUrl(post.media_file),
                                  type: 'reel',
                                  name: post.media_name || post.media_file,
                                  caption: post.caption,
                                  comment: post.first_comment,
                                })
                              }
                              className="relative w-16 h-16 rounded-xl overflow-hidden border border-purple-800/70 bg-black cursor-pointer shadow-md group-hover/thumb:ring-2 group-hover/thumb:ring-purple-500 transition-all flex items-center justify-center"
                              title="Click for full preview"
                            >
                              <video
                                src={post.preview_url || getSharedMediaUrl(post.media_file)}
                                className="w-full h-full object-cover opacity-75"
                                preload="metadata"
                              />
                              <div className="absolute inset-0 flex items-center justify-center bg-black/40 group-hover/thumb:bg-black/20 transition-all">
                                <Play className="w-5 h-5 text-white fill-white drop-shadow" />
                              </div>
                            </div>
                          ) : (
                            <div
                              onClick={() =>
                                setLightboxMedia({
                                  url: post.preview_url || getSharedMediaUrl(post.media_file),
                                  type: 'photo',
                                  name: post.media_name || post.media_file,
                                  caption: post.caption,
                                  comment: post.first_comment,
                                })
                              }
                              className="relative w-16 h-16 rounded-xl overflow-hidden border border-blue-800/70 bg-black cursor-pointer shadow-md group-hover/thumb:ring-2 group-hover/thumb:ring-blue-500 transition-all flex items-center justify-center"
                              title="Click for full preview"
                            >
                              <img
                                src={post.preview_url || getSharedMediaUrl(post.media_file)}
                                alt={post.media_name || 'preview'}
                                className="w-full h-full object-cover"
                                onError={(e) => {
                                  (e.target as HTMLElement).style.display = 'none';
                                }}
                              />
                            </div>
                          )}

                          {/* Quick Remove Media button */}
                          <button
                            type="button"
                            onClick={() => handleRemoveMediaFromRow(post.id)}
                            className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-red-600 hover:bg-red-500 text-white flex items-center justify-center text-[10px] shadow"
                            title="Remove attached media"
                          >
                            ×
                          </button>
                        </div>
                      ) : (
                        <label className="w-16 h-16 rounded-xl border border-dashed border-zinc-700 hover:border-blue-500 bg-zinc-950/60 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 cursor-pointer flex flex-col items-center justify-center transition-all p-1 text-center">
                          <Paperclip className="w-4 h-4 text-blue-400 mb-0.5" />
                          <span className="text-[9px] font-medium leading-tight">Attach</span>
                          <input
                            type="file"
                            accept={liveMode ? ".mp4,.mov,.webm" : "image/*,video/mp4,video/quicktime,video/webm"}
                            onChange={(e) =>
                              e.target.files?.[0] && handleRowMediaUpload(post.id, e.target.files[0])
                            }
                            className="hidden"
                            disabled={isUploading}
                          />
                        </label>
                      )}
                    </div>

                    {/* Caption & First Comment */}
                    {post.type === 'live' ? <div className="flex-1 space-y-2">
                      <input aria-label="Live title" value={post.title || ''} maxLength={255}
                        onChange={e => handleUpdateRow(post.id, {title: e.target.value})} placeholder="Live title"
                        className="w-full px-3 py-2 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-rose-500" />
                      <textarea aria-label="Live caption" value={post.caption} maxLength={5000} rows={3}
                        onChange={e => handleUpdateRow(post.id, {caption: e.target.value})} placeholder="Live caption"
                        className="w-full px-3 py-2 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-rose-500 resize-none" />
                      <details className="text-xs text-zinc-400">
                        <summary className="cursor-pointer">Pinned comment (optional)</summary>
                        <textarea aria-label="Preset pinned comment" value={post.pinned_comment || ''} maxLength={1000} rows={2}
                          onChange={e => handleUpdateRow(post.id, {pinned_comment: e.target.value})} placeholder="Comment to pin when Live starts"
                          className="mt-2 w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-rose-500 resize-none" />
                        <p className="mt-1 text-[11px] text-zinc-500">Leave blank to disable the preset for this Live.</p>
                      </details>
                      <p className="text-xs text-zinc-500">Added to Facebook before streaming. Choose the broadcast destination in Live Producer.</p>
                    </div> : <div className="flex-1 space-y-2">
                      <textarea
                        value={post.caption}
                        onChange={(e) => handleUpdateRow(post.id, { caption: e.target.value })}
                        placeholder={
                          post.type === 'reel'
                            ? 'Reel caption & hashtags (#reels #viral)...'
                            : "Post caption: What's on your mind?..."
                        }
                        rows={2}
                        className="w-full px-3 py-2 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-white placeholder-zinc-500 focus:outline-none focus:border-blue-500 resize-none font-sans"
                      />

                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-zinc-500 whitespace-nowrap">
                          1st Comment Link:
                        </span>
                        <input
                          type="url"
                          value={post.first_comment}
                          onChange={(e) =>
                            handleUpdateRow(post.id, { first_comment: e.target.value })
                          }
                          placeholder="https://example.com/guide (optional destination link)"
                          className="flex-1 px-2.5 py-1 text-[11px] rounded bg-zinc-950 border border-zinc-800 text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
                        />
                      </div>
                    </div>}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-8 rounded-2xl border border-dashed border-zinc-800/80 text-center text-zinc-500 text-xs">
              {liveMode ? 'Upload videos or add a video row to start a broadcast batch.' : 'Drop videos/photos above or click Add Post to start.'}
            </div>
          )}
        </div>

        {/* ============================================================
            RIGHT COLUMN: CAMPAIGN SIDEBAR (35% width)
            ============================================================ */}
        <div className="lg:col-span-5 xl:col-span-4 space-y-4">

          <div className="p-5 rounded-2xl bg-zinc-900/60 border border-zinc-800/80 space-y-5">

            {/* 1. Campaign Name (Inline editable) */}
            <div>
              <label className="block text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-1.5">
                {liveMode ? 'Live batch name' : 'Campaign Name'}
              </label>
              {isEditingName ? (
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={batchName}
                    onChange={(e) => setBatchName(e.target.value)}
                    onBlur={() => setIsEditingName(false)}
                    onKeyDown={(e) => e.key === 'Enter' && setIsEditingName(false)}
                    autoFocus
                    className="flex-1 px-3 py-1.5 text-xs rounded-lg bg-zinc-950 border border-zinc-700 text-white focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => setIsEditingName(false)}
                    className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-white border border-zinc-700"
                  >
                    <Check className="w-3.5 h-3.5" />
                  </button>
                </div>
              ) : (
                <div
                  onClick={() => setIsEditingName(true)}
                  className="flex items-center justify-between p-2.5 rounded-lg bg-zinc-950/70 border border-zinc-800 hover:border-zinc-700 cursor-pointer group transition-colors"
                >
                  <span className="text-xs font-medium text-zinc-200 truncate">{batchName}</span>
                  <Edit2 className="w-3.5 h-3.5 text-zinc-500 group-hover:text-zinc-300 transition-colors" />
                </div>
              )}
            </div>

            {/* 2. Target Profiles Selector Card */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">
                  Target Profiles
                </label>
                <button
                  type="button"
                  onClick={() => setShowProfileModal(true)}
                  className="text-xs text-zinc-400 hover:text-zinc-200 font-medium"
                >
                  Customize
                </button>
              </div>

              <div
                onClick={() => setShowProfileModal(true)}
                className="p-3 rounded-xl bg-zinc-950/70 border border-zinc-800 hover:border-zinc-700 cursor-pointer transition-colors flex items-center justify-between"
              >
                <div className="flex items-center gap-2.5">
                  <Users className="w-4 h-4 text-zinc-400 shrink-0" />
                  <div>
                    <div className="text-xs font-semibold text-zinc-200">
                      {selectedProfileIds.length} of {profiles.length} Accounts
                    </div>
                    <div className="text-[11px] text-zinc-500">
                      {selectedProfileIds.length === runningProfilesCount && runningProfilesCount > 0
                        ? 'All running accounts active'
                        : `${selectedProfileIds.length} ${liveMode ? 'profiles will broadcast' : 'accounts will post'}`}
                    </div>
                  </div>
                </div>

                <span className="px-2 py-1 rounded bg-zinc-800 text-[11px] font-medium text-zinc-300">
                  Edit
                </span>
              </div>
            </div>

            {/* 3. Execution Mode Segmented Control */}
            <div>
              <label className="block text-[11px] font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                {liveMode ? 'Start time' : 'Posting Schedule Mode'}
              </label>

              <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-zinc-950 border border-zinc-800">
                <button
                  type="button"
                  onClick={() => setExecutionMode('now')}
                  className={`py-2 px-3 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-all ${
                    executionMode === 'now'
                      ? 'bg-zinc-800 text-white border border-zinc-600 font-semibold'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <Zap className="w-3.5 h-3.5" />
                  <span>Start Now</span>
                </button>

                <button
                  type="button"
                  onClick={() => setExecutionMode('scheduled')}
                  className={`py-2 px-3 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-all ${
                    executionMode === 'scheduled'
                      ? 'bg-zinc-800 text-white border border-zinc-600 font-semibold'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <Calendar className="w-3.5 h-3.5" />
                  <span>Scheduled</span>
                </button>
              </div>

              <p className="text-[11px] text-zinc-500 mt-2 leading-relaxed">
                {executionMode === 'now'
                  ? 'Each account runs independently: finish, close its container, wait its iteration delay, then start the next iteration.'
                  : `The batch starts at ${startTime}. Each account advances independently after its own iteration delay, within Resource Mode limits.`}
              </p>
            </div>

            {liveMode && <section className="space-y-3 rounded-xl border border-rose-900/50 bg-zinc-900 p-4" aria-label="Live playback settings">
              <h3 className="text-sm font-semibold text-zinc-200">Playback</h3>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-xs text-zinc-400">Playback<Select ariaLabel="Live playback" value={loopVideo ? 'loop' : 'once'} onValueChange={value => { setLoopVideo(value === 'loop'); if (value === 'loop' && !durationMinutes) setDurationMinutes('60'); }} className="mt-1" options={[{value: 'once', label: 'Play once'}, {value: 'loop', label: 'Loop video'}]} /></label>
                <label className="text-xs text-zinc-400">Audio<Select ariaLabel="Live audio" value={muted ? 'muted' : 'original'} onValueChange={value => setMuted(value === 'muted')} className="mt-1" options={[{value: 'original', label: 'Original audio'}, {value: 'muted', label: 'Muted'}]} /></label>
              </div>
              <label className="block text-xs text-zinc-400">{loopVideo ? 'Stop after (minutes)' : 'Duration limit (minutes, optional)'}<input type="number" min={1} max={240} placeholder="Full video" value={durationMinutes} onChange={e => setDurationMinutes(e.target.value)} className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-white" /></label>
              <p className="text-[11px] text-zinc-500">{loopVideo ? 'Repeats until the duration limit or Stop Live.' : 'Ends with the video or duration limit.'} Maximum 4 hours. Settings apply to all videos in this batch.</p>
            </section>}
            {/* 4. Global AI Caption Spin Toggle */}
            <div className={`${liveMode ? 'hidden' : ''} pt-1 border-t border-zinc-800/80`}>
              <label htmlFor="ai-caption-spinning" className="flex items-center justify-between cursor-pointer py-1">
                <div>
                  <div className="text-xs font-medium text-zinc-200">
                    AI Caption Spinning
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    Applies to Photo and Reel captions
                  </div>
                </div>
                <Checkbox
                  id="ai-caption-spinning"
                  disabled={posts.length > 0 && posts.every(post => post.type === 'live')}
                  checked={aiSpinAll}
                  onCheckedChange={setAiSpinAll}
                />
              </label>
            </div>

            {/* 5. Collapsible Advanced Settings */}
            <div className="pt-1 border-t border-zinc-800/80">
              <button
                type="button"
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="w-full flex items-center justify-between text-xs font-semibold text-zinc-400 hover:text-zinc-200 transition-colors py-1"
              >
                <span>Advanced Schedule & Pacing</span>
                {showAdvanced ? (
                  <ChevronDown className="w-4 h-4" />
                ) : (
                  <ChevronRight className="w-4 h-4" />
                )}
              </button>

              {showAdvanced && (
                <div className="space-y-3 pt-3 animate-in fade-in duration-150 text-xs">
                  {executionMode === 'scheduled' ? (
                    <div>
                      <label className="block text-zinc-400 mb-1">Batch Start Time</label>
                      <input
                        type="time"
                        value={startTime}
                        onChange={(e) => setStartTime(e.target.value)}
                        className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-zinc-600"
                      />
                      <p className="text-[10px] text-zinc-500 mt-1">
                        This gates the first post only. Accounts run within Resource Mode limits, and posts advance in order.
                      </p>
                    </div>
                  ) : (
                    <div className="px-2.5 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-[11px] text-zinc-500">
                      The first iteration is eligible immediately.
                    </div>
                  )}

                  <div>
                    <label className="block text-zinc-400 mb-1">
                      Profile Stagger Delay (Seconds)
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={3600}
                      step={10}
                      value={staggerSeconds}
                      onChange={(e) => {
                        const val = parseInt(e.target.value, 10);
                        setStaggerSeconds(Number.isNaN(val) ? 60 : Math.max(0, val));
                      }}
                      className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500"
                    />
                  </div>

                  <div>
                    <label className="block text-zinc-400 mb-1">
                      Per-account Iteration Delay (Seconds)
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={3600}
                      step={10}
                      value={iterationDelaySeconds}
                      onChange={(e) => {
                        const val = parseInt(e.target.value, 10);
                        setIterationDelaySeconds(Number.isNaN(val) ? 60 : Math.max(0, val));
                      }}
                      className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500"
                    />
                    <p className="text-[10px] text-zinc-500 mt-1">
                      Pause after each account's task finishes before preparing or running its next iteration.
                    </p>
                  </div>

                  <div className={liveMode ? 'hidden' : ''}>
                    <label className="block text-zinc-400 mb-1">
                      {liveMode ? 'Session preparation (skipped for Live)' : 'Rolling Session Preparation'}
                    </label>
                    <Select
                      disabled={liveMode} value={liveMode ? 'off' : preparationMode}
                      onValueChange={(value) => setPreparationMode(value as 'off' | 'brief' | 'extended')}
                      ariaLabel="Rolling session preparation"
                      options={[
                        { value: 'brief', label: 'Brief browsing (40–55s) [Recommended]' },
                        { value: 'extended', label: 'Extended browsing (55–70s)' },
                        { value: 'off', label: 'Off' },
                      ]}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* 6. Campaign Summary & Primary Launch Button */}
            <div className="pt-2 border-t border-zinc-800/80 space-y-3">
              <div className="p-3 rounded-xl bg-zinc-950/80 border border-zinc-800 text-xs text-zinc-400">
                <strong className="text-zinc-200">{selectedProfileIds.length} profiles</strong>
                {' '}×{' '}
                <strong className="text-zinc-200">{posts.length} {liveMode ? 'videos' : 'posts'}</strong>
                {' '}={' '}
                <strong className="text-white">{totalExecutions} total executions</strong>
              </div>

              <button
                type="button"
                onClick={handleSubmitBatch}
                disabled={isSubmitting || selectedProfileIds.length === 0 || posts.length === 0}
                className="w-full py-3 px-4 rounded-xl font-semibold text-xs flex items-center justify-center gap-2 transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm"
              >
                {isSubmitting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : executionMode === 'now' ? (
                  <Zap className="w-4 h-4" />
                ) : (
                  <Calendar className="w-4 h-4" />
                )}
                <span>
                  {executionMode === 'now'
                    ? liveMode ? `Start Live (${totalExecutions} broadcasts)` : `Start Campaign Now (${totalExecutions} Posts)`
                    : liveMode ? `Schedule Live (${totalExecutions} broadcasts)` : `Schedule Campaign (${totalExecutions} Posts)`}
                </span>
              </button>
            </div>

          </div>
        </div>
      </div>

      {/* ============================================================
          PROFILE SELECTION MODAL
          ============================================================ */}
      {showProfileModal && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setShowProfileModal(false)}
        >
          <div
            role="dialog" aria-modal="true" aria-label="Choose batch profiles"
            className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-xl max-h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-150"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-blue-400" />
                  Choose batch profiles
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  Select which accounts will publish this daily batch.
                </p>
              </div>

              <button
                type="button"
                onClick={() => setShowProfileModal(false)}
                aria-label="Close batch profiles"
                className="p-1 rounded text-zinc-400 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="overflow-y-auto p-4">
              <GroupedProfileSelector disabledProfileIds={hasLive ? profiles.filter(p => !p.live?.configured).map(p => p.id) : []} profiles={profiles} selectedIds={selectedProfileIds} onChange={setSelectedProfileIds} />
            </div>

            {/* Modal Footer */}
            <div className="p-4 border-t border-zinc-800 flex items-center justify-between">
              <span className="text-xs text-zinc-400 font-medium">
                {selectedProfileIds.length} profiles selected
              </span>
              <button
                type="button"
                onClick={() => setShowProfileModal(false)}
                className="px-5 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-md transition-colors"
              >
                Done
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
            {/* Header */}
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

            {/* Media View */}
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

            {/* Caption & First Comment */}
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
