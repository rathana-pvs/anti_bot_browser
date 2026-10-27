import { useEffect, useState } from 'react';
import { Film, Newspaper, Plus } from 'lucide-react';
import { useReelStudio } from '../../hooks/useReelStudio';
import { articleSupports, hasContent, reelSeconds, draftTitle, patchStyle, unstyledScene } from '../../services/reelDocument';
import type { CreatedReelMedia, StyleRole } from '../../types/reelCreator';
import { Alert } from '../ui/Alert';
import { Button } from '../ui/Button';
import { Select } from '../ui/Select';
import { Spinner } from '../ui/Spinner';
import { FinishPanel } from './FinishPanel';
import { HouseStylePicker } from './HouseStylePicker';
import { UpdateStoryDialog } from './UpdateStoryDialog';
import { LookPicker } from './LookPicker';
import { OutputPanel } from './OutputPanel';
import { PhonePreview } from './PhonePreview';
import { SceneList } from './SceneList';
import { SourceInput } from './SourceInput';
import { reelOnly, StylePanel } from './StylePanel';
import { useSceneLayout } from '../../hooks/useSceneLayout';
import './reelCreator.css';

interface Props {
  active: boolean;
  onUseReel: (media: CreatedReelMedia) => void;
}

type Mode = 'simple' | 'advanced';
const savedMode = (): Mode => { try { return localStorage.getItem('reel_creator_mode') === 'advanced' ? 'advanced' : 'simple'; } catch { return 'simple'; } };

export function ReelCreator({ active, onUseReel }: Props) {
  const studio = useReelStudio(active, onUseReel);
  const { doc, capabilities, busy, job, fresh } = studio;
  const [scene, setScene] = useState(0);
  const [started, setStarted] = useState(false);
  const [mode, setModeState] = useState<Mode>(savedMode);
  const [scope, setScope] = useState<'reel' | 'scene'>('reel');
  const [role, setRole] = useState<StyleRole>('headline');
  const [updating, setUpdating] = useState(false);
  const layout = useSceneLayout(active && studio.loaded, studio.shown, scene);
  const pick = (target: StyleRole) => {
    setMode('advanced'); setRole(target);
    setScope(reelOnly.includes(target) ? 'reel' : 'scene');
    window.requestAnimationFrame(() => document.getElementById('reel-style')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
  };
  const setMode = (next: Mode) => {
    setModeState(next);
    try { localStorage.setItem('reel_creator_mode', next); } catch { /* The mode is a per-viewer convenience. */ }
  };
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
  // Plain values, not a hook: this runs after the early returns above. An article only known by its link is not
  // read again here, so every type stays open for it.
  const supports = doc.sourceText ? articleSupports(doc.sourceText) : { story: true, stat: true, quote: true, end: true };
  const rendering = busy === 'render' || (!!job && fresh && ['queued', 'rendering', 'verifying'].includes(job.state));
  const done = !!job && fresh && job.state === 'completed' ? job : null;
  const recent = studio.drafts.map(d => ({ value: d.id, label: draftTitle(d.reel) }));
  const edit = (index: number, patch: Partial<typeof doc.reel.scenes[number]>) =>
    studio.setReel(reel => ({ ...reel, scenes: reel.scenes.map((s, i) => i === index ? { ...s, ...patch } : s) }));

  return <div className="reel-creator-shell mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 lg:p-6">
    <header className="flex items-center gap-3">
      <Film className="h-4 w-4 text-violet-400" aria-hidden="true" />
      {recent.length > 0 && <div className="w-80 max-w-full shrink-0"><Select className="whitespace-nowrap [&>span:first-child]:min-w-0 [&>span:first-child]:flex-1 [&>span:first-child]:truncate" value={recent.some(r => r.value === studio.draftId) ? studio.draftId : ''} options={recent}
        placeholder="Recent reels" ariaLabel="Recent reels" onValueChange={id => studio.open(studio.drafts.find(d => d.id === id) ?? null)} /></div>}
      <Button variant="outline" size="sm" disabled={!!busy} onClick={() => studio.open(null)}><Plus className="h-3.5 w-3.5" />New</Button>
      {hasContent(doc.reel) && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => setUpdating(true)} title="Rewrite this reel from newer reporting as a new reel">
        <Newspaper className="h-3.5 w-3.5" />Update story</Button>}
      <UpdateStoryDialog key={studio.draftId} open={updating} busy={writing} onOpenChange={setUpdating}
        onUpdate={(source, mark) => { void studio.updateStory(source, mark).then(() => setUpdating(false)); }} />
      <span className="ml-auto text-xs text-zinc-500" aria-live="polite">{studio.saveState}</span>
      {hasReel && <div role="radiogroup" aria-label="Editing mode" className="flex rounded-lg border border-zinc-800 p-0.5">
        {(['simple', 'advanced'] as const).map(value => <button key={value} type="button" role="radio" aria-checked={mode === value} onClick={() => setMode(value)}
          className={`rounded-md px-3 py-1.5 text-xs capitalize transition-colors ${mode === value ? 'bg-zinc-800 text-white' : 'text-zinc-500 hover:text-zinc-200'}`}>{value}</button>)}
      </div>}
    </header>

    {studio.error && <Alert variant="danger" role="alert">{studio.error}</Alert>}
    {studio.notice && <Alert variant="success"><div className="flex flex-1 items-center justify-between gap-3"><span>{studio.notice}</span>
      {studio.undo && <Button variant="outline" size="sm" onClick={studio.undoRewrite}>Undo</Button>}</div></Alert>}

    {!hasReel
      ? <SourceInput looks={capabilities.looks} houseStyles={studio.houseStyles} busy={writing} goal={doc.goal} onGoal={studio.setGoal}
          onCreate={(source, look, photos, goal, house) => { void studio.create(source, look, photos, goal, house); }}
          importImages={studio.importImages} onBlank={() => setStarted(true)} />
      : <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4 lg:sticky lg:top-4 lg:self-start">
          <PhonePreview active={active} reel={studio.shown} scene={scene} onScene={setScene} job={job} fresh={fresh}
            onCancel={() => { void studio.updateJob('cancel'); }} onRetry={() => { void studio.updateJob('retry'); }}
            layout={layout} selected={mode === 'advanced' ? role : null} onPick={pick} onFrame={patch => edit(scene, patch)} />
          <OutputPanel output={studio.output} onOutput={studio.setOutput} photoRatio={studio.photoRatio} onPhotoRatio={studio.setPhotoRatio}
            scene={scene} seconds={reelSeconds(doc.reel)} reelRatio={doc.reel.ratio} rendering={rendering} busy={!!busy} makingPhoto={busy === 'photo'}
            ready={done?.kind === 'final'} onCreate={kind => { void studio.startRender(kind); }}
            onSend={() => { void studio.send(scene); }} onSave={() => { void studio.saveFile(scene); }} />
        </div>

        <div className="min-w-0 space-y-6">
          <LookPicker looks={capabilities.looks} ratios={capabilities.ratios} reel={doc.reel}
            onLook={look => studio.setReel(reel => ({ ...reel, look }))} onTag={tag => studio.setReel(reel => ({ ...reel, tag }))}
            onRatio={ratio => studio.setReel(reel => ({ ...reel, ratio }))} onLive={live => studio.setReel(reel => ({ ...reel, live }))}
            houseStyles={<HouseStylePicker styles={studio.houseStyles} reel={doc.reel} busy={!!busy} onPick={studio.pickHouseStyle}
              onSave={name => { void studio.saveHouseStyle(name); }} onDelete={id => { void studio.deleteHouseStyle(id); }} />} />
          <SceneList reel={doc.reel} hooks={doc.hooks} selected={scene} busy={!!busy} writing={busy === 'hooks'} adding={busy === 'scene'} canWrite={canWrite}
            rewriting={studio.rewriting} onRewrite={index => { void studio.rewriteScene(index); }}
            undoable={studio.undo?.index ?? null} onUndo={studio.undoRewrite} supports={supports}
            onSelect={setScene} onEdit={edit} onHook={studio.pickHook} onMoreHooks={() => { void studio.moreHooks(); }}
            onPhoto={index => { void studio.changePhoto(index); }} onPhotos={() => { void studio.addPhotos(); }}
            onMove={studio.moveScene} onRemove={studio.removeScene} onAdd={studio.addScene} />
          {mode === 'advanced' && <StylePanel reel={doc.reel} looks={capabilities.looks} scene={scene} scope={scope} onScope={setScope} role={role} onRole={setRole}
            onStyle={(target, patch) => {
              if (scope === 'reel' || ['brand', 'logo', 'ticker'].includes(target)) studio.setReel(reel => ({ ...reel, style: patchStyle(reel.style, target, patch) }));
              else edit(scene, { style: patchStyle(doc.reel.scenes[scene]?.style, target, patch) });
            }}
            onScene={patch => edit(scene, patch)} onAccent={accent => studio.setReel(reel => ({ ...reel, accent }))}
            onResetScene={() => studio.setReel(reel => ({ ...reel, scenes: reel.scenes.map((s, i) => i === scene ? unstyledScene(s) : s) }))} layout={layout} />}
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
