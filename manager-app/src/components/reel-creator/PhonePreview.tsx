import { useEffect, useRef, useState } from 'react';
import { Play } from 'lucide-react';
import { Button } from '../ui/Button';
import { Spinner } from '../ui/Spinner';
import { reelApi } from '../../services/reelCreator';
import type { Reel, ReelJob } from '../../types/reelCreator';

interface Props {
  active: boolean;
  reel: Reel;
  scene: number;
  onScene: (index: number) => void;
  job: ReelJob | null;
  fresh: boolean;
  onCancel: () => void;
  onRetry: () => void;
}

/** Shows the selected scene exactly as rendered, or the finished video while it matches the edits. */
export function PhonePreview({ active, reel, scene, onScene, job, fresh, onCancel, onRetry }: Props) {
  const [poster, setPoster] = useState('');
  const [problem, setProblem] = useState('');
  const [video, setVideo] = useState('');
  const [cover, setCover] = useState('');
  const player = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [videoLoading, setVideoLoading] = useState(false);
  const [playPending, setPlayPending] = useState(false);
  const [videoProblem, setVideoProblem] = useState('');
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const shown = fresh && job?.state === 'completed';
  const working = !!job && fresh && ['queued', 'rendering', 'verifying'].includes(job.state);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let url = '';
    const timer = window.setTimeout(() => {
      reelApi.poster(reel, Math.min(scene, reel.scenes.length - 1), controller.signal).then(blob => {
        url = URL.createObjectURL(blob);
        setPoster(old => { if (old) URL.revokeObjectURL(old); return url; });
        setProblem('');
      }).catch(err => { if (!controller.signal.aborted) setProblem(err instanceof Error ? err.message : 'Preview unavailable.'); });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, reel, scene]);
  useEffect(() => () => { setPoster(old => { if (old) URL.revokeObjectURL(old); return ''; }); }, []);

  useEffect(() => {
    setVideo('');
    setCover('');
    setPlaying(false);
    setPlayPending(false);
    setVideoProblem('');
    setVideoLoading(false);
    if (!shown || !job || !active) return;
    setVideoLoading(true);
    let cancelled = false, url = '';
    let image = '';
    void reelApi.jobThumbnail(job.id).then(blob => { if (!cancelled) { image = URL.createObjectURL(blob); setCover(image); } }).catch(() => undefined);
    void reelApi.video(job.id).then(blob => {
      if (!cancelled) { url = URL.createObjectURL(blob); setVideo(url); }
    }).catch(err => {
      if (!cancelled) setVideoProblem(err instanceof Error ? err.message : 'Could not load the video preview.');
    }).finally(() => { if (!cancelled) setVideoLoading(false); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); if (image) URL.revokeObjectURL(image); setCover(''); };
  }, [shown, job?.id, active, previewAttempt]);  // eslint-disable-line react-hooks/exhaustive-deps

  const play = async () => {
    if (!player.current) return;
    setPlayPending(true);
    try {
      await player.current.play();
    } catch (err) {
      setPlayPending(false);
      setVideoProblem(err instanceof Error ? `Could not play the preview: ${err.message}` : 'Could not play the video preview.');
    }
  };

  const previewImage = (shown && cover) || poster;

  const shape = reel.ratio === '4:5' ? 'aspect-[4/5]' : 'aspect-[9/16]';
  return <div className="flex flex-col items-center gap-3">
    <div className="reel-phone relative w-full max-w-[300px] overflow-hidden rounded-[28px] border-4 border-zinc-800 bg-black shadow-xl">
      {video ? <video ref={player} key={video} src={video} poster={previewImage || undefined} controls loop playsInline preload="metadata"
          onPlaying={() => { setPlaying(true); setPlayPending(false); setVideoProblem(''); }}
          onError={() => {
            setPlaying(false); setPlayPending(false);
            setVideoProblem('The app could not decode this video preview. You can retry the preview or save the video to play it in another player.');
          }}
          className={`${shape} w-full bg-black`} />
        : previewImage ? <img src={previewImage} alt={`Scene ${scene + 1} preview`} className={`${shape} w-full object-cover`} draggable={false} />
        : <div className={`flex ${shape} w-full items-center justify-center`}><Spinner className="text-zinc-600" /></div>}
      {video && !playing && previewImage && <img src={previewImage} alt="Completed video cover" className="absolute inset-0 h-full w-full object-cover" draggable={false} />}
      {shown && !playing && <div className="absolute inset-0 flex items-center justify-center">
        {videoLoading || playPending ? <div role="status" className="rounded-full bg-black/70 p-4"><Spinner label={videoLoading ? 'Loading video preview' : 'Starting playback'} className="text-white" /></div>
          : video && !videoProblem ? <Button aria-label="Play finished video" onClick={() => { void play(); }} className="rounded-full bg-black/70 p-4 text-white hover:bg-black/90"><Play className="h-6 w-6" />Play video</Button>
          : null}
      </div>}
      {working && <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70 p-6 text-center">
        <Spinner className="h-6 w-6" />
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"><div className="h-full rounded-full bg-violet-500 transition-all" style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }} /></div>
        <p className="text-xs text-zinc-300">{job?.reason ?? (job?.state === 'queued' ? 'Waiting…' : `${Math.round((job?.progress ?? 0) * 100)}%`)}</p>
        <Button variant="outline" size="sm" onClick={onCancel}>Stop</Button>
      </div>}
    </div>
    {!video && reel.scenes.length > 1 && <div role="tablist" aria-label="Scenes" className="flex gap-1.5">
      {reel.scenes.map((_, i) => <button key={i} role="tab" type="button" aria-selected={i === scene} aria-label={`Scene ${i + 1}`} onClick={() => onScene(i)}
        className={`h-7 w-7 rounded-full text-xs transition-colors ${i === scene ? 'bg-violet-600 text-white' : 'bg-zinc-900 text-zinc-400 hover:bg-zinc-800'}`}>{i + 1}</button>)}
    </div>}
    {shown && <p className="flex items-center gap-1.5 text-xs text-zinc-500"><Play className="h-3 w-3" />{job?.kind === 'preview' ? 'Quick preview' : 'Final video'}</p>}
    {shown && videoProblem && <div className="max-w-[300px] space-y-2 text-center">
      <p role="alert" className="text-xs text-amber-400">{videoProblem}</p>
      <Button variant="outline" size="sm" onClick={() => setPreviewAttempt(value => value + 1)}>Retry preview</Button>
    </div>}
    {problem && <p role="alert" className="max-w-[300px] text-center text-xs text-amber-400">{problem}</p>}
    {fresh && job && ['failed', 'interrupted', 'cancelled'].includes(job.state) && <div className="max-w-[300px] space-y-2 text-center">
      <p role="alert" className="text-xs text-red-400">{job.error ?? 'The video stopped.'}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>Try again</Button>
    </div>}
  </div>;
}
