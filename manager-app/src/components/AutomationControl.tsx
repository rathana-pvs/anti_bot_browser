import React, { useState, useEffect, useRef } from 'react';
import { Profile } from '../types/profile';
import { AutomationTaskState, RunAutomationParams } from '../types/automation';
import { runAutomation, fetchAutomationStatus, stopAutomation } from '../services/api';
import {
  Flame,
  Send,
  Square,
  Terminal,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Sparkles,
  MessageCircle,
} from 'lucide-react';

interface AutomationControlProps {
  profile: Profile;
  onRefreshProfile?: () => void;
}

export const AutomationControl: React.FC<AutomationControlProps> = ({ profile, onRefreshProfile }) => {
  const [taskState, setTaskState] = useState<AutomationTaskState>({
    profile_id: profile.id,
    status: 'idle',
    logs: [],
  });
  const [showPostForm, setShowPostForm] = useState(false);
  const [showWarmingForm, setShowWarmingForm] = useState(false);
  const [showCommentForm, setShowCommentForm] = useState(false);
  const [caption, setCaption] = useState('');
  const [commentLink, setCommentLink] = useState('');
  const [testComment, setTestComment] = useState('');
  const [scrolls, setScrolls] = useState(4);
  const [isStarting, setIsStarting] = useState(false);
  const [isLogsExpanded, setIsLogsExpanded] = useState(true);
  const [actionError, setActionError] = useState<string | null>(null);

  const logsEndRef = useRef<HTMLDivElement>(null);

  // Poll while the inspector is open so scheduled queue jobs appear live too.
  const refreshStatus = async () => {
    try {
      const state = await fetchAutomationStatus(profile.id);
      setTaskState(state);
    } catch (_) {}
  };

  useEffect(() => {
    refreshStatus();
  }, [profile.id]);

  useEffect(() => {
    let previousStatus = taskState.status;
    const interval: ReturnType<typeof setInterval> = setInterval(async () => {
      try {
        const state = await fetchAutomationStatus(profile.id);
        setTaskState(state);
        if (state.status !== previousStatus) {
          if (state.status === 'running' || state.status === 'failed' || state.status === 'uncertain') {
            setIsLogsExpanded(true);
          }
          if (previousStatus === 'running' && state.status !== 'running' && onRefreshProfile) {
            onRefreshProfile();
          }
          previousStatus = state.status;
        }
      } catch (_) {}
    }, 1000);
    return () => clearInterval(interval);
  }, [profile.id, onRefreshProfile]);

  // Scroll to bottom of logs when expanded and logs change
  useEffect(() => {
    if (isLogsExpanded && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [taskState.logs, isLogsExpanded]);

  const handleStartWarming = async () => {
    if (profile.status !== 'running') {
      setActionError('Container must be started before launching automation.');
      return;
    }
    setIsStarting(true);
    setActionError(null);
    try {
      const res = await runAutomation({
        profile_id: profile.id,
        task: 'warming',
        scrolls,
      });
      setTaskState(res.state);
      setShowWarmingForm(false);
      setIsLogsExpanded(true);
    } catch (err: any) {
      setActionError(err.message || 'Failed to start warming');
    } finally {
      setIsStarting(false);
    }
  };

  const handleStartPost = async () => {
    if (profile.status !== 'running') {
      setActionError('Container must be started before launching automation.');
      return;
    }
    if (!caption.trim()) {
      setActionError('Caption is required to publish a post.');
      return;
    }
    setIsStarting(true);
    setActionError(null);
    try {
      const params: RunAutomationParams = {
        profile_id: profile.id,
        task: 'post',
        caption: caption.trim(),
        comment_link: commentLink.trim() || undefined,
      };
      const res = await runAutomation(params);
      setTaskState(res.state);
      setShowPostForm(false);
      setIsLogsExpanded(true);
    } catch (err: any) {
      setActionError(err.message || 'Failed to publish post');
    } finally {
      setIsStarting(false);
    }
  };

  const handleStartComment = async () => {
    if (profile.status !== 'running') {
      setActionError('Container must be started before launching automation.');
      return;
    }
    if (!testComment.trim()) {
      setActionError('Comment text is required.');
      return;
    }
    setIsStarting(true);
    setActionError(null);
    try {
      const res = await runAutomation({
        profile_id: profile.id,
        task: 'comment',
        comment_link: testComment.trim(),
      });
      setTaskState(res.state);
      setShowCommentForm(false);
      setIsLogsExpanded(true);
    } catch (err: any) {
      setActionError(err.message || 'Failed to start auto comment');
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    if (!confirm('Are you sure you want to stop the running automation?')) return;
    try {
      await stopAutomation(profile.id);
      await refreshStatus();
    } catch (err: any) {
      setActionError(err.message || 'Failed to stop task');
    }
  };

  const getStatusBadge = () => {
    switch (taskState.status) {
      case 'running':
        return (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-400 font-mono text-[10px] animate-pulse">
            <Loader2 className="w-2.5 h-2.5 animate-spin" />
            RUNNING {taskState.task ? `(${taskState.task.toUpperCase()})` : ''}
          </span>
        );
      case 'completed':
        return (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-mono text-[10px]">
            <CheckCircle2 className="w-2.5 h-2.5" />
            COMPLETED
          </span>
        );
      case 'failed':
        return (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-400 font-mono text-[10px]">
            <AlertTriangle className="w-2.5 h-2.5" />
            FAILED
          </span>
        );
      case 'uncertain':
        return (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-300 font-mono text-[10px]">
            <AlertTriangle className="w-2.5 h-2.5" />
            UNCERTAIN — CHECK FACEBOOK
          </span>
        );
      case 'stopped':
        return (
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-400 font-mono text-[10px]">
            STOPPED
          </span>
        );
      default:
        return (
          <span className="px-2 py-0.5 rounded-full bg-zinc-800/80 border border-zinc-700/60 text-zinc-400 font-mono text-[10px]">
            IDLE
          </span>
        );
    }
  };

  const visibleLogs = taskState.logs.filter((log) => log.level !== 'DEBUG');
  const latestLog = visibleLogs.length > 0 ? visibleLogs[visibleLogs.length - 1] : null;

  return (
    <div className="space-y-3 p-3 rounded-lg bg-zinc-950 border border-zinc-800/80">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-zinc-300">
          <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
          Zero-CDP Engine
        </span>
        {getStatusBadge()}
      </div>

      {actionError && (
        <div className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-[11px] text-rose-300">
          {actionError}
        </div>
      )}

      {taskState.error && (
        <div className="p-2 rounded bg-rose-950/40 border border-rose-800/60 text-[11px] text-rose-300 break-words">
          <span className="font-semibold">Last error:</span> {taskState.error}
        </div>
      )}

      {/* When Running: Show active indicator and stop button */}
      {taskState.status === 'running' ? (
        <div className="space-y-2.5">
          <div className="p-2 rounded bg-zinc-900 border border-zinc-800 space-y-1">
            <div className="flex items-center justify-between text-[11px] text-zinc-400">
              <span>Current Task</span>
              <span className="font-mono text-zinc-200 capitalize">{taskState.task}</span>
            </div>
            {latestLog && (
              <p className="text-[11px] text-emerald-400 font-mono truncate">
                &gt; {latestLog.message}
              </p>
            )}
          </div>

          <button
            onClick={handleStop}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded bg-rose-950/60 border border-rose-800/60 text-rose-300 hover:bg-rose-900/60 text-xs font-medium transition-colors"
          >
            <Square className="w-3 h-3 fill-current" />
            <span>Stop Automation</span>
          </button>
        </div>
      ) : (
        /* Action Buttons when Idle */
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2">
            <button
              onClick={() => {
                setShowWarmingForm(!showWarmingForm);
                setShowPostForm(false);
                setShowCommentForm(false);
                setActionError(null);
              }}
              className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded border text-xs font-medium transition-colors ${
                showWarmingForm
                  ? 'bg-amber-950/40 border-amber-600/60 text-amber-300'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-300 hover:bg-zinc-850 hover:text-white'
              }`}
            >
              <Flame className="w-3.5 h-3.5 text-amber-400" />
              <span>Warm Feed</span>
            </button>

            <button
              onClick={() => {
                setShowPostForm(!showPostForm);
                setShowWarmingForm(false);
                setShowCommentForm(false);
                setActionError(null);
              }}
              className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded border text-xs font-medium transition-colors ${
                showPostForm
                  ? 'bg-blue-950/40 border-blue-600/60 text-blue-300'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-300 hover:bg-zinc-850 hover:text-white'
              }`}
            >
              <Send className="w-3.5 h-3.5 text-blue-400" />
              <span>Auto Post</span>
            </button>

            <button
              onClick={() => {
                setShowCommentForm(!showCommentForm);
                setShowPostForm(false);
                setShowWarmingForm(false);
                setActionError(null);
              }}
              className={`flex items-center justify-center gap-1.5 py-1.5 px-2 rounded border text-xs font-medium transition-colors ${
                showCommentForm
                  ? 'bg-emerald-950/40 border-emerald-600/60 text-emerald-300'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-300 hover:bg-zinc-850 hover:text-white'
              }`}
            >
              <MessageCircle className="w-3.5 h-3.5 text-emerald-400" />
              <span>Auto Comment</span>
            </button>
          </div>

          {/* Warming Configuration Sub-form */}
          {showWarmingForm && (
            <div className="p-2.5 rounded bg-zinc-900/90 border border-amber-900/30 space-y-2 text-xs">
              <div className="flex justify-between items-center text-[11px]">
                <span className="text-zinc-400">Scroll Iterations:</span>
                <span className="font-mono text-amber-400 font-medium">{scrolls}</span>
              </div>
              <input
                type="range"
                min="1"
                max="10"
                value={scrolls}
                onChange={(e) => setScrolls(parseInt(e.target.value))}
                className="w-full h-1 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-amber-500"
              />
              <button
                onClick={handleStartWarming}
                disabled={isStarting}
                className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-zinc-950 font-semibold text-xs transition-colors disabled:opacity-50"
              >
                {isStarting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Flame className="w-3 h-3" />}
                <span>Launch Warming</span>
              </button>
            </div>
          )}

          {/* Post Composer Sub-form */}
          {showPostForm && (
            <div className="p-2.5 rounded bg-zinc-900/90 border border-blue-900/30 space-y-2 text-xs">
              <div>
                <label className="text-[11px] text-zinc-400 block mb-1">Post Caption</label>
                <textarea
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                  placeholder="What's on your mind?..."
                  rows={2}
                  className="w-full p-2 bg-zinc-950 border border-zinc-800 rounded text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-blue-500 resize-none"
                />
              </div>

              <div>
                <label className="text-[11px] text-zinc-400 block mb-1">
                  1st-Comment Link <span className="text-zinc-500">(Organic CTA)</span>
                </label>
                <input
                  type="text"
                  value={commentLink}
                  onChange={(e) => setCommentLink(e.target.value)}
                  placeholder="https://example.com/landing"
                  className="w-full p-1.5 bg-zinc-950 border border-zinc-800 rounded text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-blue-500 font-mono text-[11px]"
                />
              </div>

              <button
                onClick={handleStartPost}
                disabled={isStarting}
                className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs transition-colors disabled:opacity-50"
              >
                {isStarting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                <span>Publish Post</span>
              </button>
            </div>
          )}

          {showCommentForm && (
            <div className="p-2.5 rounded bg-zinc-900/90 border border-emerald-900/30 space-y-2 text-xs">
              <div>
                <label className="text-[11px] text-zinc-400 block mb-1">Test comment for first profile post</label>
                <textarea
                  value={testComment}
                  onChange={(e) => setTestComment(e.target.value)}
                  placeholder="Enter the comment to submit once…"
                  rows={2}
                  className="w-full p-2 bg-zinc-950 border border-zinc-800 rounded text-xs text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-emerald-500 resize-none"
                />
              </div>
              <button
                onClick={handleStartComment}
                disabled={isStarting}
                className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-zinc-950 font-semibold text-xs transition-colors disabled:opacity-50"
              >
                {isStarting ? <Loader2 className="w-3 h-3 animate-spin" /> : <MessageCircle className="w-3 h-3" />}
                <span>Comment on First Post</span>
              </button>
              <p className="text-[10px] text-zinc-500">Testing only: this does not create a new post and never retries submission.</p>
            </div>
          )}
        </div>
      )}

      {/* Live execution log stays available in the right inspector. */}
      <div className="pt-1">
          <button
            onClick={() => setIsLogsExpanded(!isLogsExpanded)}
            className="w-full flex items-center justify-between text-[11px] text-zinc-500 hover:text-zinc-300 py-1 transition-colors"
          >
            <span className="flex items-center gap-1 font-mono">
              <Terminal className="w-3 h-3" />
              Live execution log ({visibleLogs.length})
            </span>
            {isLogsExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>

          {isLogsExpanded && (
            <div className="mt-1.5 p-2 rounded bg-black/90 border border-zinc-800/80 font-mono text-[10px] space-y-1 max-h-64 overflow-y-auto select-text">
              {visibleLogs.length === 0 && (
                <div className="text-zinc-600 py-1">Waiting for execution logs…</div>
              )}
              {visibleLogs.map((log, idx) => (
                <div key={idx} className="leading-tight flex items-start gap-1">
                  <span className="text-zinc-600 shrink-0">
                    {log.timestamp?.includes('T') ? log.timestamp.split('T')[1]?.slice(0, 8) : log.timestamp?.slice(0, 8) || '--:--:--'}
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
                  <span className="text-zinc-300 break-words whitespace-pre-wrap">{log.message.replace(/\\n/g, '\n')}</span>
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          )}
        </div>
    </div>
  );
};
