import React, { useState, useEffect } from 'react';
import { Profile } from '../types/profile';
import { AutomationTaskState } from '../types/automation';
import { runAutomation, fetchAutomationTasks, stopAutomation, uploadMediaFiles } from '../services/api';
import { BatchPostCreator } from './BatchPostCreator';
import { PostingQueuePanel } from './PostingQueuePanel';
import {
  Flame,
  Send,
  Square,
  Sparkles,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Terminal,
  ShieldCheck,
  RefreshCw,
  Layers,
  PanelLeftClose,
  PanelLeft,
  Calendar,
  Clock,
  Image as ImageIcon,
  Film,
  X,
  Play,
} from 'lucide-react';

interface CampaignsPanelProps {
  profiles: Profile[];
  onSelectProfile: (profileId: string) => void;
  isSidebarOpen?: boolean;
  onToggleSidebar?: () => void;
}

export const CampaignsPanel: React.FC<CampaignsPanelProps> = ({
  profiles,
  onSelectProfile,
  isSidebarOpen,
  onToggleSidebar,
}) => {
  const [activeMainTab, setActiveMainTab] = useState<'batch_creator' | 'queue_monitor' | 'instant'>('batch_creator');
  const [taskType, setTaskType] = useState<'warming' | 'post'>('warming');
  const [selectedProfileIds, setSelectedProfileIds] = useState<string[]>([]);
  const [scrolls, setScrolls] = useState<number>(4);
  const [caption, setCaption] = useState<string>('');
  const [commentLink, setCommentLink] = useState<string>('');
  const [attachedMedia, setAttachedMedia] = useState<{
    filename: string;
    original_name: string;
    type: 'photo' | 'reel';
    url: string;
  } | null>(null);
  const [isUploadingMedia, setIsUploadingMedia] = useState<boolean>(false);
  const [isLaunching, setIsLaunching] = useState<boolean>(false);
  const [activeTasks, setActiveTasks] = useState<Record<string, AutomationTaskState>>({});
  const [selectedTaskProfileId, setSelectedTaskProfileId] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);

  const runningProfiles = profiles.filter((p) => p.status === 'running');

  // Load active tasks periodically
  const loadTasks = async () => {
    try {
      const data = await fetchAutomationTasks();
      setActiveTasks(data);
    } catch (_) {}
  };

  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 1500);
    return () => clearInterval(interval);
  }, []);

  const handleSelectAllRunning = () => {
    if (selectedProfileIds.length === runningProfiles.length) {
      setSelectedProfileIds([]);
    } else {
      setSelectedProfileIds(runningProfiles.map((p) => p.id));
    }
  };

  const toggleProfileSelection = (id: string) => {
    setSelectedProfileIds((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    );
  };

  const handleMediaUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    setErrorBanner(null);
    setIsUploadingMedia(true);
    try {
      const file = e.target.files[0];
      const formData = new FormData();
      formData.append('files', file);
      const res = await uploadMediaFiles(formData);
      if (res.files && res.files.length > 0) {
        const up = res.files[0];
        setAttachedMedia({
          filename: up.filename,
          original_name: up.original_name || up.filename,
          type: up.type,
          url: `/shared_media/${up.filename}`,
        });
      }
    } catch (err: any) {
      setErrorBanner(err.message || 'Failed to upload media file');
    } finally {
      setIsUploadingMedia(false);
      e.target.value = '';
    }
  };

  const handleLaunchCampaign = async () => {
    setErrorBanner(null);
    setSuccessBanner(null);

    if (selectedProfileIds.length === 0) {
      setErrorBanner('Please select at least one running container profile.');
      return;
    }

    if (taskType === 'post' && !caption.trim() && !attachedMedia) {
      setErrorBanner('Either a post caption or media attachment (photo/video) is required.');
      return;
    }

    setIsLaunching(true);
    let launched = 0;
    const errors: string[] = [];
    const effectiveTask = taskType === 'warming'
      ? 'warming'
      : (attachedMedia?.type === 'reel' ? 'reel' : 'post');

    for (const profileId of selectedProfileIds) {
      try {
        await runAutomation({
          profile_id: profileId,
          task: effectiveTask,
          scrolls: taskType === 'warming' ? scrolls : undefined,
          caption: taskType === 'post' && caption.trim() ? caption.trim() : undefined,
          comment_link: taskType === 'post' && commentLink.trim() ? commentLink.trim() : undefined,
          media: taskType === 'post' && attachedMedia ? attachedMedia.filename : undefined,
        });
        launched++;
      } catch (err: any) {
        errors.push(`${profileId}: ${err.message}`);
      }
    }

    setIsLaunching(false);
    await loadTasks();

    if (errors.length > 0) {
      setErrorBanner(`Launched ${launched} tasks. Failed on: ${errors.join('; ')}`);
    } else {
      const taskLabel = taskType === 'warming'
        ? 'Feed Warming'
        : attachedMedia?.type === 'reel'
        ? 'Reel Posting'
        : attachedMedia
        ? 'Photo Post'
        : 'Text Post';
      setSuccessBanner(`Successfully launched "${taskLabel}" across ${launched} profile(s)!`);
      if (taskType === 'post') {
        setCaption('');
        setCommentLink('');
        setAttachedMedia(null);
      }
    }
  };

  const handleStopTask = async (profileId: string) => {
    try {
      await stopAutomation(profileId);
      await loadTasks();
    } catch (err: any) {
      alert(`Failed to stop: ${err.message}`);
    }
  };

  const inspectedTask = selectedTaskProfileId ? activeTasks[selectedTaskProfileId] : null;

  return (
    <div className="flex-1 flex flex-col h-full bg-background overflow-hidden">
      {/* Top Header */}
      <header className="h-14 border-b border-border px-6 flex items-center justify-between shrink-0 bg-surface/50">
        <div className="flex items-center gap-3">
          {onToggleSidebar && (
            <button
              onClick={onToggleSidebar}
              className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors mr-1"
              title={isSidebarOpen ? 'Collapse Profiles' : 'Expand Profiles'}
            >
              {isSidebarOpen ? (
                <PanelLeftClose className="w-4 h-4" />
              ) : (
                <PanelLeft className="w-4 h-4" />
              )}
            </button>
          )}
          <div className="p-2 rounded-lg bg-zinc-900 border border-zinc-800 text-emerald-400">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-zinc-100 flex items-center gap-2">
              Zero-CDP Facebook Campaign Center
              <span className="flex items-center gap-1 text-[11px] font-normal text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/30">
                <ShieldCheck className="w-3 h-3" /> 0% Bot Detection
              </span>
            </h2>
            <p className="text-xs text-zinc-400">
              Host-driven X11 input dispatch with Bezier curve easing, micro-jitter, and visual verification.
            </p>
          </div>
        </div>

        <button
          onClick={loadTasks}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-300 hover:text-white text-xs font-medium transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Refresh</span>
        </button>
      </header>

      {/* Notification Banners */}
      {errorBanner && (
        <div className="mx-6 mt-4 p-3 rounded-lg bg-rose-950/60 border border-rose-800/80 text-rose-300 text-xs flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{errorBanner}</span>
          </div>
          <button onClick={() => setErrorBanner(null)} className="text-rose-400 hover:text-white font-bold ml-4">
            ×
          </button>
        </div>
      )}

      {successBanner && (
        <div className="mx-6 mt-4 p-3 rounded-lg bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 text-xs flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>{successBanner}</span>
          </div>
          <button onClick={() => setSuccessBanner(null)} className="text-emerald-400 hover:text-white font-bold ml-4">
            ×
          </button>
        </div>
      )}

      {/* Sub-Tab Navigation Bar */}
      <div className="flex items-center gap-2 border-b border-border px-6 pt-2 bg-surface/40 shrink-0">
        <button
          type="button"
          onClick={() => setActiveMainTab('batch_creator')}
          className={`px-4 py-2.5 text-xs font-semibold border-b-2 transition-all flex items-center gap-2 ${
            activeMainTab === 'batch_creator'
              ? 'border-blue-500 text-blue-400 bg-blue-950/20'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Calendar className="w-3.5 h-3.5 text-blue-400" />
          <span>📅 Daily Batch Creator</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveMainTab('queue_monitor')}
          className={`px-4 py-2.5 text-xs font-semibold border-b-2 transition-all flex items-center gap-2 ${
            activeMainTab === 'queue_monitor'
              ? 'border-emerald-500 text-emerald-400 bg-emerald-950/20'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Clock className="w-3.5 h-3.5 text-emerald-400" />
          <span>⏳ Posting Queue & Schedule</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveMainTab('instant')}
          className={`px-4 py-2.5 text-xs font-semibold border-b-2 transition-all flex items-center gap-2 ${
            activeMainTab === 'instant'
              ? 'border-amber-500 text-amber-400 bg-amber-950/20'
              : 'border-transparent text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Flame className="w-3.5 h-3.5 text-amber-400" />
          <span>⚡ Instant Multi-Runner</span>
        </button>
      </div>

      <div className={`flex-1 overflow-y-auto p-6 ${activeMainTab === 'batch_creator' ? '' : 'hidden'}`}>
        <BatchPostCreator
          profiles={profiles}
          onBatchCreated={() => setActiveMainTab('queue_monitor')}
        />
      </div>

      <div className={`flex-1 overflow-y-auto p-6 ${activeMainTab === 'queue_monitor' ? '' : 'hidden'}`}>
        <PostingQueuePanel profiles={profiles} />
      </div>

      <div className={`flex-1 grid grid-cols-12 gap-6 p-6 overflow-hidden ${activeMainTab === 'instant' ? '' : 'hidden'}`}>
        {/* Left Column: Campaign Dispatcher (5 cols) */}
        <div className="col-span-5 flex flex-col space-y-4 overflow-y-auto pr-1">
          {/* Workflow Selector */}
          <div className="p-4 rounded-xl bg-surface border border-border space-y-4">
            <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
              1. Choose Automation Action
            </h3>

            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setTaskType('warming')}
                className={`p-3.5 rounded-lg border text-left transition-all ${
                  taskType === 'warming'
                    ? 'bg-amber-950/30 border-amber-600/70 text-amber-200 ring-1 ring-amber-500/50'
                    : 'bg-zinc-950 border-zinc-800/80 text-zinc-400 hover:border-zinc-700'
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <Flame className="w-4 h-4 text-amber-400" />
                  <span className="font-semibold text-xs text-zinc-100">Feed Warming</span>
                </div>
                <p className="text-[11px] text-zinc-400 leading-snug">
                  Organic newsfeed navigation with random reading pauses and Bezier mouse wheel scrolls.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setTaskType('post')}
                className={`p-3.5 rounded-lg border text-left transition-all ${
                  taskType === 'post'
                    ? 'bg-blue-950/30 border-blue-600/70 text-blue-200 ring-1 ring-blue-500/50'
                    : 'bg-zinc-950 border-zinc-800/80 text-zinc-400 hover:border-zinc-700'
                }`}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <Send className="w-4 h-4 text-blue-400" />
                  <span className="font-semibold text-xs text-zinc-100">Auto Post</span>
                </div>
                <p className="text-[11px] text-zinc-400 leading-snug">
                  Publishes text status updates, photos, or video reels with human typing cadence and 1st comment.
                </p>
              </button>
            </div>

            {/* Task Configuration Fields */}
            {taskType === 'warming' ? (
              <div className="p-3 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2 text-xs">
                <div className="flex justify-between items-center text-zinc-300">
                  <span>Feed Scroll Depth:</span>
                  <span className="font-mono text-amber-400 font-bold">{scrolls} cycles</span>
                </div>
                <input
                  type="range"
                  min="2"
                  max="10"
                  value={scrolls}
                  onChange={(e) => setScrolls(parseInt(e.target.value))}
                  className="w-full h-1.5 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-amber-500"
                />
                <span className="text-[11px] text-zinc-500 block">
                  Each cycle scrolls 4-8 wheel notches and pauses organically (2-5s) to emulate real human consumption.
                </span>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Media Attachment (Photo or Reel Video) */}
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs text-zinc-300 font-medium flex items-center gap-1.5">
                      <span>Media Attachment (Photo or Video)</span>
                      <span className="text-[10px] text-zinc-500 font-normal">Optional</span>
                    </label>
                    {attachedMedia && (
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded font-mono font-medium flex items-center gap-1 ${
                          attachedMedia.type === 'reel'
                            ? 'bg-purple-950 text-purple-300 border border-purple-800/80'
                            : 'bg-blue-950 text-blue-300 border border-blue-800/80'
                        }`}
                      >
                        {attachedMedia.type === 'reel' ? (
                          <>
                            <Film className="w-3 h-3 text-purple-400" /> Reel Video
                          </>
                        ) : (
                          <>
                            <ImageIcon className="w-3 h-3 text-blue-400" /> Photo Post
                          </>
                        )}
                      </span>
                    )}
                  </div>

                  {attachedMedia ? (
                    <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800 flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-12 h-12 rounded-md overflow-hidden bg-black border border-zinc-700 shrink-0 flex items-center justify-center relative">
                          {attachedMedia.type === 'reel' ? (
                            <>
                              <video
                                src={attachedMedia.url}
                                className="w-full h-full object-cover"
                                preload="metadata"
                              />
                              <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                                <Play className="w-3.5 h-3.5 text-white fill-white" />
                              </div>
                            </>
                          ) : (
                            <img
                              src={attachedMedia.url}
                              alt="attached"
                              className="w-full h-full object-cover"
                            />
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="text-xs text-zinc-200 font-medium truncate max-w-[260px]">
                            {attachedMedia.original_name}
                          </div>
                          <div className="text-[10px] text-zinc-500 font-mono mt-0.5">
                            {attachedMedia.type === 'reel'
                              ? 'Facebook Reel Studio flow'
                              : 'Standard Composer Photo attachment'}
                          </div>
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => setAttachedMedia(null)}
                        className="p-1.5 rounded-md text-zinc-500 hover:text-red-400 hover:bg-zinc-800 transition-colors"
                        title="Remove attached media"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <label
                      className={`flex flex-col items-center justify-center p-3 border border-dashed rounded-lg cursor-pointer transition-all ${
                        isUploadingMedia
                          ? 'border-blue-500/50 bg-blue-950/20'
                          : 'border-zinc-800 hover:border-zinc-700 bg-zinc-950/60 hover:bg-zinc-950'
                      }`}
                    >
                      <input
                        type="file"
                        accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm"
                        onChange={handleMediaUpload}
                        disabled={isUploadingMedia}
                        className="hidden"
                      />
                      {isUploadingMedia ? (
                        <div className="flex items-center gap-2 text-xs text-blue-400 py-1">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span>Uploading media file...</span>
                        </div>
                      ) : (
                        <div className="flex flex-col items-center gap-1 text-center py-1">
                          <div className="flex items-center gap-2 text-zinc-400">
                            <ImageIcon className="w-4 h-4 text-blue-400" />
                            <span className="text-[11px] text-zinc-600">/</span>
                            <Film className="w-4 h-4 text-purple-400" />
                            <span className="text-xs font-medium text-zinc-300 ml-1">Attach Photo or Video</span>
                          </div>
                          <span className="text-[10px] text-zinc-500">
                            Click to browse JPG, PNG, WEBP (Photo) or MP4, MOV (Reel)
                          </span>
                        </div>
                      )}
                    </label>
                  )}
                </div>

                <div>
                  <label className="text-xs text-zinc-300 block mb-1 font-medium">Post Caption</label>
                  <textarea
                    value={caption}
                    onChange={(e) => setCaption(e.target.value)}
                    placeholder={
                      attachedMedia
                        ? 'Enter post caption (optional when media attached)...'
                        : 'Enter engaging Facebook status text...'
                    }
                    rows={3}
                    className="w-full p-2.5 bg-zinc-950 border border-zinc-800 rounded-lg text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
                  />
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1">
                    <label className="text-xs text-zinc-300 font-medium">1st-Comment URL</label>
                    <span className="text-[10px] text-emerald-400 font-medium bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">
                      Algo Safe
                    </span>
                  </div>
                  <input
                    type="url"
                    value={commentLink}
                    onChange={(e) => setCommentLink(e.target.value)}
                    placeholder="https://yourbrand.com/special-deal"
                    className="w-full p-2 bg-zinc-950 border border-zinc-800 rounded-lg text-xs text-zinc-100 font-mono placeholder-zinc-600 focus:outline-none focus:border-blue-500"
                  />
                  <p className="text-[10px] text-zinc-500 mt-1">
                    Facebook penalizes outbound links in post copy. Placing links in comment #1 retains 100% organic reach.
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Profile Target Selector */}
          <div className="p-4 rounded-xl bg-surface border border-border space-y-3 flex-1 flex flex-col min-h-[220px]">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                2. Target Profiles ({selectedProfileIds.length} Selected)
              </h3>
              <button
                type="button"
                onClick={handleSelectAllRunning}
                className="text-xs text-emerald-400 hover:text-emerald-300 font-medium"
              >
                {selectedProfileIds.length === runningProfiles.length && runningProfiles.length > 0
                  ? 'Deselect All'
                  : 'Select All Running'}
              </button>
            </div>

            {runningProfiles.length === 0 ? (
              <div className="p-6 rounded-lg bg-zinc-950 border border-zinc-800 text-center text-xs text-zinc-500 my-auto">
                No active containers running. Start a profile from the Profiles tab to automate it.
              </div>
            ) : (
              <div className="space-y-1.5 overflow-y-auto max-h-56 pr-1">
                {profiles.map((p) => {
                  const isRunning = p.status === 'running';
                  const isSelected = selectedProfileIds.includes(p.id);
                  const isTaskRunning = activeTasks[p.id]?.status === 'running';

                  return (
                    <div
                      key={p.id}
                      onClick={() => isRunning && toggleProfileSelection(p.id)}
                      className={`flex items-center justify-between p-2.5 rounded-lg border text-xs cursor-pointer transition-colors ${
                        !isRunning
                          ? 'opacity-40 bg-zinc-950/40 border-zinc-900 cursor-not-allowed'
                          : isSelected
                          ? 'bg-zinc-850 border-emerald-500/50 text-white'
                          : 'bg-zinc-950 border-zinc-800/80 text-zinc-400 hover:border-zinc-700'
                      }`}
                    >
                      <div className="flex items-center gap-2.5 truncate">
                        <input
                          type="checkbox"
                          disabled={!isRunning}
                          checked={isSelected}
                          onChange={() => {}}
                          className="rounded border-zinc-700 text-emerald-600 focus:ring-0"
                        />
                        <div className="truncate">
                          <span className="font-medium text-zinc-200 block truncate">{p.name}</span>
                          <span className="text-[10px] text-zinc-500 font-mono">
                            {p.id} · {p.network.proxy_host || 'Direct'}
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {isTaskRunning && (
                          <span className="flex items-center gap-1 text-[10px] font-mono text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded-full border border-amber-500/30 animate-pulse">
                            <Loader2 className="w-2.5 h-2.5 animate-spin" />
                            Busy
                          </span>
                        )}
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded uppercase font-mono font-medium ${
                            isRunning
                              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                              : 'bg-zinc-900 text-zinc-500'
                          }`}
                        >
                          {p.status}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Launch Button */}
            <div className="pt-2">
              <button
                type="button"
                onClick={handleLaunchCampaign}
                disabled={isLaunching || selectedProfileIds.length === 0}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-zinc-950 font-bold text-xs uppercase tracking-wider transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-emerald-950/30"
              >
                {isLaunching ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : taskType === 'warming' ? (
                  <Flame className="w-4 h-4" />
                ) : attachedMedia?.type === 'reel' ? (
                  <Film className="w-4 h-4" />
                ) : attachedMedia?.type === 'photo' ? (
                  <ImageIcon className="w-4 h-4" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                <span>
                  Launch{' '}
                  {taskType === 'warming'
                    ? 'Warming Routine'
                    : attachedMedia?.type === 'reel'
                    ? 'Reel Campaign'
                    : attachedMedia?.type === 'photo'
                    ? 'Photo Post'
                    : 'Posting Campaign'}
                </span>
              </button>
            </div>
          </div>
        </div>

        {/* Right Column: Active Automation Tasks & Live Inspector (7 cols) */}
        <div className="col-span-7 flex flex-col space-y-4 overflow-hidden">
          {/* Tasks Grid */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col h-1/2 overflow-hidden">
            <div className="flex items-center justify-between mb-3 shrink-0">
              <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                <Layers className="w-3.5 h-3.5 text-zinc-400" />
                Live Automation Tasks ({Object.keys(activeTasks).length})
              </h3>
            </div>

            {Object.keys(activeTasks).length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-6 border border-dashed border-zinc-800 rounded-lg text-zinc-500 text-xs">
                <Sparkles className="w-8 h-8 text-zinc-700 mb-2" />
                <p>No active or recent automation tasks.</p>
                <p className="text-[11px] text-zinc-600 mt-1">
                  Configure and launch a campaign on the left to watch live execution.
                </p>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                {Object.entries(activeTasks).map(([profileId, task]) => {
                  const isInspecting = selectedTaskProfileId === profileId;
                  const profile = profiles.find((p) => p.id === profileId);

                  return (
                    <div
                      key={profileId}
                      onClick={() => setSelectedTaskProfileId(profileId)}
                      className={`p-3 rounded-lg border text-xs transition-colors cursor-pointer ${
                        isInspecting
                          ? 'bg-zinc-850 border-zinc-600'
                          : 'bg-zinc-950 border-zinc-800/80 hover:border-zinc-700'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-zinc-200">
                            {profile?.name || profileId}
                          </span>
                          <span className="font-mono text-[10px] text-zinc-500">{profileId}</span>
                          <span className="font-mono text-[10px] uppercase px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300">
                            {task.task}
                          </span>
                        </div>

                        <div className="flex items-center gap-2">
                          {task.status === 'running' ? (
                            <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-400 font-mono text-[10px] animate-pulse">
                              <Loader2 className="w-2.5 h-2.5 animate-spin" />
                              RUNNING
                            </span>
                          ) : task.status === 'completed' ? (
                            <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-mono text-[10px]">
                              <CheckCircle2 className="w-2.5 h-2.5" />
                              SUCCESS
                            </span>
                          ) : task.status === 'failed' ? (
                            <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-400 font-mono text-[10px]">
                              <AlertTriangle className="w-2.5 h-2.5" />
                              FAILED
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400 font-mono text-[10px]">
                              {task.status?.toUpperCase() || 'IDLE'}
                            </span>
                          )}

                          {task.status === 'running' && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                handleStopTask(profileId);
                              }}
                              className="p-1 rounded bg-rose-950/60 border border-rose-800/60 text-rose-300 hover:bg-rose-900/60 transition-colors"
                              title="Stop Task"
                            >
                              <Square className="w-3 h-3 fill-current" />
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Latest log snippet */}
                      {task.logs.length > 0 && (
                        <p className="text-[11px] text-zinc-400 font-mono truncate bg-zinc-900/80 px-2 py-1 rounded border border-zinc-800/60">
                          &gt; {task.logs[task.logs.length - 1].message}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Bottom Half: Step Inspector Terminal */}
          <div className="p-4 rounded-xl bg-surface border border-border flex flex-col h-1/2 overflow-hidden">
            <div className="flex items-center justify-between mb-2 shrink-0">
              <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-emerald-400" />
                <h3 className="text-xs font-semibold text-zinc-300">
                  {inspectedTask
                    ? `Live Execution Log: ${inspectedTask.profile_id} (${inspectedTask.task})`
                    : 'Task Log Inspector'}
                </h3>
              </div>
              {inspectedTask && (
                <button
                  onClick={() => onSelectProfile(inspectedTask.profile_id)}
                  className="text-xs text-zinc-400 hover:text-white underline"
                >
                  Open in VNC Viewer
                </button>
              )}
            </div>

            {!inspectedTask || inspectedTask.logs.length === 0 ? (
              <div className="flex-1 flex items-center justify-center text-xs text-zinc-600 font-mono">
                Select a task above to inspect real-time Zero-CDP telemetry.
              </div>
            ) : (
              <div className="flex-1 p-3 rounded-lg bg-black border border-zinc-800 font-mono text-[11px] overflow-y-auto space-y-1 select-text">
                {inspectedTask.logs.map((log, idx) => (
                  <div key={idx} className="flex items-start gap-2 leading-tight">
                    <span className="text-zinc-600 shrink-0">
                      {log.timestamp ? log.timestamp.split('T')[1]?.slice(0, 8) : '--:--:--'}
                    </span>
                    <span
                      className={`font-semibold shrink-0 ${
                        log.level === 'STEP'
                          ? 'text-cyan-400'
                          : log.level === 'SUCCESS'
                          ? 'text-emerald-400'
                          : log.level === 'ERROR'
                          ? 'text-rose-400'
                          : log.level === 'WARN'
                          ? 'text-amber-400'
                          : 'text-zinc-400'
                      }`}
                    >
                      [{log.level}]
                    </span>
                    <span className="text-zinc-300 break-words">{log.message}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
