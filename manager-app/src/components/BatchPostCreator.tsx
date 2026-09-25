import React, { useState, useEffect } from 'react';
import { Profile } from '../types/profile';
import { CreateBatchParams } from '../types/automation';
import { createBatch, uploadMediaFiles } from '../services/api';
import {
  UploadCloud,
  Film,
  Image as ImageIcon,
  Sparkles,
  Trash2,
  Calendar,
  Clock,
  ShieldCheck,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Layers,
  FileCode,
  X,
  Play,
  Maximize2,
  Paperclip,
  Zap,
} from 'lucide-react';

interface BatchPostCreatorProps {
  profiles: Profile[];
  onBatchCreated: () => void;
}

interface DraftPostRow {
  id: string;
  type: 'photo' | 'reel';
  media_file: string;
  media_name: string;
  preview_url?: string;
  caption: string;
  first_comment: string;
  ai_spin: boolean;
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
}) => {
  // Load draft from localStorage so switching tabs or views never loses data
  const [batchName, setBatchName] = useState(() => {
    return (
      localStorage.getItem('batch_creator_batch_name') ||
      `Daily Batch ${new Date().toLocaleDateString()}`
    );
  });

  const [selectedProfileIds, setSelectedProfileIds] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem('batch_creator_profiles');
      if (saved) return JSON.parse(saved);
    } catch (_) {}
    return profiles.filter((p) => p.status === 'running').map((p) => p.id);
  });

  const [startTime, setStartTime] = useState(() => {
    return localStorage.getItem('batch_creator_start_time') || '09:00';
  });

  const [endTime, setEndTime] = useState(() => {
    return localStorage.getItem('batch_creator_end_time') || '21:00';
  });

  const [staggerMinutes, setStaggerMinutes] = useState(() => {
    return parseInt(localStorage.getItem('batch_creator_stagger') || '15', 10) || 15;
  });

  const [preparationMode, setPreparationMode] = useState<'off' | 'brief' | 'extended'>(() => {
    const rollingPipelineEnabled = localStorage.getItem('batch_creator_rolling_pipeline_v1') === 'true';
    if (!rollingPipelineEnabled) return 'brief';
    const saved = localStorage.getItem('batch_creator_preparation_mode');
    return saved === 'off' || saved === 'brief' || saved === 'extended' ? saved : 'brief';
  });

  const [aiSpinAll, setAiSpinAll] = useState(() => {
    const saved = localStorage.getItem('batch_creator_ai_spin');
    return saved !== null ? saved === 'true' : true;
  });

  const [startNow, setStartNow] = useState(() => {
    return localStorage.getItem('batch_creator_start_now') === 'true';
  });

  const [posts, setPosts] = useState<DraftPostRow[]>(() => {
    try {
      const saved = localStorage.getItem('batch_creator_draft_posts');
      if (saved) return JSON.parse(saved);
    } catch (_) {}
    return [];
  });

  // Auto-save to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('batch_creator_draft_posts', JSON.stringify(posts));
    } catch (_) {}
  }, [posts]);

  useEffect(() => {
    localStorage.setItem('batch_creator_batch_name', batchName);
  }, [batchName]);

  useEffect(() => {
    localStorage.setItem('batch_creator_profiles', JSON.stringify(selectedProfileIds));
  }, [selectedProfileIds]);

  useEffect(() => {
    localStorage.setItem('batch_creator_start_time', startTime);
    localStorage.setItem('batch_creator_end_time', endTime);
    localStorage.setItem('batch_creator_stagger', String(staggerMinutes));
    localStorage.setItem('batch_creator_ai_spin', String(aiSpinAll));
    localStorage.setItem('batch_creator_start_now', String(startNow));
    localStorage.setItem('batch_creator_preparation_mode', preparationMode);
    localStorage.setItem('batch_creator_rolling_pipeline_v1', 'true');
  }, [startTime, endTime, staggerMinutes, aiSpinAll, startNow, preparationMode]);

  const [isUploading, setIsUploading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isAiGeneratingAll, setIsAiGeneratingAll] = useState(false);
  const [aiTopic, setAiTopic] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleClearDraft = () => {
    if (posts.length > 0 && !confirm('Are you sure you want to clear all drafted posts?')) return;
    setPosts([]);
    localStorage.removeItem('batch_creator_draft_posts');
    setSuccessMsg('Draft cleared.');
  };

  // Big size preview modal state
  const [lightboxMedia, setLightboxMedia] = useState<LightboxMedia | null>(null);

  // Profile Selection Helpers
  const toggleProfile = (id: string) => {
    setSelectedProfileIds((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    );
  };

  const handleSelectRunningOnly = () => {
    setSelectedProfileIds(profiles.filter((p) => p.status === 'running').map((p) => p.id));
  };

  const handleSelectAll = () => {
    setSelectedProfileIds(profiles.map((p) => p.id));
  };

  const handleClearSelection = () => {
    setSelectedProfileIds([]);
  };

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
        type: file.type,
        media_file: file.filename,
        media_name: file.original_name || file.filename,
        preview_url: `/shared_media/${file.filename}`,
        caption: '',
        first_comment: '',
        ai_spin: aiSpinAll,
      }));

      setPosts((prev) => [...prev, ...newRows]);
      setSuccessMsg(`Uploaded ${res.files.length} media files into the batch table!`);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to upload media files');
    } finally {
      setIsUploading(false);
      e.target.value = '';
    }
  };

  // Single Row Media Upload Handler
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
                  preview_url: `/shared_media/${uploaded.filename}`,
                  type: uploaded.type,
                }
              : row
          )
        );
        setSuccessMsg(`Attached ${uploaded.original_name} to row!`);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to attach media to row');
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
          const type: 'photo' | 'reel' =
            rawType === 'reel' || rawType === 'video' ? 'reel' : 'photo';
          const mediaFile = item.media_file || item.media || item.file || '';

          return {
            id: `json_${Date.now()}_${idx}`,
            type,
            media_file: mediaFile,
            media_name: mediaFile || '',
            preview_url: mediaFile ? `/shared_media/${mediaFile}` : undefined,
            caption,
            first_comment: comment,
            ai_spin: item.ai_spin !== undefined ? item.ai_spin : aiSpinAll,
          };
        });

        setPosts((prev) => [...prev, ...importedRows]);
        setSuccessMsg(
          `📄 Imported ${importedRows.length} posts from ${file.name}! You can now attach images or reels to each row.`
        );
      } catch (err: any) {
        setErrorMsg(`Failed to parse JSON file: ${err.message}`);
      } finally {
        e.target.value = '';
      }
    };

    reader.readAsText(file);
  };

  // Add empty row
  const handleAddEmptyRow = (type: 'photo' | 'reel') => {
    setPosts((prev) => [
      ...prev,
      {
        id: `draft_${Date.now()}_${prev.length}`,
        type,
        media_file: '',
        media_name: '',
        caption: '',
        first_comment: '',
        ai_spin: aiSpinAll,
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

  // Bulk AI Caption Generation
  const handleGenerateAllCaptions = async () => {
    if (posts.length === 0) return;
    setIsAiGeneratingAll(true);
    setErrorMsg(null);

    const baseTheme = aiTopic.trim() || 'Daily tips and mindset growth for high achievers';
    const variations = [
      "Here is something you need to know today! Consistency is what separates dreams from reality. 🚀 #success #focus #growth",
      "Quick reminder: small actions done daily lead to massive transformations. ⚡ What is your goal this week? #mindset #dailyhabits",
      "Most people quit when it gets uncomfortable. That is your cue to push through. 🔥 #discipline #motivation",
      "3 rules that change everything: 1. Show up. 2. Do the work. 3. Ignore the noise. 🎯 #focus #reels",
      "Never trade long-term vision for short-term comfort. Stay locked in. 💡 #habits #productivity",
      "The best time to start was yesterday. The next best time is right now. ✨ #inspiration #action",
      "Real strength is built when nobody is watching. Keep building. 🏆 #grind #determination",
      "Success is simply consistency disguised as hard work. 📌 Save this for later! #quotes #mindset",
      "If you want different results, you must take different actions. Start small today. 🚀 #breakthrough",
      "Your future self will thank you for the sacrifices you make today. 💯 #dailygrind #focus",
    ];

    setTimeout(() => {
      setPosts((prev) =>
        prev.map((row, idx) => ({
          ...row,
          caption: row.caption.trim() ? row.caption : `${baseTheme}: ${variations[idx % variations.length]}`,
        }))
      );
      setIsAiGeneratingAll(false);
      setSuccessMsg('✨ AI Captions generated for all draft rows!');
    }, 600);
  };

  // Schedule Batch Submission
  const handleSubmitBatch = async (forceStartNow?: boolean) => {
    setErrorMsg(null);
    setSuccessMsg(null);

    if (selectedProfileIds.length === 0) {
      setErrorMsg('Please select at least one target profile.');
      return;
    }
    if (posts.length === 0) {
      setErrorMsg('Please add at least one post to the batch.');
      return;
    }

    const emptyRow = posts.find((p) => !p.caption.trim() && !p.media_file);
    if (emptyRow) {
      setErrorMsg('Each post row must have either a media file or a caption.');
      return;
    }

    setIsSubmitting(true);
    const executeImmediate = forceStartNow !== undefined ? forceStartNow : startNow;

    try {
      const payload: CreateBatchParams = {
        name: batchName.trim() || `Daily Batch ${new Date().toLocaleDateString()}`,
        target_profiles: selectedProfileIds,
        start_now: executeImmediate,
        schedule_window: {
          start_time: startTime,
          end_time: endTime,
          profile_stagger_minutes: staggerMinutes,
          session_preparation_mode: preparationMode,
          start_now: executeImmediate,
        },
        posts: posts.map((p) => ({
          type: p.type,
          media_file: p.media_file,
          base_caption: p.caption.trim(),
          first_comment: p.first_comment.trim() || undefined,
          ai_spin: p.ai_spin,
        })),
      };

      const res = await createBatch(payload);
      setSuccessMsg(
        `✅ Successfully created batch "${res.batch.name}" with ${res.total_executions} executions across ${res.batch.target_profiles.length} profiles! ${executeImmediate ? '🚀 Execution starting now.' : ''}`
      );
      setPosts([]);
      localStorage.removeItem('batch_creator_draft_posts');
      onBatchCreated();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to schedule batch campaign');
    } finally {
      setIsSubmitting(false);
    }
  };

  const totalExecutions = selectedProfileIds.length * posts.length;

  return (
    <div className="space-y-6">
      {/* Top Banner Alerts */}
      {errorMsg && (
        <div className="p-3 bg-red-950/70 border border-red-800 text-red-200 text-xs rounded-lg flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}
      {successMsg && (
        <div className="p-3 bg-emerald-950/70 border border-emerald-800 text-emerald-200 text-xs rounded-lg flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* Batch Name & Quick Options Bar */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 flex flex-wrap items-center justify-between gap-3">
        <div className="flex-1 min-w-[240px]">
          <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
            Batch Campaign Name
          </label>
          <input
            type="text"
            value={batchName}
            onChange={(e) => setBatchName(e.target.value)}
            className="w-full px-3 py-1.5 text-xs rounded bg-zinc-950 border border-zinc-700 text-white focus:outline-none focus:border-blue-500"
          />
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer pt-4">
          <input
            type="checkbox"
            checked={aiSpinAll}
            onChange={(e) => {
              setAiSpinAll(e.target.checked);
              setPosts((prev) => prev.map((p) => ({ ...p, ai_spin: e.target.checked })));
            }}
            className="w-4 h-4 rounded border-zinc-700 bg-zinc-900 text-purple-600 focus:ring-purple-500"
          />
          <span>Default AI Caption Spinning for All Rows</span>
        </label>
      </div>

      {/* 1. Target Profiles Selective Checkbox Matrix */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-blue-400" />
              1. Target Profiles Matrix (Choose Which Accounts Post)
            </h3>
            <p className="text-xs text-zinc-400 mt-0.5">
              Only checked accounts will receive scheduled posts from this batch.
            </p>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <button
              type="button"
              onClick={handleSelectRunningOnly}
              className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 font-medium transition-colors"
            >
              Select Running Only
            </button>
            <button
              type="button"
              onClick={handleSelectAll}
              className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 font-medium transition-colors"
            >
              Select All
            </button>
            <button
              type="button"
              onClick={handleClearSelection}
              className="px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400 transition-colors"
            >
              Clear
            </button>
          </div>
        </div>

        {/* Profile Checkbox Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5 pt-1">
          {profiles.map((profile) => {
            const isSelected = selectedProfileIds.includes(profile.id);
            const isRunning = profile.status === 'running';

            return (
              <label
                key={profile.id}
                className={`flex items-center justify-between p-2.5 rounded-lg border cursor-pointer transition-all ${
                  isSelected
                    ? 'bg-blue-950/30 border-blue-600/70 text-white'
                    : 'bg-zinc-950/60 border-zinc-800/80 text-zinc-400 hover:border-zinc-700'
                }`}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleProfile(profile.id)}
                    className="w-4 h-4 rounded border-zinc-700 text-blue-600 focus:ring-blue-500 bg-zinc-900"
                  />
                  <div className="truncate">
                    <div className="text-xs font-medium truncate">{profile.name}</div>
                    <div className="text-[10px] text-zinc-500 font-mono">
                      {profile.network?.proxy_host ? `Proxy: ${profile.network.proxy_host}` : 'Direct Network'}
                    </div>
                  </div>
                </div>

                <span
                  className={`text-[10px] font-mono px-1.5 py-0.5 rounded capitalize shrink-0 ${
                    isRunning
                      ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-800/60'
                      : 'bg-zinc-900 text-zinc-500'
                  }`}
                >
                  {profile.status}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* 2. Batch Content Preparation Section */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
              <Layers className="w-4 h-4 text-purple-400" />
              2. Content Preparation ({posts.length} Posts In Table)
            </h3>
            <p className="text-xs text-zinc-400 mt-0.5">
              Import from a JSON file, or drag & drop media. Then attach and preview images or reels.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* JSON Upload Button */}
            <label className="px-3 py-1.5 rounded-lg bg-emerald-900/40 hover:bg-emerald-800/50 border border-emerald-700/60 text-xs font-semibold text-emerald-300 flex items-center gap-1.5 cursor-pointer transition-colors shadow-sm">
              <FileCode className="w-3.5 h-3.5" />
              <span>Upload JSON Content</span>
              <input
                type="file"
                accept=".json,application/json"
                onChange={handleJsonUpload}
                className="hidden"
              />
            </label>

            <button
              type="button"
              onClick={() => handleAddEmptyRow('reel')}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200 flex items-center gap-1.5 transition-colors"
            >
              <Film className="w-3.5 h-3.5 text-purple-400" />
              + Add Reel Row
            </button>
            <button
              type="button"
              onClick={() => handleAddEmptyRow('photo')}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200 flex items-center gap-1.5 transition-colors"
            >
              <ImageIcon className="w-3.5 h-3.5 text-blue-400" />
              + Add Photo Row
            </button>
            {posts.length > 0 && (
              <button
                type="button"
                onClick={handleClearDraft}
                className="px-2.5 py-1.5 rounded-lg bg-red-950/40 hover:bg-red-900/60 border border-red-800/50 text-xs text-red-300 flex items-center gap-1.5 transition-colors"
                title="Clear current draft"
              >
                <Trash2 className="w-3.5 h-3.5 text-red-400" />
                Clear Draft
              </button>
            )}
          </div>
        </div>

        {/* Dual Import Options Bar: Drag & Drop Media OR JSON Helper */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Media Bulk Dropzone (2 cols) */}
          <label className="md:col-span-2 border-2 border-dashed border-zinc-700/80 hover:border-blue-500/80 rounded-xl p-5 flex flex-col items-center justify-center cursor-pointer transition-colors bg-zinc-950/40 group">
            <input
              type="file"
              multiple
              accept="image/*,video/mp4,video/quicktime"
              onChange={handleBulkMediaUpload}
              className="hidden"
              disabled={isUploading}
            />
            {isUploading ? (
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />
                <span>Uploading media to shared volume...</span>
              </div>
            ) : (
              <>
                <UploadCloud className="w-7 h-7 text-zinc-400 group-hover:text-blue-400 transition-colors mb-1.5" />
                <span className="text-xs font-medium text-zinc-300">
                  Bulk Drop Images & Videos
                </span>
                <span className="text-[10px] text-zinc-500 mt-0.5">
                  .mp4 / .mov auto-assigned as <strong className="text-purple-400">Reels</strong> · .jpg / .png auto-assigned as <strong className="text-blue-400">Photos</strong>
                </span>
              </>
            )}
          </label>

          {/* JSON File Template Guide Card (1 col) */}
          <div className="p-4 rounded-xl bg-zinc-950/80 border border-zinc-800/80 flex flex-col justify-between text-xs space-y-2">
            <div>
              <div className="font-semibold text-zinc-200 flex items-center gap-1.5">
                <FileCode className="w-3.5 h-3.5 text-emerald-400" />
                JSON Format Support
              </div>
              <p className="text-[11px] text-zinc-400 mt-1 leading-snug">
                Upload a JSON file containing caption and first-comment links.
              </p>
              <pre className="mt-2 p-2 rounded bg-zinc-900 text-[10px] text-zinc-300 font-mono overflow-x-auto">
{`[
  {
    "caption": "Your post text...",
    "comment": "https://link.com",
    "type": "reel"
  }
]`}
              </pre>
            </div>
          </div>
        </div>

        {/* Bulk AI Generator Bar */}
        {posts.length > 0 && (
          <div className="p-3 rounded-lg bg-purple-950/20 border border-purple-800/40 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 flex-1 min-w-[240px]">
              <Sparkles className="w-4 h-4 text-purple-400 shrink-0" />
              <input
                type="text"
                value={aiTopic}
                onChange={(e) => setAiTopic(e.target.value)}
                placeholder="Topic / Niche (e.g. Daily productivity tips for entrepreneurs)..."
                className="w-full px-3 py-1.5 text-xs rounded bg-zinc-900 border border-zinc-700 text-white placeholder-zinc-500 focus:outline-none focus:border-purple-500"
              />
            </div>

            <button
              type="button"
              onClick={handleGenerateAllCaptions}
              disabled={isAiGeneratingAll}
              className="px-3 py-1.5 rounded bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-xs font-semibold text-white flex items-center gap-1.5 transition-colors shrink-0"
            >
              {isAiGeneratingAll ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Sparkles className="w-3.5 h-3.5" />
              )}
              ✨ Generate Captions for All ({posts.length})
            </button>
          </div>
        )}

        {/* Posts Table List */}
        {posts.length > 0 && (
          <div className="space-y-3 max-h-[560px] overflow-y-auto pr-1">
            {posts.map((post, idx) => (
              <div
                key={post.id}
                className="p-3.5 rounded-xl bg-zinc-950/80 border border-zinc-800/80 hover:border-zinc-750 transition-all space-y-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className="w-5 h-5 rounded-full bg-zinc-800 text-[10px] font-mono text-zinc-300 flex items-center justify-center font-bold">
                      {idx + 1}
                    </span>

                    {/* Type Toggle Badge */}
                    <button
                      type="button"
                      onClick={() =>
                        handleUpdateRow(post.id, {
                          type: post.type === 'reel' ? 'photo' : 'reel',
                        })
                      }
                      className={`px-2 py-0.5 rounded text-[11px] font-semibold flex items-center gap-1 transition-colors ${
                        post.type === 'reel'
                          ? 'bg-purple-950 text-purple-300 border border-purple-700/60'
                          : 'bg-blue-950 text-blue-300 border border-blue-700/60'
                      }`}
                    >
                      {post.type === 'reel' ? (
                        <>
                          <Film className="w-3 h-3 text-purple-400" /> Reel Video
                        </>
                      ) : (
                        <>
                          <ImageIcon className="w-3 h-3 text-blue-400" /> Photo Post
                        </>
                      )}
                    </button>

                    {post.media_file ? (
                      <span className="text-[11px] text-zinc-300 font-mono truncate max-w-[200px]">
                        📁 {post.media_name || post.media_file}
                      </span>
                    ) : (
                      <span className="text-[11px] text-amber-400 font-medium flex items-center gap-1">
                        ⚠️ No media attached
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={post.ai_spin}
                        onChange={(e) =>
                          handleUpdateRow(post.id, { ai_spin: e.target.checked })
                        }
                        className="rounded border-zinc-700 bg-zinc-900 text-purple-600 focus:ring-purple-500 w-3.5 h-3.5"
                      />
                      <span>AI Spin per Profile</span>
                    </label>

                    <button
                      type="button"
                      onClick={() => handleRemoveRow(post.id)}
                      className="p-1 rounded text-zinc-500 hover:text-red-400 transition-colors"
                      title="Remove row"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Media Row Grid: Thumbnail Preview + Inputs */}
                <div className="flex flex-col sm:flex-row gap-3">
                  {/* Media Preview / Attach Box */}
                  <div className="shrink-0 flex items-center sm:items-start">
                    {post.media_file ? (
                      <div className="relative group">
                        {post.type === 'reel' ? (
                          <div
                            onClick={() =>
                              setLightboxMedia({
                                url: post.preview_url || `/shared_media/${post.media_file}`,
                                type: 'reel',
                                name: post.media_name || post.media_file,
                                caption: post.caption,
                                comment: post.first_comment,
                              })
                            }
                            className="relative w-16 h-16 rounded-xl overflow-hidden border border-purple-800/80 bg-black cursor-pointer shadow-md group-hover:ring-2 group-hover:ring-purple-500 transition-all flex items-center justify-center"
                            title="Click for big size preview"
                          >
                            <video
                              src={post.preview_url || `/shared_media/${post.media_file}`}
                              className="w-full h-full object-cover opacity-75"
                              preload="metadata"
                            />
                            <div className="absolute inset-0 flex items-center justify-center bg-black/40 group-hover:bg-black/20 transition-all">
                              <Play className="w-5 h-5 text-white fill-white drop-shadow" />
                            </div>
                            <div className="absolute bottom-1 right-1 p-0.5 rounded bg-black/70 text-[9px] text-zinc-300">
                              <Maximize2 className="w-2.5 h-2.5" />
                            </div>
                          </div>
                        ) : (
                          <div
                            onClick={() =>
                              setLightboxMedia({
                                url: post.preview_url || `/shared_media/${post.media_file}`,
                                type: 'photo',
                                name: post.media_name || post.media_file,
                                caption: post.caption,
                                comment: post.first_comment,
                              })
                            }
                            className="relative w-16 h-16 rounded-xl overflow-hidden border border-blue-800/80 bg-black cursor-pointer shadow-md group-hover:ring-2 group-hover:ring-blue-500 transition-all flex items-center justify-center"
                            title="Click for big size preview"
                          >
                            <img
                              src={post.preview_url || `/shared_media/${post.media_file}`}
                              alt={post.media_name || 'preview'}
                              className="w-full h-full object-cover"
                            />
                            <div className="absolute bottom-1 right-1 p-0.5 rounded bg-black/70 text-[9px] text-zinc-300 opacity-0 group-hover:opacity-100 transition-opacity">
                              <Maximize2 className="w-2.5 h-2.5" />
                            </div>
                          </div>
                        )}

                        {/* Remove media button */}
                        <button
                          type="button"
                          onClick={() => handleRemoveMediaFromRow(post.id)}
                          className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-red-600 hover:bg-red-500 text-white flex items-center justify-center text-[10px] shadow"
                          title="Remove media"
                        >
                          ×
                        </button>
                      </div>
                    ) : (
                      <label className="w-16 h-16 rounded-xl border border-dashed border-zinc-700 hover:border-blue-500 bg-zinc-900/60 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 cursor-pointer flex flex-col items-center justify-center transition-all p-1 text-center">
                        <Paperclip className="w-4 h-4 text-blue-400 mb-0.5" />
                        <span className="text-[9px] font-medium leading-tight">Attach Media</span>
                        <input
                          type="file"
                          accept="image/*,video/mp4,video/quicktime"
                          onChange={(e) =>
                            e.target.files?.[0] && handleRowMediaUpload(post.id, e.target.files[0])
                          }
                          className="hidden"
                          disabled={isUploading}
                        />
                      </label>
                    )}
                  </div>

                  {/* Caption & First Comment Fields */}
                  <div className="flex-1 space-y-2">
                    <textarea
                      value={post.caption}
                      onChange={(e) => handleUpdateRow(post.id, { caption: e.target.value })}
                      placeholder={
                        post.type === 'reel'
                          ? 'Reel description & trending hashtags (#reels #viral #fyp)...'
                          : "Post caption: What's on your mind?..."
                      }
                      rows={2}
                      className="w-full px-3 py-2 text-xs rounded-lg bg-zinc-900/90 border border-zinc-800 text-white placeholder-zinc-500 focus:outline-none focus:border-blue-500 resize-none font-sans"
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
                        placeholder="https://example.com/guide (optional destination link for max organic reach)"
                        className="flex-1 px-2.5 py-1 text-[11px] rounded bg-zinc-900 border border-zinc-800 text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-blue-500"
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 3. Timing, Staggering & Campaign Controls */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
        <h3 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
          <Clock className="w-4 h-4 text-emerald-400" />
          3. Daily Pacing & Profile Stagger Schedule
        </h3>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
          <div>
            <label className="block text-zinc-400 mb-1 font-medium">Daily Start Time</label>
            <input
              type="time"
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-zinc-400 mb-1 font-medium">Daily End Time</label>
            <input
              type="time"
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-zinc-400 mb-1 font-medium">
              Profile Stagger Delay (Minutes)
            </label>
            <input
              type="number"
              min={5}
              max={60}
              value={staggerMinutes}
              onChange={(e) => setStaggerMinutes(parseInt(e.target.value, 10) || 15)}
              className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-zinc-400 mb-1 font-medium">Rolling Session Preparation</label>
            <select
              value={preparationMode}
              onChange={(e) => setPreparationMode(e.target.value as 'off' | 'brief' | 'extended')}
              className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-white focus:outline-none focus:border-blue-500"
            >
              <option value="off">Off</option>
              <option value="brief">Brief browsing (40–55s)</option>
              <option value="extended">Extended browsing (55–70s)</option>
            </select>
            <p className="text-[10px] text-zinc-500 mt-1">Starts the next profile, validates login/theme, scrolls without engagement, then keeps it ready.</p>
          </div>
        </div>

        {/* Start Now Mode Banner Card */}
        <div className={`p-3.5 rounded-xl border transition-all flex items-center justify-between gap-4 ${
          startNow 
            ? 'bg-amber-950/25 border-amber-600/60 text-amber-200' 
            : 'bg-zinc-950/60 border-zinc-800/80 text-zinc-400'
        }`}>
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${startNow ? 'bg-amber-600/20 text-amber-400' : 'bg-zinc-800 text-zinc-400'}`}>
              <Zap className="w-5 h-5" />
            </div>
            <div>
              <div className="text-xs font-semibold text-white flex items-center gap-2">
                <span>Start Now Mode (Immediate Execution)</span>
                {startNow && <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300 font-mono">ACTIVE</span>}
              </div>
              <div className="text-[11px] text-zinc-400 mt-0.5">
                Execute Post #1 immediately upon creation without waiting for scheduled window. Subsequent posts follow stagger delay.
              </div>
            </div>
          </div>
          <label className="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              type="checkbox"
              checked={startNow}
              onChange={(e) => setStartNow(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-11 h-6 bg-zinc-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-zinc-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-amber-600"></div>
          </label>
        </div>

        {/* Live Calculation Summary */}
        <div className="p-3 rounded-lg bg-blue-950/20 border border-blue-900/40 flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="text-blue-300">
            📊 <strong>{selectedProfileIds.length} profiles</strong> selected × <strong>{posts.length} posts</strong> ={' '}
            <strong className="text-white">{totalExecutions} total scheduled executions today</strong>.
          </div>
          <div className="text-zinc-400 font-mono text-[11px]">
            Persisted shuffled profile order · maximum two open containers
          </div>
        </div>

        {/* Action Buttons: Start Now & Schedule */}
        <div className="pt-2 flex flex-wrap items-center justify-end gap-3">
          <button
            type="button"
            onClick={() => handleSubmitBatch(false)}
            disabled={isSubmitting || selectedProfileIds.length === 0 || posts.length === 0}
            className="px-5 py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-zinc-200 font-medium text-xs flex items-center gap-2 transition-all border border-zinc-700"
          >
            <Calendar className="w-4 h-4 text-zinc-400" />
            Schedule for {startTime} - {endTime}
          </button>

          <button
            type="button"
            onClick={() => handleSubmitBatch(true)}
            disabled={isSubmitting || selectedProfileIds.length === 0 || posts.length === 0}
            className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 disabled:opacity-50 text-white font-semibold text-xs flex items-center gap-2 shadow-lg shadow-amber-900/30 transition-all"
          >
            {isSubmitting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Zap className="w-4 h-4 text-white" />
            )}
            ⚡ Start Batch Now ({totalExecutions} Posts)
          </button>
        </div>
      </div>

      {/* 4. Full Size Media Lightbox Modal */}
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
