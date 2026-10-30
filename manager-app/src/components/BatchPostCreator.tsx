import { CaptionHookEditor, HookDraft, hookIsApproved } from "./CaptionHookEditor";
import { LivePlaybackSettings, LivePlaybackMode } from './LivePlaybackSettings';
import { GroupedProfileSelector } from './GroupedProfileSelector';
import { StageBadge, stageView, useAccountHealth } from './accounts/accountHealth';
import { WarmingSettings, defaultWarmingOptions } from './WarmingSettings';
import { MediaLightbox, LightboxMedia } from './MediaLightbox';
import React, { useState, useEffect, useMemo } from 'react';
import { Profile } from '../types/profile';
import { CreateBatchParams, WarmingOptions } from '../types/automation';
import type { CreatedReelMedia } from '../types/reelCreator';
import { createBatch, uploadMediaFiles, getSharedMediaUrl, fetchQueue } from '../services/api';
import {
  UploadCloud,
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
  ChevronDown,
  ChevronRight,
  Users,
  Plus,
  Flame,
} from 'lucide-react';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';
import { ActionMenu } from './ui/ActionMenu';
import { useAppDialog } from './ui/AppDialogProvider';
import { useFileDrop } from '../hooks/useFileDrop';

export type CampaignKind = 'posts' | 'live' | 'warmup';

interface BatchPostCreatorProps {
  profiles: Profile[];
  onBatchCreated: () => void;
  mode?: CampaignKind;
  incomingReel?: CreatedReelMedia | null;
  onIncomingReelConsumed?: () => void;
  onEditProfile?: (profileId: string) => void;
}

const STORAGE_PREFIX: Record<CampaignKind, string> = { posts: 'batch_creator', live: 'live_creator', warmup: 'warmup_creator' };
const DEFAULT_NAME: Record<CampaignKind, string> = { posts: 'Daily batch', live: 'Live batch', warmup: 'Warm-up' };

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
  hook?: HookDraft;
}

export const BatchPostCreator: React.FC<BatchPostCreatorProps> = ({
  profiles,
  onBatchCreated,
  mode = 'posts',
  incomingReel,
  onIncomingReelConsumed,
  onEditProfile,
}) => {
  const { showToast } = useAppDialog();
  const liveMode = mode === 'live';
  const warmupMode = mode === 'warmup';
  const storageKey = (name: string) => `${STORAGE_PREFIX[mode]}_${name}`;
  const [scrolls, setScrolls] = useState(() => Math.max(1, Number(localStorage.getItem(storageKey('scrolls'))) || 4));
  const [warmingOptions, setWarmingOptions] = useState<WarmingOptions>(() => {
    try { return { ...defaultWarmingOptions, ...JSON.parse(localStorage.getItem(storageKey('warming_options')) || '{}') }; }
    catch (_) { return defaultWarmingOptions; }
  });
  const [playbackMode, setPlaybackMode] = useState<LivePlaybackMode>(() => localStorage.getItem(storageKey('playback_mode')) === 'manual' ? 'manual' : 'auto');
  const [loopVideo, setLoopVideo] = useState(() => localStorage.getItem(storageKey('loop')) === 'true');
  const [durationMinutes, setDurationMinutes] = useState(() => localStorage.getItem(storageKey('duration_minutes')) || '');
  const [muted, setMuted] = useState(() => localStorage.getItem(storageKey('muted')) === 'true');

  // Campaign Name with inline editing
  const [batchName, setBatchName] = useState(() => {
    return (
      localStorage.getItem(storageKey('batch_name')) ||
      `${DEFAULT_NAME[mode]} ${new Date().toLocaleDateString()}`
    );
  });

  // Selected Target Profiles
  const [savedProfileIds, setSelectedProfileIds] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(storageKey('profiles'));
      if (saved) { const ids = JSON.parse(saved); if (Array.isArray(ids)) return ids.filter((id): id is string => typeof id === "string"); }
    } catch (_) {}
    return profiles.filter((p) => p.status === 'running').map((p) => p.id);
  });
  const [showProfileModal, setShowProfileModal] = useState(false);
  const { health: accountHealth } = useAccountHealth();

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
    return saved !== null ? saved === 'true' : false;
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

  useEffect(() => {
    if (!incomingReel || liveMode !== (incomingReel.postType === 'live')) return;
    const live = incomingReel.postType === 'live';
    const id = `${incomingReel.postType === 'photo' ? 'photo' : 'reel'}_${incomingReel.jobId}`;
    setPosts(current => current.some(row => row.id === id) ? current.map(row => row.id === id ? { ...row, type: incomingReel.postType } : row) : [...current, {
      id, type: incomingReel.postType, media_file: incomingReel.filename, media_name: incomingReel.name,
      preview_url: getSharedMediaUrl(incomingReel.filename), caption: incomingReel.caption || '',
      // A Live video is titled by its headline; the read-more link is pinned once the broadcast starts.
      ...(live ? { title: incomingReel.name, pinned_comment: incomingReel.comment || '', first_comment: '' } : { first_comment: incomingReel.comment || '' }),
    }]);
    onIncomingReelConsumed?.();
  }, [incomingReel, liveMode, onIncomingReelConsumed]);

  const hasLive = liveMode || posts.some(post => post.type === 'live');
  const selectedProfileIds = savedProfileIds.filter(id => profiles.some(p => p.id === id && (!hasLive || p.live?.configured)));

  // UI state
  const [isUploading, setIsUploading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const setSuccessMsg = (message: string) => showToast(message, 'success');
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);

  // Accounts that already have queued or running work start this batch only after it.
  const [busyProfileIds, setBusyProfileIds] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const queue = await fetchQueue();
        if (!cancelled) setBusyProfileIds([...new Set(queue.executions
          .filter(item => ['pending', 'ready', 'running', 'preparing'].includes(item.status)).map(item => item.profile_id))]);
      } catch (_) {}
    };
    load();
    const interval = setInterval(load, 10000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    localStorage.setItem(storageKey('scrolls'), String(scrolls));
    localStorage.setItem(storageKey('warming_options'), JSON.stringify(warmingOptions));
  }, [scrolls, warmingOptions]);

  useEffect(() => {
    localStorage.setItem(storageKey('playback_mode'), playbackMode);
    localStorage.setItem(storageKey('loop'), String(loopVideo));
    localStorage.setItem(storageKey('duration_minutes'), durationMinutes);
    localStorage.setItem(storageKey('muted'), String(muted));
  }, [playbackMode, loopVideo, durationMinutes, muted]);

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
  const importMedia = async (files: File[]) => {
    if (!files.length || isUploading) return;
    setErrorMsg(null);
    const unsupported = files.find(file => !/\.(mp4|mov|webm)$/i.test(file.name) && (liveMode || !(file.type.startsWith('image/') || /\.(jpe?g|png|webp|avif|gif|bmp|tiff?|jfif)$/i.test(file.name))));
    if (unsupported) {
      setErrorMsg(`${unsupported.name}: ${liveMode ? 'Choose an MP4, MOV or WebM video.' : 'Choose photos or MP4, MOV or WebM videos.'}`);
      return;
    }
    setIsUploading(true);

    try {
      const formData = new FormData();
      files.forEach((file) => {
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
      setSuccessMsg(`Added ${res.files.length} file${res.files.length === 1 ? '' : 's'}.`);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to upload media files');
    } finally {
      setIsUploading(false);
    }
  };
  const handleBulkMediaUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.currentTarget.files || []);
    e.currentTarget.value = '';
    void importMedia(files);
  };
  const mediaDrop = useFileDrop(files => { void importMedia(files); }, isUploading);

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
                  type: liveMode || row.type === 'live' && uploaded.type === 'reel' ? 'live'
                    : uploaded.type === 'reel' && row.type === 'photo' ? 'photo' : uploaded.type,
                }
              : row
          )
        );
        setSuccessMsg(`Attached ${uploaded.original_name}.`);
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
        setSuccessMsg(`Imported ${importedRows.length} posts from ${file.name}.`);
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

  // Everything that would stop the batch from being created, shown in the summary before submitting.
  const problems = useMemo(() => {
    const list: string[] = [];
    if (selectedProfileIds.length === 0) list.push('Choose at least one account.');
    if (warmupMode) return list;
    if (posts.length === 0) list.push(liveMode ? 'Add at least one video.' : 'Add at least one post.');
    if (posts.some(p => p.type === 'live' && (!(p.title || '').trim() || !p.caption.trim()))) list.push('Enter a title and caption for each Live video.');
    if (posts.some(p => p.type === 'live' && !p.media_file)) list.push('Attach a video to every Live item.');
    if (liveMode && playbackMode === 'manual' && ((loopVideo && !durationMinutes) || (durationMinutes !== '' && (!Number.isInteger(Number(durationMinutes)) || Number(durationMinutes) < 1 || Number(durationMinutes) > 240)))) {
      list.push('Set a duration between 1 and 240 minutes. Loop needs a duration.');
    }
    if (posts.some(p => !p.caption.trim() && !p.media_file)) list.push('Each post needs a photo, video, or caption.');
    if (posts.some(p => !hookIsApproved(p.caption, p.media_file, p.type, p.hook))) list.push('Preview and approve each caption hook, or keep the original caption.');
    return list;
  }, [selectedProfileIds.length, warmupMode, liveMode, posts, playbackMode, loopVideo, durationMinutes]);

  const handleSubmitBatch = async () => {
    setErrorMsg(null);
    if (problems.length) {
      setErrorMsg(problems[0]);
      return;
    }
    setIsSubmitting(true);
    const startNow = executionMode === 'now';

    try {
      const payload: CreateBatchParams = {
        name: batchName.trim() || `${DEFAULT_NAME[mode]} ${new Date().toLocaleDateString()}`,
        target_profiles: selectedProfileIds,
        start_now: startNow,
        schedule_window: {
          start_time: startTime,
          end_time: endTime,
          profile_stagger_seconds: staggerSeconds,
          batch_iteration_delay_seconds: iterationDelaySeconds,
          session_preparation_mode: liveMode || warmupMode ? 'off' : preparationMode,
          start_now: startNow,
        },
        posts: warmupMode
          ? [{ type: 'warming', media_file: '', base_caption: '', scrolls: Math.max(1, scrolls), warming_options: warmingOptions, ai_spin: false }]
          : posts.map((p) => ({
            type: p.type,
            ...(p.type === 'live' ? {title: (p.title || '').trim(), pinned_comment: (p.pinned_comment || '').trim(), playback_mode: playbackMode, loop: playbackMode === 'manual' && loopVideo, muted, max_duration_seconds: playbackMode === 'manual' && durationMinutes ? Number(durationMinutes) * 60 : undefined} : {}),
            media_file: p.media_file,
            base_caption: p.caption,
            ...(p.hook?.enabled ? { hook_approval: p.hook.approval } : {}),
            first_comment: p.type === 'live' ? undefined : p.first_comment.trim() || undefined,
            ai_spin: p.type === 'live' || p.hook?.enabled ? false : aiSpinAll,
          })),
      };

      const res = await createBatch(payload);
      setSuccessMsg(`Created “${res.batch.name}” with ${res.total_executions} jobs.`);
      if (!warmupMode) {
        setPosts([]);
        localStorage.removeItem(storageKey('draft_posts'));
      }
      onBatchCreated();
    } catch (err: any) {
      setErrorMsg(err.message || 'Couldn’t create the campaign.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const itemCount = warmupMode ? 1 : posts.length;
  const totalExecutions = selectedProfileIds.length * itemCount;
  const selectedProfiles = profiles.filter(p => selectedProfileIds.includes(p.id));
  const busySelected = selectedProfiles.filter(p => busyProfileIds.includes(p.id));
  const erroredSelected = selectedProfiles.filter(p => p.status === 'error');
  const liveSetupMissing = hasLive ? profiles.filter(p => !p.live?.configured) : [];
  const stages = Object.fromEntries(profiles.map(p => [p.id, stageView(p, accountHealth[p.id])]));
  // Advice only: the user can keep any of these accounts.
  const stageWarnings = selectedProfiles.flatMap(p => {
    const view = stages[p.id];
    if (view.stage === 'attention') return [{ profile: p, view, note: view.reason }];
    if (warmupMode) return [];
    if (view.stage === 'new' || view.stage === 'resting') return [{ profile: p, view, note: view.reason }];
    const left = view.health ? view.health.daily_limit - view.health.posts_today : Infinity;
    return itemCount > left ? [{ profile: p, view, note: `${Math.max(0, left)} suggested post${left === 1 ? '' : 's'} left today; this batch has ${itemCount}.` }] : [];
  });


  const startLabel = executionMode === 'now' ? 'Starts now' : `Starts at ${startTime}`;
  const submitLabel = `${executionMode === 'now' ? 'Start' : 'Schedule'} ${totalExecutions} ${warmupMode ? 'warm-up' : liveMode ? 'broadcast' : 'job'}${totalExecutions === 1 ? '' : 's'}`;
  const sectionTitle = 'text-[11px] font-semibold text-zinc-400 uppercase tracking-wider';
  const card = 'p-4 rounded-2xl bg-zinc-900/60 border border-zinc-800/80';
  const jsonInput = React.useRef<HTMLInputElement>(null);

  return (
    <div className="max-w-7xl mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        {/* Left: what will be posted */}
        <div className="lg:col-span-7 xl:col-span-8 space-y-4">
          {warmupMode ? (
            <div className={`${card} space-y-3`}>
              <div className="flex items-start gap-3">
                <div className="p-2 rounded-lg bg-orange-950/40 border border-orange-900/60 text-orange-400"><Flame className="w-4 h-4" /></div>
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">Warm-up only</h3>
                  <p className="text-xs text-zinc-400 mt-0.5">Each account browses its feed or profile like a person would, with varied scrolling, reading pace, and breaks. Nothing is posted.</p>
                </div>
              </div>
              <div className="p-3 rounded-xl bg-zinc-950 border border-zinc-800">
                <WarmingSettings scrolls={scrolls} onScrollsChange={setScrolls} value={warmingOptions} onChange={setWarmingOptions} />
              </div>
            </div>
          ) : <>
            <div className={`${card} space-y-3`}>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">
                    {liveMode ? 'Videos to broadcast' : 'Content'} <span className="text-xs font-normal text-zinc-500">· {posts.length} {posts.length === 1 ? 'item' : 'items'}</span>
                  </h3>
                  <p className="text-xs text-zinc-400 mt-0.5">
                    {liveMode ? 'Each account broadcasts with its own saved streaming settings.' : 'Videos become Reels by default. Switch an item to Feed post to use the feed composer.'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button type="button" onClick={handleAddEmptyRow}
                    className="px-2.5 py-1.5 rounded-lg border border-zinc-700 text-xs text-zinc-300 hover:bg-zinc-800 flex items-center gap-1.5 transition-colors">
                    <Plus className="w-3.5 h-3.5" /><span>{liveMode ? 'Add video' : 'Add text post'}</span>
                  </button>
                  {!liveMode && <>
                    <input ref={jsonInput} type="file" accept=".json,application/json" onChange={handleJsonUpload} className="hidden" />
                    <ActionMenu ariaLabel="More content options" items={[
                      { label: 'Import posts from JSON', icon: <FileCode className="w-3.5 h-3.5" />, onSelect: () => jsonInput.current?.click() },
                      ...(posts.length ? [{ label: 'Clear all items', icon: <Trash2 className="w-3.5 h-3.5" />, danger: true, onSelect: () => setPosts([]) }] : []),
                    ]} />
                  </>}
                </div>
              </div>

              <label {...mediaDrop.handlers} className={`border-2 border-dashed rounded-xl p-5 flex flex-col items-center justify-center cursor-pointer transition-all group ${mediaDrop.dragging ? 'border-blue-400 bg-blue-500/10' : 'border-zinc-800 hover:border-blue-500 bg-zinc-950/40'}`}>
                <input type="file" multiple accept={liveMode ? ".mp4,.mov,.webm" : "image/*,video/mp4,video/quicktime,video/webm"}
                  onChange={handleBulkMediaUpload} className="hidden" disabled={isUploading} />
                {isUploading ? (
                  <span className="flex items-center gap-2 text-xs text-zinc-400"><Loader2 className="w-5 h-5 text-blue-400 animate-spin" />Uploading…</span>
                ) : <>
                  <UploadCloud className="w-7 h-7 text-zinc-500 group-hover:text-blue-400 transition-colors mb-1.5" />
                  <span className="text-xs font-semibold text-zinc-200">
                    {mediaDrop.dragging ? 'Release to add your files' : liveMode ? 'Drop videos here, or click to browse' : 'Drop photos or videos here, or click to browse'}
                  </span>
                  <span className="text-[11px] text-zinc-500 mt-1">{liveMode ? 'MP4, MOV, or WebM' : 'Photos · MP4, MOV, or WebM videos'}</span>
                </>}
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
                        disabled={liveMode} options={liveMode ? [{value: 'live', label: 'Live'}] : [{value: 'photo', label: 'Feed post'}, {value: 'reel', label: 'Reel'}]}
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
                          {post.type !== 'photo' || /\.(mp4|mov|webm)$/i.test(post.media_file) ? (
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

                      <CaptionHookEditor caption={post.caption} media={post.media_file} type={post.type}
                        value={post.hook} recentHooks={posts.filter(p => p.id !== post.id && p.hook?.approval).map(p => p.hook?.selected || '')}
                        onChange={hook => handleUpdateRow(post.id, { hook })} />
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
              {liveMode ? 'Drop videos above or add a video to start.' : 'Drop photos or videos above, or add a text post.'}
            </div>
          )}
          </>}
        </div>

        {/* Right: who, when, and a summary of what will happen */}
        <div className="lg:col-span-5 xl:col-span-4 space-y-4">
          <div className={`${card} space-y-3`}>
            <div className="flex items-center justify-between">
              <span className={sectionTitle}>Accounts</span>
              <button type="button" onClick={() => setShowProfileModal(true)} className="text-xs text-blue-400 hover:text-blue-300">Choose</button>
            </div>
            <button type="button" onClick={() => setShowProfileModal(true)}
              className="w-full p-3 rounded-xl bg-zinc-950/70 border border-zinc-800 hover:border-zinc-700 transition-colors flex items-center gap-2.5 text-left">
              <Users className="w-4 h-4 text-zinc-400 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-semibold text-zinc-200">{selectedProfileIds.length} of {profiles.length} accounts</span>
                <span className="block truncate text-[11px] text-zinc-500">
                  {selectedProfiles.length ? selectedProfiles.slice(0, 3).map(p => p.name).join(', ') + (selectedProfiles.length > 3 ? ` +${selectedProfiles.length - 3}` : '') : 'None selected'}
                </span>
              </span>
              <ChevronRight className="w-4 h-4 text-zinc-500" />
            </button>
            {stageWarnings.length > 0 && (
              <details className="text-[11px] text-amber-300">
                <summary className="cursor-pointer">{stageWarnings.length} selected account{stageWarnings.length === 1 ? ' isn’t' : 's aren’t'} suggested for {warmupMode ? 'this' : 'posting'} right now</summary>
                <ul className="mt-2 space-y-1.5">
                  {stageWarnings.slice(0, 8).map(({ profile, view, note }) => <li key={profile.id} className="flex items-start gap-2 text-zinc-300">
                    <StageBadge view={view} compact className="mt-0.5" />
                    <span className="min-w-0 flex-1"><span className="block truncate">{profile.name}</span><span className="block text-zinc-500">{note}</span></span>
                  </li>)}
                </ul>
                <div className="mt-2 flex flex-wrap gap-3">
                  <button type="button" className="text-blue-400 hover:text-blue-300"
                    onClick={() => setSelectedProfileIds(selectedProfileIds.filter(id => !stageWarnings.some(w => w.profile.id === id)))}>Remove them</button>
                  <span className="text-zinc-500">Or keep them: this is only a suggestion.</span>
                </div>
              </details>
            )}
            {liveSetupMissing.length > 0 && (
              <details className="text-[11px] text-amber-300">
                <summary className="cursor-pointer">{liveSetupMissing.length} account{liveSetupMissing.length === 1 ? ' needs' : 's need'} Live setup first</summary>
                <ul className="mt-2 space-y-1">
                  {liveSetupMissing.slice(0, 8).map(p => <li key={p.id} className="flex items-center justify-between gap-2 text-zinc-300">
                    <span className="truncate">{p.name}</span>
                    {onEditProfile && <button type="button" className="shrink-0 text-blue-400 hover:text-blue-300" onClick={() => onEditProfile(p.id)}>Set up</button>}
                  </li>)}
                </ul>
              </details>
            )}
          </div>

          <div className={`${card} space-y-3`}>
            <span className={sectionTitle}>When</span>
            <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-zinc-950 border border-zinc-800">
              {(['now', 'scheduled'] as const).map(option => <button key={option} type="button" onClick={() => setExecutionMode(option)}
                className={`py-2 px-3 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-all ${executionMode === option ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}>
                {option === 'now' ? <Zap className="w-3.5 h-3.5" /> : <Calendar className="w-3.5 h-3.5" />}
                <span>{option === 'now' ? 'Start now' : 'Schedule'}</span>
              </button>)}
            </div>
            {executionMode === 'scheduled' && (
              <label className="block text-xs text-zinc-400">
                <span className="mb-1 block">First job at</span>
                <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500" />
              </label>
            )}
            {!liveMode && !warmupMode && (
              <div className="space-y-1.5">
                <span className="block text-xs text-zinc-400">Warm up before posting</span>
                <Select value={preparationMode} onValueChange={(value) => setPreparationMode(value as 'off' | 'brief' | 'extended')} ariaLabel="Warm up before posting"
                  options={[
                    { value: 'brief', label: 'Short browse, 40–55s (recommended)' },
                    { value: 'extended', label: 'Longer browse, 55–70s' },
                    { value: 'off', label: 'Don’t warm up' },
                  ]} />
              </div>
            )}
            {liveMode && <LivePlaybackSettings mode={playbackMode} onMode={setPlaybackMode}
              loop={loopVideo} onLoop={setLoopVideo} minutes={durationMinutes} onMinutes={setDurationMinutes}
              muted={muted} onMuted={setMuted} />}

            <div className="pt-1 border-t border-zinc-800/80">
              <button type="button" onClick={() => setShowAdvanced(!showAdvanced)} aria-expanded={showAdvanced}
                className="w-full flex items-center justify-between text-xs text-zinc-400 hover:text-zinc-200 transition-colors py-1">
                <span>Pacing · {staggerSeconds}s between accounts · {iterationDelaySeconds}s rest</span>
                {showAdvanced ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
              {showAdvanced && (
                <div className="space-y-3 pt-3 text-xs">
                  <label className="block">
                    <span className="block text-zinc-400 mb-1">Seconds between accounts starting</span>
                    <input type="number" min={0} max={3600} step={10} value={staggerSeconds}
                      onChange={(e) => { const val = parseInt(e.target.value, 10); setStaggerSeconds(Number.isNaN(val) ? 60 : Math.max(0, val)); }}
                      className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500" />
                  </label>
                  <label className="block">
                    <span className="block text-zinc-400 mb-1">Seconds each account rests between jobs</span>
                    <input type="number" min={0} max={3600} step={10} value={iterationDelaySeconds}
                      onChange={(e) => { const val = parseInt(e.target.value, 10); setIterationDelaySeconds(Number.isNaN(val) ? 60 : Math.max(0, val)); }}
                      className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500" />
                  </label>
                  {!liveMode && !warmupMode && (
                    <label htmlFor="ai-caption-spinning" className="flex items-center justify-between gap-3 cursor-pointer">
                      <span>
                        <span className="block text-zinc-200">Vary captions per account</span>
                        <span className="block text-[11px] text-zinc-500">Adds canned text to each copy. Approved hook captions are left alone.</span>
                      </span>
                      <Checkbox id="ai-caption-spinning" checked={aiSpinAll} onCheckedChange={setAiSpinAll} />
                    </label>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className={`${card} space-y-3`}>
            <span className={sectionTitle}>Summary</span>
            <input value={batchName} onChange={(e) => setBatchName(e.target.value)} aria-label="Campaign name" maxLength={120}
              className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-100 focus:outline-none focus:border-blue-500" />
            <div className="text-xs text-zinc-400">
              <p>{itemCount} {warmupMode ? 'warm-up' : itemCount === 1 ? 'item' : 'items'} × {selectedProfileIds.length} accounts</p>
              <p className="mt-1 text-2xl font-semibold text-zinc-100">{totalExecutions} <span className="text-sm font-normal text-zinc-400">{warmupMode ? 'warm-ups' : liveMode ? 'broadcasts' : 'jobs'}</span></p>
              <p className="mt-1">{startLabel}</p>
            </div>
            <ul className="space-y-1.5 text-[11px]">
              {problems.map(problem => <li key={problem} className="flex items-start gap-1.5 text-red-300"><X className="w-3.5 h-3.5 shrink-0" />{problem}</li>)}
              {busySelected.length > 0 && <li className="flex items-start gap-1.5 text-amber-300"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                {busySelected.length === 1 ? `${busySelected[0].name} is busy` : `${busySelected.length} accounts are busy`} with other queued work. They’ll start this after it.</li>}
              {erroredSelected.length > 0 && <li className="flex items-start gap-1.5 text-amber-300"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                {erroredSelected.length === 1 ? `${erroredSelected[0].name} has an error` : `${erroredSelected.length} accounts have errors`}. Check them on the Profiles page.</li>}
              {problems.length === 0 && busySelected.length === 0 && erroredSelected.length === 0 && <li className="flex items-start gap-1.5 text-emerald-300"><CheckCircle2 className="w-3.5 h-3.5 shrink-0" />Ready to go</li>}
            </ul>
            {errorMsg && <p role="alert" className="flex items-start gap-1.5 rounded-lg border border-red-900 bg-red-950/50 p-2 text-[11px] text-red-200"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{errorMsg}</p>}
            <button type="button" onClick={handleSubmitBatch} disabled={isSubmitting}
              className="w-full py-2.5 px-4 rounded-xl font-semibold text-xs flex items-center justify-center gap-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed bg-blue-600 hover:bg-blue-500 text-white">
              {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : executionMode === 'now' ? <Zap className="w-4 h-4" /> : <Calendar className="w-4 h-4" />}
              <span>{submitLabel}</span>
            </button>
          </div>
        </div>
      </div>

      {showProfileModal && (
        <div
          className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setShowProfileModal(false)}
        >
          <div
            role="dialog" aria-modal="true" aria-label="Choose accounts"
            className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-xl max-h-[85vh] flex flex-col shadow-2xl animate-in fade-in zoom-in-95 duration-150"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-blue-400" />
                  Choose accounts
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  Pick the accounts that will run this campaign.
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
              <GroupedProfileSelector stages={stages} busyProfileIds={busyProfileIds} disabledProfileIds={hasLive ? profiles.filter(p => !p.live?.configured).map(p => p.id) : []} profiles={profiles} selectedIds={selectedProfileIds} onChange={setSelectedProfileIds} />
            </div>

            {/* Modal Footer */}
            <div className="p-4 border-t border-zinc-800 flex items-center justify-between">
              <span className="text-xs text-zinc-400 font-medium">
                {selectedProfileIds.length} accounts selected
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

      <MediaLightbox media={lightboxMedia} onClose={() => setLightboxMedia(null)} />
    </div>
  );
};
