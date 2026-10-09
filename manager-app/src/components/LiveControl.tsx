import { useEffect, useState } from 'react';
import { Radio, Square, Upload, Loader2 } from 'lucide-react';
import { Profile } from '../types/profile';
import { MediaItem } from '../types/automation';
import { fetchMediaList, uploadMediaFiles } from '../services/api';
import { fetchLiveSettings, fetchLiveStatus, startLive, stopLive, reviewLive, LiveSession, LiveSettings } from '../services/live';
import { useAppDialog } from './ui/AppDialogProvider';
import { Select } from './ui/Select';

const stageLabels: Record<string, string> = {
  idle: 'Ready', preparing: 'Preparing', stream_start_intent: 'Connecting', sending: 'Sending video',
  starting: 'Waiting for Facebook', live: 'Live', ending: 'Ending broadcast', stopping: 'Stopping',
  ended: 'Ended', failed: 'Failed', needs_review: 'Check Facebook', reviewed: 'Reviewed',
};
const clock = (seconds = 0) => {
  const value = Math.floor(seconds);
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
};

export function LiveControl({ profile, onRefresh }: {profile: Profile; onRefresh?: () => void}) {
  const { showConfirm } = useAppDialog();
  const [settings, setSettings] = useState<LiveSettings | null>(null);
  const [videos, setVideos] = useState<MediaItem[]>([]);
  const [filename, setFilename] = useState('');
  const [title, setTitle] = useState('');
  const [caption, setCaption] = useState('');
  const [pinnedComment, setPinnedComment] = useState('');
  const [session, setSession] = useState<LiveSession>({profile_id: profile.id, status: 'idle', stage: 'idle'});
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    let polling = false;
    setSettings(null);
    setFilename('');
    setTitle(''); setCaption(''); setPinnedComment('');
    setError(null);
    setConnected(false);
    setSession({profile_id: profile.id, status: 'idle', stage: 'idle'});
    Promise.all([fetchLiveSettings(profile.id), fetchMediaList()]).then(([value, media]) => {
      if (disposed) return;
      setSettings(value);
      setVideos(media.filter(item => item.type === 'reel'));
    }).catch(err => { if (!disposed) setError(err.message); });
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const state = await fetchLiveStatus(profile.id);
        if (!disposed) { setSession(state); setConnected(true); }
      } catch { if (!disposed) setConnected(false); }
      finally { polling = false; }
    };
    void poll();
    const timer = setInterval(poll, 2000);
    return () => { disposed = true; clearInterval(timer); };
  }, [profile.id, profile.live?.configured]);

  const act = async (operation: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await operation(); }
    catch (err) { setError(err instanceof Error ? err.message : 'Live action failed'); }
    finally { setBusy(false); }
  };
  const running = session.status === 'running';
  const review = session.status === 'needs_review';

  return <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 space-y-3 text-xs" aria-label="Facebook Live">
    <div className="flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-1.5 font-semibold text-zinc-200"><Radio className="h-4 w-4 text-rose-400" /> Facebook Live</h3>
      <span role="status" className={session.stage === 'live' && connected ? 'text-rose-400 font-semibold' : 'text-zinc-400'}>
        {!connected ? 'Reconnecting…' : stageLabels[session.stage] || session.stage}
      </span>
    </div>
    {running ? <>
      <p className="truncate text-zinc-300" title={session.filename}>{session.filename}</p>
      <div className="flex justify-between text-zinc-400"><span>Video progress</span><span className="font-mono">{clock(session.elapsed_seconds)} / {clock(session.duration_seconds)}</span></div>
      {session.duration_seconds && <progress className="w-full accent-rose-500" value={session.elapsed_seconds || 0} max={session.duration_seconds} aria-label="Video progress" />}
      <button disabled={busy || session.stage === 'stopping' || session.stage === 'ending'} onClick={() => act(async () => { await stopLive(profile.id); setSession(await fetchLiveStatus(profile.id)); })}
        className="w-full flex items-center justify-center gap-2 rounded bg-rose-950 border border-rose-800 py-2 text-rose-200 disabled:opacity-50">
        <Square className="h-3 w-3" /> Stop Live
      </button>
    </> : <>
      <div className="space-y-1 text-zinc-400"><span>Video</span>
        <Select
          value={filename}
          onValueChange={setFilename}
          ariaLabel="Video"
          placeholder="Choose an uploaded video"
          options={videos.map(video => ({ value: video.filename, label: video.original_name || video.filename }))}
          disabled={busy || review || videos.length === 0}
          className="focus:border-rose-500 focus:ring-rose-500/20"
        />
      </div>
      <label className={`flex items-center justify-center gap-2 rounded border border-zinc-700 py-2 text-zinc-300 ${busy || review ? 'opacity-50' : 'cursor-pointer hover:bg-zinc-900'}`}>
        <Upload className="h-3 w-3" /> Upload video
        <input aria-label="Upload live video" className="hidden" type="file" accept=".mp4,.mov,.webm" disabled={busy || review} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
          void act(async () => { const data = new FormData(); data.append('files', file); const result = await uploadMediaFiles(data);
            setVideos((await fetchMediaList()).filter(item => item.type === 'reel')); setFilename(result.files[0].filename); });
        }} />
      </label>
      <input aria-label="Live title" placeholder="Live title" value={title} maxLength={255} disabled={busy || review}
        onChange={e => setTitle(e.target.value)} className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-2 text-zinc-200" />
      <textarea aria-label="Live caption" placeholder="Live caption" value={caption} maxLength={5000} rows={3} disabled={busy || review}
        onChange={e => setCaption(e.target.value)} className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-2 text-zinc-200 resize-none" />
      <details className="text-zinc-400">
        <summary className="cursor-pointer">Pinned comment (optional)</summary>
        <textarea aria-label="Preset pinned comment" placeholder="Comment to pin when Live starts" value={pinnedComment}
          maxLength={1000} rows={2} disabled={busy || review} onChange={e => setPinnedComment(e.target.value)}
          className="mt-2 w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-2 text-zinc-200 resize-none" />
        <p className="mt-1 text-[11px] text-zinc-500">Leave blank to disable the preset for this Live.</p>
      </details>
      <p className="text-[11px] text-zinc-400">{settings?.key_saved ? 'Streaming key saved · manage in Edit profile' : 'Add streaming settings in Edit profile to enable Live.'}</p>
      <button disabled={busy || !connected || !settings?.key_saved || !filename || !title.trim() || !caption.trim() || review} onClick={() => act(async () => {
        setSession(await startLive(profile.id, filename, title.trim(), caption.trim(), pinnedComment.trim())); onRefresh?.();
      })} className="flex w-full items-center justify-center gap-2 rounded bg-rose-600 hover:bg-rose-500 py-2 font-semibold text-white disabled:opacity-40">
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radio className="h-3.5 w-3.5" />} Live
      </button>
      {review && session.queue_execution_id && <p className="text-amber-300">Review &amp; Resolve this Live execution in the batch queue after checking Facebook.</p>}
      {review && !session.queue_execution_id && <button disabled={busy || !connected} className="w-full rounded border border-amber-800 py-2 text-amber-300 disabled:opacity-50" onClick={async () => {
        if (await showConfirm('Confirm on Facebook that the previous broadcast has ended before allowing another Live session.', {title: 'Review previous broadcast', confirmLabel: 'Broadcast has ended'})) {
          void act(async () => { setSession(await reviewLive(profile.id)); onRefresh?.(); });
        }
      }}>I checked Facebook — broadcast ended</button>}
    </>}
    {(error || session.error) && <p role="alert" className="text-amber-300 break-words leading-relaxed">{error || session.error}</p>}
  </section>;
}
