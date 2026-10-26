import { useEffect, useState } from 'react';
import { Check, Download, Film, Play, Plus, Send } from 'lucide-react';
import { useReelStudio } from '../../hooks/useReelStudio';
import { hasContent, reelSeconds, draftTitle } from '../../services/reelDocument';
import type { CreatedReelMedia } from '../../types/reelCreator';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';
import { Spinner } from '../ui/Spinner';
import { FinishPanel } from './FinishPanel';
import { LookPicker } from './LookPicker';
import { PhonePreview } from './PhonePreview';
import { SceneList } from './SceneList';
import { SourceInput } from './SourceInput';
import './reelCreator.css';

interface Props {
  active: boolean;
  onUseReel: (media: CreatedReelMedia) => void;
}

export function ReelCreator({ active, onUseReel }: Props) {
  const studio = useReelStudio(active, onUseReel);
  const { doc, capabilities, busy, job, fresh } = studio;
  const [scene, setScene] = useState(0);
  const [started, setStarted] = useState(false);
  const hasReel = hasContent(doc.reel) || doc.reel.scenes.some(s => s.image_id) || started;
  useEffect(() => { setScene(0); setStarted(false); }, [studio.draftId]);
  useEffect(() => { setScene(index => Math.min(index, doc.reel.scenes.length - 1)); }, [doc.reel.scenes.length]);

  if (!studio.loaded) return <div className="flex h-full items-center justify-center p-10">
    {studio.error ? <Alert variant="danger" className="max-w-md">{studio.error}</Alert> : <Spinner label="Loading Reel Studio" className="text-zinc-500" />}
  </div>;
  if (!capabilities?.ready) return <div className="p-6"><Alert variant="warning" className="max-w-xl">
    Rendering is not ready. {capabilities?.missing.join(', ')}. {capabilities?.repair}</Alert></div>;

  const writing = busy === 'write';
  const canWrite = !!(doc.sourceText || doc.sourceUrl);
  const rendering = busy === 'render' || (!!job && fresh && ['queued', 'rendering', 'verifying'].includes(job.state));
  const done = !!job && fresh && job.state === 'completed' ? job : null;
  const recent = studio.drafts.map(d => ({ value: d.id, label: draftTitle(d.reel) }));
  const edit = (index: number, patch: Partial<typeof doc.reel.scenes[number]>) =>
    studio.setReel(reel => ({ ...reel, scenes: reel.scenes.map((s, i) => i === index ? { ...s, ...patch } : s) }));

  return <div className="reel-creator-shell mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 lg:p-6">
    <header className="flex items-center gap-3">
      <Film className="h-4 w-4 text-violet-400" aria-hidden="true" />
      {recent.length > 0 && <div className="w-56"><Select value={recent.some(r => r.value === studio.draftId) ? studio.draftId : ''} options={recent}
        placeholder="Recent reels" ariaLabel="Recent reels" onValueChange={id => studio.open(studio.drafts.find(d => d.id === id) ?? null)} /></div>}
      <Button variant="outline" size="sm" disabled={!!busy} onClick={() => studio.open(null)}><Plus className="h-3.5 w-3.5" />New</Button>
      <span className="ml-auto text-xs text-zinc-500" aria-live="polite">{studio.saveState}</span>
    </header>

    {studio.error && <Alert variant="danger" role="alert">{studio.error}</Alert>}
    {studio.notice && <Alert variant="success">{studio.notice}</Alert>}

    {!hasReel
      ? <SourceInput looks={capabilities.looks} busy={writing} goal={doc.goal} onGoal={studio.setGoal}
          onCreate={(source, look, photos, goal) => { void studio.create(source, look, photos, goal); }}
          importImages={studio.importImages} onBlank={() => setStarted(true)} />
      : <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4 lg:sticky lg:top-4 lg:self-start">
          <PhonePreview active={active} reel={doc.reel} scene={scene} onScene={setScene} job={job} fresh={fresh}
            onCancel={() => { void studio.updateJob('cancel'); }} onRetry={() => { void studio.updateJob('retry'); }} />
          <div className="mx-auto flex max-w-[300px] flex-col gap-2">
            {done?.kind === 'final'
              ? <>
                <Button size="lg" onClick={studio.useVideo}><Send className="h-4 w-4" />Use for Reel</Button>
                <Button variant="outline" onClick={() => { void studio.saveFile(); }}><Download className="h-4 w-4" />Save video</Button>
                <p className="text-center text-xs text-zinc-500">Choose accounts, then post now or schedule.</p>
              </>
              : <>
                <Button size="lg" loading={rendering} disabled={rendering || !!busy} onClick={() => { void studio.startRender('final'); }}>
                  <Check className="h-4 w-4" />Create video · {reelSeconds(doc.reel)}s
                </Button>
                <Button variant="outline" disabled={rendering || !!busy} onClick={() => { void studio.startRender('preview'); }}><Play className="h-4 w-4" />Quick preview</Button>
              </>}
          </div>
        </div>

        <div className="min-w-0 space-y-6">
          <LookPicker looks={capabilities.looks} ratios={capabilities.ratios} reel={doc.reel}
            onLook={look => studio.setReel(reel => ({ ...reel, look }))} onTag={tag => studio.setReel(reel => ({ ...reel, tag }))}
            onRatio={ratio => studio.setReel(reel => ({ ...reel, ratio }))} />
          <SceneList reel={doc.reel} hooks={doc.hooks} selected={scene} busy={!!busy} writing={busy === 'hooks'} adding={busy === 'scene'} canWrite={canWrite}
            onSelect={setScene} onEdit={edit} onHook={studio.pickHook} onMoreHooks={() => { void studio.moreHooks(); }}
            onPhoto={index => { void studio.changePhoto(index); }} onPhotos={() => { void studio.addPhotos(); }}
            onMove={studio.moveScene} onRemove={studio.removeScene} onAdd={studio.addScene} />
          <FinishPanel reel={doc.reel} goal={doc.goal} onGoal={studio.setGoal} comment={doc.comment} onComment={comment => studio.setDoc(d => ({ ...d, comment }))} looks={capabilities.looks} tracks={capabilities.music} caption={doc.caption} busy={!!busy} writing={busy === 'caption'} canWrite={canWrite}
            onBrand={studio.setBrand} onDate={dateline => studio.setReel(reel => ({ ...reel, dateline }))} onProgress={show_progress => studio.setReel(reel => ({ ...reel, show_progress }))}
            onMotion={motion => studio.setReel(reel => ({ ...reel, motion }))}
            onMusic={studio.setMusic} onPickMusic={() => { void studio.pickMusic(); }}
            onLogo={file => { void studio.uploadLogo(file); }} onCaption={caption => studio.setDoc(d => ({ ...d, caption }))}
            onRewrite={() => { void studio.rewriteCaption(); }} />
          <Button variant="ghost" size="sm" className="text-zinc-500" onClick={() => { void studio.discard(); }}>Delete this reel</Button>
        </div>
      </div>}
  </div>;
}
