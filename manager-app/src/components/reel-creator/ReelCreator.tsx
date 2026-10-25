import { useContentFit } from '../../hooks/useContentFit';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Clapperboard, Film, Download, ArrowRight, Loader2, Plus, RefreshCw, Image as ImageIcon, X, Check } from 'lucide-react';
import { reelApi } from '../../services/reelCreator';
import { emptyComposition, emptyEditorial } from '../../types/reelCreator';
import type { CreatedReelMedia, EditorialDraft, ReelAsset, ReelCapabilities, ReelComposition, ReelDraft, ReelJob } from '../../types/reelCreator';
import { NewsAssistant } from './NewsAssistant';
import { NewsAudio } from './NewsAudio';
import { PhotoStoryEditor } from './PhotoStoryEditor';
import { ReelTextEditor } from './ReelTextEditor';
import { ReelStyleEditor } from './ReelStyleEditor';
import { NewsNarration } from './NewsNarration';
import { reelDimensions, switchReelRatio } from '../../services/photoFraming';
import { reelPhotos, storyUpdate, reelDurations, minimumDuration, normalizeTiming } from '../../services/storyComposition';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { Select } from '../ui/Select';
import './reelCreator.css';

interface Props {
  active: boolean;
  onUseReel: (media: CreatedReelMedia) => void;
}

const inputClass = 'w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5 text-xs text-zinc-100 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10';
const templates = [
  { id: 'bbc', name: 'BBC News', badge: 'Broadcast Strap', description: 'Dynamic lower-third broadcast strap with high-contrast headline card and kinetic slide reveals.', color: '#bb1919', kind: 'bbc' },
  { id: 'economist', name: 'The Economist', badge: 'Editorial Serif', description: 'Iconic red masthead crest, refined serif typography, and balanced analytical commentary.', color: '#e3120b', kind: 'economist' },
  { id: 'reuters', name: 'Reuters', badge: 'Wire Fact-Check', description: 'High-contrast wire layout, signature Reuters orange accents, and stepped fact highlights.', color: '#ff8000', kind: 'reuters' },
  { id: 'ap_wire', name: 'AP News', badge: 'Breaking Alert', description: 'Urgent breaking news alert ribbon, dateline stamps, and punchy high-impact typography.', color: '#d9232a', kind: 'ap_wire' },
  { id: 'lens_report', name: 'Lens Report', badge: 'Photo Feature', description: 'Immersive full-canvas photography with editorial documentary captions and line-by-line reveals.', color: '#38bdf8', kind: 'lens_report' },
  { id: 'daily_brief', name: 'Daily Brief', badge: 'Framed Digest', description: 'Structured card layout with scene progress bar, clean typography, and source attribution.', color: '#10b981', kind: 'daily_brief' },
] as const;
const newsPresets = [
  { name: 'BBC Broadcast', category: 'WORLD NEWS', template: 'bbc', accent: '#bb1919' },
  { name: 'The Economist', category: 'ANALYSIS', template: 'economist', accent: '#e3120b' },
  { name: 'Reuters Wire', category: 'SPECIAL REPORT', template: 'reuters', accent: '#ff8000' },
  { name: 'AP Breaking', category: 'BREAKING NEWS', template: 'ap_wire', accent: '#d9232a' },
  { name: 'US Politics', category: 'US POLITICS', template: 'ap_wire', accent: '#2563eb' },
] as const;
const editorTabs = [
  { id: 'story', label: 'Story' },
  { id: 'media', label: 'Media' },
  { id: 'style', label: 'Style' },
] as const;
type EditorTab = typeof editorTabs[number]['id'];

const busy = (job?: ReelJob | null) => !!job && ['queued', 'rendering', 'verifying'].includes(job.state);
const fingerprint = (value: ReelComposition) => JSON.stringify(value);
const draftFingerprint = (composition: ReelComposition, editorial: EditorialDraft) => JSON.stringify([composition, editorial]);
const newId = () => crypto.randomUUID().replace(/-/g, '');
const message = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';

export function ReelCreator({ active, onUseReel }: Props) {
  const [editorTab, setEditorTab] = useState<EditorTab>('story');
  const tabId = useId();
  const editorRef = useRef<HTMLDivElement>(null);
  const scrollSurfaceRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const previewScrollRef = useRef<HTMLElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectEditorTab = (tab: EditorTab) => {
    setEditorTab(tab);
    if (editorRef.current) editorRef.current.scrollTop = 0;
  };
  const [composition, setComposition] = useState<ReelComposition>(emptyComposition);
  const [editorial, setEditorial] = useState<EditorialDraft>(emptyEditorial);
  const editorialRef = useRef(editorial);
  editorialRef.current = editorial;
  const compositionRef = useRef(composition);
  compositionRef.current = composition;
  const draftRef = useRef({ id: newId(), revision: 0 });
  const savedHash = useRef('');
  const saveChain = useRef<Promise<ReelDraft | undefined>>(Promise.resolve(undefined));
  const requestIds = useRef(new Map<string, string>());
  const loadEpoch = useRef(0);
  const [drafts, setDrafts] = useState<ReelDraft[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [capabilities, setCapabilities] = useState<ReelCapabilities | null>(null);
  const [saveState, setSaveState] = useState('Loading…');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [posterUrl, setPosterUrl] = useState('');
  const [posterError, setPosterError] = useState('');
  const [posterHash, setPosterHash] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [jobs, setJobs] = useState<ReelJob[]>([]);
  const [selectedPhoto, setSelectedPhoto] = useState(0);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [videoPosterUrl, setVideoPosterUrl] = useState('');
  const [uploading, setUploading] = useState<ReelAsset['kind'] | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [assetNames, setAssetNames] = useState<Record<string, string>>({});
  const [brand, setBrand] = useState<Pick<ReelComposition, 'brand' | 'accent' | 'logo_id'> | null>(null);
  const [view, setView] = useState<'poster' | 'video'>('poster');
  const [showGuide, setShowGuide] = useState(false);
  const [guideBottom, setGuideBottom] = useState(20);
  const posterRef = useRef('');
  const hash = fingerprint(composition);
  const contentCheck = useContentFit(composition, loaded && active);
  const photos = reelPhotos(composition);
  const photoIds = photos.map(p => p.image_id).join(',');
  const layoutHash = hash + ':' + selectedPhoto;
  const draftHash = draftFingerprint(composition, editorial);
  const job = jobs.find(j => j.id === selectedJobId) || jobs.find(j => j.composition.image_id === composition.image_id);
  const displayed = view === 'video' && job ? job.composition : composition;
  const [displayWidth, displayHeight] = reelDimensions(displayed);
  const previewSize = reelDimensions(displayed, true);
  const formatLabel = `${displayWidth} × ${displayHeight} · ${displayed.fps} fps · MP4`;
  const jobBusy = busy(job);
  const anyBusy = jobs.some(busy);
  const stale = !!job && fingerprint(job.composition) !== hash;

  const patch = (value: Partial<ReelComposition>) => setComposition(old => {
    const next = {...old, ...value};
    return normalizeTiming({
      ...next, version: next.template === 'lens_report' ? 3 : next.version === 3 ? 2 : next.version,
      ...(value.template ? {layout_revision: value.template === 'lens_report' ? 2 : 1} : {}),
      ...(value.narration_id === null ? {narration_duration: null} : {}),
    });
  });

  useEffect(() => {
    const surface = scrollSurfaceRef.current;
    if (!surface) return;
    // Route blank-space scrolling to the nearest column, including the outer margins.
    const scrollFromBlankSpace = (event: WheelEvent) => {
      if (event.ctrlKey || !event.deltaY) return;
      if (event.target !== surface && event.target !== surface.firstElementChild && event.target !== gridRef.current) return;
      const editor = editorRef.current;
      const grid = gridRef.current;
      if (!editor || !grid) return;
      let target: HTMLElement = grid;
      const preview = previewScrollRef.current;
      if (getComputedStyle(editor).overflowY === 'auto' && preview) {
        const editorBounds = editor.getBoundingClientRect();
        const previewBounds = preview.getBoundingClientRect();
        const divider = (editorBounds.right + previewBounds.left) / 2;
        target = event.clientX >= divider ? preview : editor;
      }
      if (target.scrollHeight <= target.clientHeight) return;
      const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? target.clientHeight : 1;
      event.preventDefault();
      target.scrollTop += event.deltaY * units;
    };
    surface.addEventListener('wheel', scrollFromBlankSpace, { passive: false });
    return () => surface.removeEventListener('wheel', scrollFromBlankSpace);
  }, []);


  const persist = useCallback(() => {
    setSaveState('Saving…');
    const work = saveChain.current.catch(() => undefined).then(async () => {
      const snapshot = compositionRef.current;
      const editorialSnapshot = editorialRef.current;
      const snapshotHash = draftFingerprint(snapshot, editorialSnapshot);
      if (savedHash.current === snapshotHash && draftRef.current.revision > 0) {
        setSaveState('Saved');
        return undefined;
      }
      const result = await reelApi.saveDraft(draftRef.current.id, draftRef.current.revision, snapshot, editorialSnapshot);
      draftRef.current.revision = result.revision;
      savedHash.current = snapshotHash;
      setDrafts(old => [result, ...old.filter(d => d.id !== result.id)]);
      setSaveState(draftFingerprint(compositionRef.current, editorialRef.current) === snapshotHash ? 'Saved' : 'Unsaved changes');
      return result;
    });
    saveChain.current = work;
    return work;
  }, []);

  const initialize = useCallback(async () => {
    const epoch = ++loadEpoch.current;
    try {
      setError('');
      const [caps, saved, renders, savedBrand] = await Promise.all([
        reelApi.capabilities(true), reelApi.drafts(), reelApi.jobs(), reelApi.brand(),
      ]);
      if (epoch !== loadEpoch.current) return;
      if (!caps.aspect_ratios) throw new Error('Aspect ratio support needs the updated local backend. Restart the development server or reopen the updated app.');
      if (!caps.slideshow_controls) throw new Error('Slideshow settings need the updated local backend. Reopen the updated app, or restart your independent worker service after updating its runtime.');
      setCapabilities(caps); setDrafts(saved); setJobs(renders); setBrand(savedBrand);
      const preferred = localStorage.getItem('reel_creator_draft');
      const initial = saved.find(d => d.id === preferred) || saved[0];
      if (initial) {
        draftRef.current = { id: initial.id, revision: initial.revision };
        savedHash.current = draftFingerprint(initial.composition, initial.editorial);
        setComposition(initial.composition);
        setEditorial(initial.editorial);
      }
      setLoaded(true); setSaveState(initial ? 'Saved' : 'New draft');
    } catch (err) {
      if (epoch === loadEpoch.current) { setError(message(err)); setSaveState('Could not load drafts'); }
    }
  }, []);

  useEffect(() => { void initialize(); return () => { loadEpoch.current += 1; }; }, [initialize]);
  useEffect(() => {
    if (!loaded) return;
    localStorage.setItem('reel_creator_draft', draftRef.current.id);
    if (savedHash.current === draftHash && draftRef.current.revision > 0) return;
    setSaveState('Unsaved changes');
    const timer = window.setTimeout(() => {
      void persist().catch(err => { setSaveState('Could not save'); setError(message(err)); });
    }, 650);
    return () => window.clearTimeout(timer);
  }, [draftHash, loaded, persist]);

  useEffect(() => {
    if (!loaded || !active) return;
    let cancelled = false;
    let timer: number;
    const poll = async () => {
      try { const result = await reelApi.jobs(); if (!cancelled) setJobs(result); }
      catch (err) { if (!cancelled) setError(message(err)); }
      if (!cancelled) timer = window.setTimeout(poll, 2000);
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [loaded, active]);

  useEffect(() => {
    const ids = [...photoIds.split(',').filter(Boolean), composition.logo_id, composition.audio_id].filter((id): id is string => !!id);
    let cancelled = false;
    for (const id of ids) {
      if (!assetNames[id]) void reelApi.asset(id).then(asset => {
        if (!cancelled) setAssetNames(old => ({ ...old, [id]: asset.name }));
      }).catch(() => { if (!cancelled) setError('An input asset is missing. Import it again.'); });
    }
    return () => { cancelled = true; };
  }, [photoIds, composition.logo_id, composition.audio_id, assetNames]);

  useEffect(() => {
    if (!active || !loaded) return;
    if (!composition.image_id) {
      if (posterRef.current) URL.revokeObjectURL(posterRef.current);
      posterRef.current = '';
      setPosterUrl(''); setPosterHash(''); setPosterError(''); setWarnings([]);
      return;
    }
    if (anyBusy) return;
    const controller = new AbortController();
    let alive = true;
    const timer = window.setTimeout(async () => {
      setPreviewing(true);
      try {
        const result = await reelApi.poster(composition, controller.signal, selectedPhoto);
        if (!alive) return;
        const url = URL.createObjectURL(result.blob);
        if (posterRef.current) URL.revokeObjectURL(posterRef.current);
        posterRef.current = url;
        setPosterUrl(url); setPosterHash(layoutHash); setWarnings(result.warnings); setPosterError('');
      } catch (err) {
        if (alive && !controller.signal.aborted) setPosterError(message(err));
      } finally { if (alive) setPreviewing(false); }
    }, 500);
    return () => { alive = false; controller.abort(); window.clearTimeout(timer); setPreviewing(false); };
  }, [layoutHash, loaded, active, anyBusy]); // The hash covers the complete composition snapshot.

  useEffect(() => () => { if (posterRef.current) URL.revokeObjectURL(posterRef.current); }, []);

  useEffect(() => {
    setVideoUrl('');
    setVideoPosterUrl('');
    if (job?.state !== 'completed' || !active) return;
    let cancelled = false;
    let url = '';
    let coverUrl = '';
    const controller = new AbortController();
    void reelApi.video(job.id).then(blob => {
      if (cancelled) return;
      url = URL.createObjectURL(blob); setVideoUrl(url);
    }).catch(err => { if (!cancelled) setError(message(err)); });
    // Use the saved render's composition so later edits cannot change its cover.
    void reelApi.poster(job.composition, controller.signal, 0).then(result => {
      if (cancelled) return;
      coverUrl = URL.createObjectURL(result.blob); setVideoPosterUrl(coverUrl);
    }).catch(err => { if (!cancelled) setError(`Could not load video cover: ${message(err)}`); });
    return () => {
      cancelled = true; controller.abort();
      if (url) URL.revokeObjectURL(url);
      if (coverUrl) URL.revokeObjectURL(coverUrl);
    };
  }, [job?.id, job?.state, active]);

  const selectDraft = async (id?: string) => {
    try {
      await persist();
      const selected = drafts.find(d => d.id === id);
      draftRef.current = selected ? { id: selected.id, revision: selected.revision } : { id: newId(), revision: 0 };
      savedHash.current = selected ? draftFingerprint(selected.composition, selected.editorial) : '';
      const next = selected?.composition || { ...emptyComposition(), ...brand };
      compositionRef.current = next;
      setComposition(next); setSelectedJobId(null); setView('poster'); setError('');
      const nextEditorial = selected?.editorial || emptyEditorial();
      editorialRef.current = nextEditorial;
      setEditorial(nextEditorial);
      setSelectedPhoto(0);
      setPosterUrl(''); setPosterHash(''); setPosterError(''); setWarnings([]);
      localStorage.setItem('reel_creator_draft', draftRef.current.id);
    } catch (err) { setError(message(err)); }
  };

  const importPhotos = async (files?: File[], replace?: number) => {
    if (uploading) return;
    const before = compositionRef.current;
    const oldPhotos = reelPhotos(before);
    const limit = replace === undefined ? 5 - oldPhotos.length : 1;
    if (limit < 1) return;
    setUploading('image');
    try {
      if (files && files.length > limit) throw new Error(`Choose up to ${limit} photos for this action.`);
      if (files?.some(file => file.type.startsWith('video/') || /\.(mp4|mov|webm)$/i.test(file.name))) throw new Error('This slideshow uses photos. Add existing videos in Content Studio under Daily Batch Creator.');
      let result;
      if (!files) result = await reelApi.pickImages(limit);
      else {
        result = { assets: [] as ReelAsset[], errors: [] as string[] };
        for (const file of files) {
          try { result.assets.push(await reelApi.upload('image', file)); }
          catch (err) { result.errors.push(`${file.name}: ${message(err)}`); }
        }
      }
      if (!result) return;
      if (result.assets.length) {
        setAssetNames(old => ({ ...old, ...Object.fromEntries(result.assets.map(asset => [asset.id, asset.name])) }));
        if ((compositionRef.current.aspect_ratio ?? '9:16') !== '9:16') {
          for (const asset of result.assets) {
            asset.suggested_framing = await reelApi.autoFraming(asset.id, null, compositionRef.current.aspect_ratio);
          }
        }
        const added = result.assets.map(asset => ({ image_id: asset.id, movement: 'auto' as const, focal_x: .5, focal_y: .5, ...asset.suggested_framing?.settings }));
        const currentPhotos = reelPhotos(compositionRef.current);
        const nextPhotos = replace === undefined ? [...currentPhotos, ...added] : currentPhotos.map((photo, i) => i === replace ? {...added[0], headline: photo.headline, duration_seconds: photo.duration_seconds} : photo);
        const update = storyUpdate(compositionRef.current, nextPhotos);
        patch({ ...update, ...(before.version === 1 ? { motion: true } : {}) });
        setSelectedPhoto(replace ?? oldPhotos.length);
        setView('poster'); setPosterUrl(''); setPosterHash('');
        const notes = [];
        if (result.assets.some(asset => asset.suggested_framing?.status === 'detected')) notes.push('Faces detected: suggested framing applied with photo motion off. Review the preview or use Adjust framing.');
        if (result.assets.some(asset => asset.suggested_framing?.status === 'unavailable')) notes.push('Face detection unavailable: whole-photo framing applied.');
        if (update.duration !== before.duration) notes.push(`Duration changed to ${update.duration} seconds so each photo has time on screen.`);
        if (notes.length) setNotice(notes.join(' '));
      }
      setError(result.errors.join(' '));
    } catch (err) { setError(message(err)); }
    finally { setUploading(null); }
  };

  const upload = async (kind: ReelAsset['kind'], file?: File) => {
    if (!file) return;
    setUploading(kind); setError('');
    try {
      const asset = await reelApi.upload(kind, file);
      setAssetNames(old => ({ ...old, [asset.id]: asset.name }));
      patch({ [kind === 'image' ? 'image_id' : kind === 'logo' ? 'logo_id' : 'audio_id']: asset.id, ...(kind === 'audio' ? { audio_preset: 'none' as const } : {}) });
      if (kind === 'image') { setView('poster'); setPosterUrl(''); setPosterHash(''); setSelectedJobId(null); }
    } catch (err) { setError(message(err)); }
    finally { setUploading(null); }
  };

  const pickAudio = async () => {
    if (uploading) return;
    setUploading('audio');
    try {
      const asset = await reelApi.pickAudio();
      if (!asset) return; // Closing the picker changes no media, preset, or draft fields.
      setError('');
      setAssetNames(old => ({ ...old, [asset.id]: asset.name }));
      patch({ audio_id: asset.id, audio_preset: 'none' });
    } catch (err) { setError(message(err)); }
    finally { setUploading(null); }
  };

  const render = async (kind: ReelJob['kind']) => {
    const snapshot = { ...compositionRef.current };
    const postCaption = editorialRef.current.post_caption;
    const key = kind + fingerprint(snapshot) + postCaption;
    const requestId = requestIds.current.get(key) || crypto.randomUUID();
    requestIds.current.set(key, requestId);
    setSubmitting(true); setError(''); setNotice('');
    try {
      await persist();
      const result = await reelApi.render(snapshot, kind, requestId, postCaption);
      requestIds.current.delete(key);
      setJobs(old => [result, ...old.filter(j => j.id !== result.id)]);
      setSelectedJobId(result.id); setView('video');
    } catch (err) { setError(message(err)); }
    finally { setSubmitting(false); }
  };

  const updateJob = async (action: 'cancel' | 'retry', id: string) => {
    try {
      const result = await reelApi[action](id);
      setJobs(old => old.map(j => j.id === id ? result : j));
      setSelectedJobId(id); setView('video'); setError('');
    } catch (err) { setError(message(err)); }
  };

  const exportVideo = async () => {
    if (!job?.output) return;
    setExporting(true); setError('');
    try { setNotice(await reelApi.export(job.id, job.output.filename)); }
    catch (err) { setError(message(err)); }
    finally { setExporting(false); }
  };

  const saveBrand = async () => {
    try {
      const result = await reelApi.saveBrand(composition);
      setBrand(result); setNotice('Brand preset saved for your next reel.');
    } catch (err) { setError(message(err)); }
  };

  const draftOptions = drafts.map(d => ({ value: d.id, label: d.composition.headline.replace(/\n/g, ' ') || assetNames[d.composition.image_id || ''] || 'Untitled reel' }));
  if (!draftOptions.some(d => d.value === draftRef.current.id)) draftOptions.unshift({ value: draftRef.current.id, label: 'New reel' });

  return <div ref={scrollSurfaceRef} className="h-full min-h-0 w-full overflow-hidden"><div className="reel-creator-shell mx-auto flex w-full max-w-6xl flex-col gap-3 px-5 py-4 lg:px-7">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2.5"><div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-2"><Clapperboard className="h-4 w-4 text-blue-400" /></div>
        <h1 className="text-base font-semibold text-zinc-100">Reel Creator</h1></div>
      <div className="flex min-w-0 max-w-full items-center gap-2">
        <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-[10px] text-zinc-500" role="status">{saveState === 'Saved' && <Check className="h-3 w-3 text-emerald-400" />}{saveState}</span>
        <div className="w-44 min-w-0 sm:w-56"><Select value={draftRef.current.id} options={draftOptions} onValueChange={id => void selectDraft(id)} ariaLabel="Saved reel drafts" className="h-9 min-h-9 [&>span:first-child]:min-w-0 [&>span:first-child]:truncate" contentClassName="max-w-sm [&_[role=option]]:break-words" disabled={!loaded || !!uploading || saveState === 'Saving…'} /></div>
        <Button className="h-9 shrink-0 px-3" variant="outline" disabled={!loaded || !!uploading || saveState === 'Saving…'} onClick={() => void selectDraft()}><Plus className="h-3.5 w-3.5" />New</Button></div>
    </div>
    {error && <div role="alert" className="flex items-start justify-between gap-3 rounded-xl border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-200"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X className="h-4 w-4" /></button></div>}
    {notice && <div role="status" className="flex items-start justify-between gap-3 rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-3 text-xs text-emerald-200"><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice('')}><X className="h-4 w-4" /></button></div>}
    {capabilities && !capabilities.ready && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-xs text-amber-200"><div><p className="font-semibold">A little setup before your first export</p><p className="mt-1">Missing: {capabilities.missing.join(', ')}. {capabilities.repair}</p></div><Button variant="outline" onClick={() => void reelApi.capabilities(true).then(setCapabilities).catch(err => setError(message(err)))}><RefreshCw className="h-3.5 w-3.5" />Check again</Button></div>}
    {!loaded ? <div className="py-20 text-center text-sm text-zinc-500"><Button variant="outline" onClick={() => void initialize()}><RefreshCw className="h-4 w-4" />Reload creator</Button></div> :
      <div ref={gridRef} className="reel-creator-grid grid items-start gap-6">
        <div className="reel-creator-editor flex min-h-0 flex-col gap-3">
          <div role="tablist" aria-label="Reel editing sections" className="reel-editor-tabs sticky top-0 z-10 grid shrink-0 grid-cols-3 gap-1 rounded-xl border border-zinc-800 bg-zinc-950 p-1 shadow-lg">
            {editorTabs.map((tab, index) => <button key={tab.id} ref={element => { tabRefs.current[index] = element; }}
              type="button" role="tab" id={`${tabId}-tab-${tab.id}`} aria-controls={`${tabId}-panel-${tab.id}`}
              aria-selected={editorTab === tab.id} tabIndex={editorTab === tab.id ? 0 : -1}
              onClick={() => selectEditorTab(tab.id)} onKeyDown={event => {
                let next = index;
                if (event.key === 'ArrowRight') next = (index + 1) % editorTabs.length;
                else if (event.key === 'ArrowLeft') next = (index + editorTabs.length - 1) % editorTabs.length;
                else if (event.key === 'Home') next = 0;
                else if (event.key === 'End') next = editorTabs.length - 1;
                else return;
                event.preventDefault();
                selectEditorTab(editorTabs[next].id);
                tabRefs.current[next]?.focus();
              }} className={`rounded-lg px-3 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 ${editorTab === tab.id ? 'bg-blue-500/15 text-blue-300 ring-1 ring-blue-500/25' : 'text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200'}`}>
              {tab.label}
            </button>)}
          </div>
          {editorTab!=='story' && contentCheck.fit && !contentCheck.fit.fits && <div role="status" className="rounded-xl border border-amber-500/25 bg-amber-950/20 px-4 py-3 text-xs text-amber-200">Content needs adjustment for the current settings. <button className="ml-2 underline" onClick={()=>selectEditorTab('story')}>Review / adapt content</button></div>}
          <div ref={editorRef} className="reel-creator-editor-content space-y-5">
          {/* Keep panels mounted so switching tabs preserves local state and pending work. */}
          <div role="tabpanel" id={`${tabId}-panel-story`} aria-labelledby={`${tabId}-tab-story`} hidden={editorTab !== 'story'} tabIndex={0} className="space-y-5">
          <NewsAssistant key={'news-' + draftRef.current.id} value={editorial} composition={composition} contentCheck={contentCheck} onOpenSettings={() => selectEditorTab('style')} categoryHint={composition.category}
            onChange={setEditorial} onApply={value => { patch(value); setView('poster'); }} />
          <ReelTextEditor value={composition} onChange={patch} />
          <NewsNarration key={'narration-' + draftRef.current.id} value={composition} editorial={editorial} onChange={patch} />
          </div>
          <div role="tabpanel" id={`${tabId}-panel-media`} aria-labelledby={`${tabId}-tab-media`} hidden={editorTab !== 'media'} tabIndex={0} className="space-y-5">
          <PhotoStoryEditor key={'photos-' + draftRef.current.id} value={composition} names={assetNames} selected={Math.min(selectedPhoto, Math.max(0, photos.length - 1))}
            onSelect={setSelectedPhoto} onChange={patch} onImport={(files, replace) => void importPhotos(files, replace)} uploading={!!uploading} rendering={anyBusy} framingEnabled={!!capabilities?.photo_framing} />
          <NewsAudio value={composition} onChange={patch} onUpload={file => void upload('audio', file)} onPick={() => void pickAudio()} uploading={uploading === 'audio'} customName={assetNames[composition.audio_id || ''] || ''} />

          </div>
          <div role="tabpanel" id={`${tabId}-panel-style`} aria-labelledby={`${tabId}-tab-style`} hidden={editorTab !== 'style'} tabIndex={0} className="space-y-5">
          <ReelStyleEditor value={composition} onChange={patch} />
          <details className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5"><summary className="cursor-pointer text-xs font-semibold text-zinc-300">News branding <span className="ml-2 font-normal text-zinc-600">Saved for new reels</span></summary>
            <div className="mt-4 space-y-4"><p className="text-[11px] leading-relaxed text-zinc-500">A consistent logo, masthead and accent color across every photo. Save once to reuse on new reels.</p><div className="grid grid-cols-[1fr_72px] gap-3"><label className="space-y-1.5 text-xs text-zinc-400">Brand name<input className={inputClass} value={composition.brand} maxLength={48} placeholder="Your page name" onChange={e => patch({ brand: e.target.value })} /></label><label className="space-y-1.5 text-xs text-zinc-400">Accent<input type="color" className="h-10 w-full rounded-lg border border-zinc-800 bg-zinc-950 p-1" value={composition.accent} onChange={e => patch({ accent: e.target.value })} /></label></div>
              <div className="flex items-center gap-2"><label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-3 text-xs text-zinc-400"><ImageIcon className="h-4 w-4 shrink-0" /><span className="truncate">{uploading === 'logo' ? 'Importing…' : composition.logo_id ? assetNames[composition.logo_id] || 'Logo selected' : 'Add a PNG logo · optional'}</span><input type="file" className="sr-only" accept="image/png" aria-label="Upload logo" disabled={!!uploading} onChange={e => { void upload('logo', e.target.files?.[0]); e.target.value = ''; }} /></label>{composition.logo_id && <Button variant="ghost" size="icon" aria-label="Remove logo" onClick={() => patch({ logo_id: null })}><X className="h-4 w-4" /></Button>}</div>
              <div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => void saveBrand()}>Save brand preset</Button>{brand && <Button variant="ghost" size="sm" onClick={() => patch(brand)}>Apply saved brand</Button>}</div>
            </div>
          </details>

          <details className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5"><summary className="cursor-pointer text-xs font-semibold text-zinc-300">Text & layout</summary><div className="mt-4">
            <div className="mb-4 flex items-center gap-2"><h2 className="text-xs font-semibold text-zinc-200">Choose your news style</h2></div>
            <div className="mb-5 space-y-2"><p className="text-[11px] text-zinc-500">Quick news presets <span className="text-zinc-600">· sets style, category and accent</span></p><div className="flex flex-wrap gap-2">{newsPresets.map(preset => {
              const selected = composition.category === preset.category && composition.template === preset.template && composition.accent === preset.accent;
              return <button key={preset.name} type="button" aria-pressed={selected} onClick={() => patch({ category: preset.category, template: preset.template, accent: preset.accent })} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[11px] font-medium transition ${selected ? 'border-zinc-500 bg-zinc-800 text-white' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200'}`}><span className="h-1.5 w-1.5 rounded-full" style={{ background: preset.accent }} />{preset.name}</button>;
            })}</div></div>
            {composition.template === 'lens_report' && <p className="mb-3 text-xs text-zinc-400">Lens Report supports 9:16 and 4:5. Headlines reveal line by line, followed by captions. Edit each photo’s title and caption in Media. Fit preserves the whole image over a soft background; Fill allows gentle motion. Source attribution is required.</p>}
            {capabilities && !capabilities.lens_report && <div className="mb-3 flex items-center gap-3"><p className="text-xs text-zinc-500">Lens Report unavailable: {capabilities.lens_report_reason || 'Restart the updated backend.'}</p><Button variant="outline" size="sm" onClick={() => void reelApi.capabilities(true).then(setCapabilities).catch(err => setError(message(err)))}>Check again</Button></div>}
            {composition.template === 'daily_brief' && <p className="text-xs text-zinc-400">Daily Brief uses a vertical photo card and short text. Add source attribution below. Photo headlines become scene titles; photo framing is preserved. Scenes use cuts and text fades. Slideshow movement and text entrance overrides apply to the other layouts.</p>}
            {capabilities && !capabilities.daily_brief && <div className="flex items-center gap-3"><p className="text-xs text-zinc-500">Daily Brief unavailable: {capabilities.daily_brief_reason || 'Update the local backend runtime.'}</p><Button variant="outline" size="sm" onClick={() => void reelApi.capabilities(true).then(setCapabilities).catch(err => setError(message(err)))}>Check again</Button></div>}
            <div className="grid grid-cols-2 gap-2.5">{templates.map(template => <button key={template.id} type="button" aria-pressed={composition.template === template.id} disabled={(template.id === 'daily_brief' && !capabilities?.daily_brief) || (template.id === 'lens_report' && (!capabilities?.lens_report || capabilities?.lens_report_revision !== 2))} title={template.id === 'daily_brief' ? capabilities?.daily_brief_reason : template.id === 'lens_report' ? capabilities?.lens_report_reason : undefined} onClick={() => patch(template.id === 'lens_report' ? {...storyUpdate({...composition, template:'lens_report', layout_revision:2, auto_timing:true, slide_seconds:null}), template:'lens_report', layout_revision:2, photo_layout:'full_bleed', motion_intensity:'balanced', text_animation:'auto', motion:true, accent:'#38bdf8', auto_timing:true, slide_seconds:null, slideshow_effect:composition.slideshow_effect==='fade'?'auto':composition.slideshow_effect, aspect_ratio: composition.aspect_ratio === '4:5' ? '4:5' : '9:16', source:composition.source || editorial.publisher || '', transition:'fade'} : template.id === 'daily_brief' ? { ...storyUpdate(composition), template: template.id, photo_layout:'full_bleed',motion_intensity:'balanced',text_animation:'auto',motion:true,lens_theme:'dark',aspect_ratio: composition.aspect_ratio==='4:5'?'4:5':'9:16', source: composition.source || editorial.publisher || '' } : { ...storyUpdate(composition), template: template.id, accent: template.color })} className={`overflow-hidden rounded-xl border text-left transition disabled:cursor-not-allowed disabled:opacity-40 ${composition.template === template.id ? 'border-blue-500/80 ring-2 ring-blue-500/30' : 'border-zinc-800 hover:border-zinc-600'}`}>
              <div className="relative h-24 overflow-hidden bg-gradient-to-br from-slate-900 via-zinc-900 to-black p-2.5">
                {template.kind === 'bbc' && <>
                  <div className="flex justify-between items-center"><div className="h-2 w-7 rounded-sm bg-red-600 flex items-center justify-center text-[7px] font-bold text-white leading-none">BBC</div><div className="h-1.5 w-1.5 rounded-full bg-red-500" /></div>
                  <div className="absolute inset-x-2 bottom-2 space-y-1">
                    <div className="h-2 w-14 rounded-sm bg-red-600 px-1 py-0.5 flex items-center"><div className="h-1 w-9 bg-white/90 rounded-sm" /></div>
                    <div className="rounded-sm bg-black/90 p-1 border-l-2 border-red-500"><div className="h-1.5 w-4/5 rounded bg-white" /><div className="mt-0.5 h-1 w-3/5 rounded bg-white/60" /></div>
                  </div>
                </>}
                {template.kind === 'economist' && <>
                  <div className="flex items-center gap-1.5"><div className="h-2.5 w-9 bg-red-600 flex items-center justify-center rounded-sm"><div className="h-1 w-7 bg-white rounded-sm" /></div><div className="h-[1px] flex-1 bg-white/20" /></div>
                  <div className="absolute inset-x-2.5 bottom-2 space-y-1 font-serif">
                    <div className="h-1 w-8 bg-red-500/80 rounded-sm" />
                    <div className="h-2 w-5/6 rounded-sm bg-amber-100/90 italic" />
                    <div className="h-1.5 w-4/6 rounded-sm bg-amber-100/60" />
                  </div>
                </>}
                {template.kind === 'reuters' && <>
                  <div className="flex items-center justify-between"><div className="flex items-center gap-1"><div className="h-1.5 w-1.5 rounded-full bg-orange-500" /><div className="h-1 w-10 bg-white/80 rounded-sm" /></div><div className="h-1.5 w-8 rounded-sm bg-orange-500/20 border border-orange-500/40" /></div>
                  <div className="absolute inset-x-2 bottom-2 space-y-1 border-l-2 border-orange-500 pl-1.5">
                    <div className="h-1.5 w-4/5 rounded bg-white/90" />
                    <div className="flex items-center gap-1"><div className="h-1 w-1 rounded-full bg-orange-400" /><div className="h-1 w-3/4 rounded bg-zinc-300" /></div>
                    <div className="flex items-center gap-1"><div className="h-1 w-1 rounded-full bg-orange-400" /><div className="h-1 w-1/2 rounded bg-zinc-400" /></div>
                  </div>
                </>}
                {template.kind === 'ap_wire' && <>
                  <div className="h-2 w-full bg-red-600 rounded-sm flex items-center px-1"><div className="h-1 w-12 bg-yellow-300 rounded-sm" /></div>
                  <div className="absolute inset-x-2 bottom-2 space-y-1 bg-zinc-950/90 p-1 rounded-sm border-t border-red-500/60">
                    <div className="h-2 w-full rounded bg-white font-bold" />
                    <div className="h-1.5 w-3/5 rounded bg-yellow-300/80" />
                  </div>
                </>}
                {template.kind === 'lens_report' && <>
                  <div className="absolute inset-0 bg-gradient-to-b from-sky-950/40 via-zinc-900/60 to-black/90 p-2">
                    <div className="h-1 w-12 bg-sky-400/80 rounded-sm" />
                    <div className="absolute inset-x-2 bottom-2 space-y-1"><div className="h-1.5 w-4/5 rounded bg-white/90" /><div className="h-1 w-3/5 rounded bg-zinc-300/60" /></div>
                  </div>
                </>}
                {template.kind === 'daily_brief' && <>
                  <div className="mx-auto mt-1 h-11 w-4/5 rounded-md border border-emerald-500/30 bg-emerald-950/30" />
                  <div className="absolute inset-x-2 bottom-2 space-y-1">
                    <div className="h-1.5 w-3/4 rounded bg-white" />
                    <div className="h-0.5 w-full bg-emerald-500 rounded-full" />
                  </div>
                </>}
                {composition.template === template.id && <Check className="absolute right-2 top-2 h-4 w-4 text-blue-400 bg-zinc-950/80 rounded-full p-0.5" />}
              </div>
              <div className="p-3">
                <div className="flex items-center justify-between gap-1">
                  <p className="text-[11px] font-semibold text-zinc-100">{template.name}</p>
                  <span className="rounded bg-zinc-800/80 px-1.5 py-0.5 text-[9px] font-medium text-zinc-400">{template.badge}</span>
                </div>
                <p className="mt-1 text-[10px] leading-relaxed text-zinc-400">{template.description}</p>
              </div></button>)}</div>
            <div className="mt-5 space-y-4">
              <div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs text-zinc-400">Category<input className={inputClass} value={composition.category} maxLength={32} placeholder="e.g. LOCAL NEWS" onChange={e => patch({ category: e.target.value })} /></label><label className="space-y-1.5 text-xs text-zinc-400">Source / date<input className={inputClass} value={composition.source} maxLength={90} placeholder={['daily_brief', 'lens_report'].includes(composition.template) ? 'Required attribution · add date if known' : 'Optional attribution'} onChange={e => patch({ source: e.target.value })} /></label></div>
            </div>
            </div>
          </details>
          </div>
          </div>
        </div>

        <aside ref={previewScrollRef} className="reel-creator-preview space-y-4">
          <div className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/40">
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3"><span className="text-[10px] font-semibold tracking-widest text-zinc-500">{displayed.aspect_ratio ?? '9:16'} REEL</span><div className="flex rounded-lg bg-zinc-950 p-0.5">{(['poster', 'video'] as const).map(item => <button key={item} className={`rounded-md px-2.5 py-1 text-[10px] ${view === item ? 'bg-zinc-800 text-zinc-200' : 'text-zinc-600'}`} onClick={() => setView(item)}>{item === 'poster' ? 'Layout' : 'Video'}</button>)}</div></div>
            <div style={{aspectRatio:`${displayWidth}/${displayHeight}`}} className="relative mx-auto my-5 w-[calc(100%-40px)] max-w-[300px] overflow-hidden rounded-xl bg-zinc-950 shadow-2xl ring-1 ring-white/5">
              {view === 'video' && videoUrl ? <video src={videoUrl} poster={videoPosterUrl || undefined} controls playsInline className="h-full w-full object-contain" /> : posterUrl ? <img src={posterUrl} className="h-full w-full object-contain" alt="Rendered news reel layout" /> : <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center"><Film className="h-10 w-10 text-zinc-800" /><p className="text-sm font-medium text-zinc-500">Your story starts here</p><p className="text-xs leading-relaxed text-zinc-700">Choose an image to see your news reel take shape.</p></div>}
              {showGuide && view === 'poster' && <div aria-label="Approximate safe-area guide, not included in export" className="pointer-events-none absolute left-[6%] right-[17%] top-[10%] rounded border border-dashed border-white/50" style={{ bottom: `${guideBottom}%` }}><span className="absolute -top-4 left-0 bg-black/70 px-1 text-[8px] tracking-wide text-white/70">GUIDE ONLY</span></div>}
              {(previewing || (view === 'video' && jobBusy)) && <div role="status" className="absolute inset-x-3 bottom-3 flex items-center gap-2 rounded-lg border border-white/10 bg-zinc-950/90 px-3 py-2 text-[11px] text-zinc-300"><Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400" />{previewing ? 'Updating layout…' : job?.state === 'queued' ? 'Queued for rendering' : job?.state === 'verifying' ? 'Checking finished video…' : `Rendering · ${Math.round((job?.progress || 0) * 100)}%`}</div>}
            </div>
            <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3 text-[10px] text-zinc-500"><span>{view === 'video' && job?.kind === 'preview' ? `${previewSize[0]} × ${previewSize[1]} · motion preview` : formatLabel}</span><span>{view === 'video' && job ? job.composition.duration : composition.duration} seconds</span></div>
          </div>
          {posterError && <p role="alert" className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-xs leading-relaxed text-amber-200">{posterError}</p>}
          {posterUrl && posterHash !== layoutHash && !previewing && <p className="text-[11px] text-amber-300">Layout preview shows earlier settings.</p>}
          {warnings.map(warning => <p key={warning} className="text-[11px] leading-relaxed text-amber-300">{warning}</p>)}
          <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4"><div className="space-y-1"><p className="text-xs font-medium text-zinc-200">{composition.narration_enabled && !composition.narration_id ? 'Prepare narration first' : 'Ready when you are'}</p><p className="text-[11px] text-zinc-500">{composition.duration} seconds · {composition.aspect_ratio ?? '9:16'} · {composition.resolution}px wide · {composition.fps} fps{composition.auto_timing ? ' · Auto timing' : composition.slide_seconds ? ` · ${composition.slide_seconds}s per photo` : ''}</p></div>
            {composition.narration_enabled && !composition.narration_id && <p className="mt-2 text-[11px] text-amber-300">Use Preview voice in Narration, or turn English voice off.</p>}
            <div className="mt-4 grid grid-cols-2 gap-2"><Button variant="outline" disabled={!composition.image_id || !capabilities?.ready || submitting || anyBusy || !!uploading || (composition.narration_enabled && !composition.narration_id)} onClick={() => void render('preview')}>Preview motion</Button><Button disabled={!composition.image_id || !capabilities?.ready || submitting || anyBusy || !!uploading || (composition.narration_enabled && !composition.narration_id)} loading={submitting} onClick={() => void render('final')}><Clapperboard className="h-4 w-4" />Render video</Button></div>
            <p className="mt-3 text-[10px] leading-relaxed text-zinc-600">Rendered locally. Preview motion uses lower resolution. Posting remains a separate step.</p>
          </div>

            <section className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4" aria-label="Timing and export settings"><h2 className="text-xs font-semibold text-zinc-200">Timing & export</h2>
              <div className="grid grid-cols-2 gap-3">
                <label className="space-y-2 text-xs text-zinc-400">Each photo<Select ariaLabel="Seconds per photo" value={composition.auto_timing ? 'auto' : String(composition.slide_seconds ?? 'fit')} options={[{value:'auto',label:'Auto · headline length'},{value:'fit',label:'Fit video · saved timing'}, ...[3,5,7,10].map(n=>({value:String(n),label:`${n} seconds${n===5?' · default':''}`}))]} onValueChange={value=>{const next={...composition,auto_timing:value==='auto',slide_seconds:value==='fit'||value==='auto'?null:Number(value) as ReelComposition['slide_seconds']};patch({...storyUpdate(next),auto_timing:next.auto_timing,slide_seconds:next.slide_seconds});}} /></label>
                <label className="space-y-2 text-xs text-zinc-400">Video length<Select ariaLabel="Reel duration" disabled={!!composition.auto_timing} value={String(composition.duration)} options={composition.auto_timing ? [{value:String(composition.duration),label:`${composition.duration} seconds · Auto`}] : reelDurations.filter(n=>n>=minimumDuration(composition)).map(n=>({value:String(n),label:`${n} seconds`}))} onValueChange={value=>patch({duration:Number(value) as ReelComposition['duration']})}/></label>
                <label className="col-span-2 space-y-2 text-xs text-zinc-400">Aspect ratio<Select ariaLabel="Video aspect ratio" value={composition.aspect_ratio ?? '9:16'} disabled={!capabilities?.aspect_ratios} options={[{value:'9:16',label:'9:16 · Vertical reel'},{value:'4:5',label:'4:5 · Portrait feed'},...(['lens_report','daily_brief'].includes(composition.template) ? [] : [{value:'1:1',label:'1:1 · Square feed'}])]} onValueChange={value=>{patch(switchReelRatio(composition,value as NonNullable<ReelComposition['aspect_ratio']>));setView('poster');}}/>{!capabilities?.aspect_ratios&&<span className="block text-[11px] text-amber-300">Restart the local backend to enable more ratios.</span>}</label>
                <label className="space-y-2 text-xs text-zinc-400">Resolution<Select ariaLabel="Export resolution" value={String(composition.resolution)} options={[{value:'1080',label:'1080 px wide · recommended'},{value:'720',label:'720 px wide · smaller file'}]} onValueChange={value=>patch({resolution:Number(value) as ReelComposition['resolution']})}/></label>
                <label className="space-y-2 text-xs text-zinc-400">Frame rate<Select ariaLabel="Export frame rate" value={String(composition.fps)} options={[24,30,60].map(n=>({value:String(n),label:`${n} fps${n===30?' · recommended':''}`}))} onValueChange={value=>patch({fps:Number(value) as ReelComposition['fps']})}/></label>
                <label className="space-y-2 text-xs text-zinc-400">Transitions<Select ariaLabel="Photo transition" value={composition.transition} options={[{value:'fade',label:'Crossfade'},{value:'cut',label:'Simple cut'}]} onValueChange={value=>patch({transition:value as ReelComposition['transition']})}/></label>
              </div>
              <label className="flex items-center gap-2 text-xs text-zinc-400"><Checkbox checked={composition.motion} onCheckedChange={motion=>patch({motion})} ariaLabel="Animate photos and text"/>Animate photos and text</label>
              <p className="text-[11px] leading-relaxed text-zinc-500">{composition.auto_timing ? 'Each photo plays once. Duration follows headline length or prepared narration.' : 'Photos repeat until the video ends. Frame rate also applies to motion previews.'}</p>
              <label className="flex items-center gap-2 text-[11px] text-zinc-500"><Checkbox checked={showGuide} onCheckedChange={setShowGuide} ariaLabel="Show approximate safe-area guide"/>Show safe-area guide in preview</label>
              {showGuide&&<label className="block text-[11px] text-zinc-500">Bottom guide · {guideBottom}%<input type="range" className="mt-2 w-full accent-blue-500" min="10" max="30" value={guideBottom} onChange={e=>setGuideBottom(Number(e.target.value))}/></label>}
            </section>

          {job && <section className="space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4" aria-label="Render status"><div className="flex items-center justify-between"><p className="text-xs font-semibold text-zinc-200">{job.kind === 'preview' ? 'Motion preview' : 'Full-quality video'}</p><span className={`text-[10px] capitalize ${job.state === 'completed' ? 'text-emerald-400' : 'text-zinc-500'}`}>{job.state}</span></div>
            {jobBusy && <><progress aria-label="Video render progress" max="1" value={job.progress} className="h-1 w-full accent-blue-500" /><p className="text-[11px] text-zinc-400">{job.reason || (job.state === 'verifying' ? 'Checking format, duration, and playback.' : 'You can keep editing while this runs.')}</p><Button variant="outline" size="sm" onClick={() => void updateJob('cancel', job.id)}>Cancel render</Button></>}
            {job.error && <p role="alert" className="text-xs leading-relaxed text-amber-200">{job.error}</p>}
            {['failed', 'interrupted', 'cancelled'].includes(job.state) && <Button variant="outline" size="sm" disabled={anyBusy} onClick={() => void updateJob('retry', job.id)}><RefreshCw className="h-3 w-3" />Retry saved settings</Button>}
            {stale && <p className="text-[11px] leading-relaxed text-amber-300">This video uses earlier settings. Render again to include your latest edits.</p>}
            {job.state === 'completed' && job.kind === 'final' && job.output && <><Button variant="outline" className="w-full" loading={exporting} onClick={() => void exportVideo()}><Download className="h-4 w-4" />Export MP4</Button><Button className="w-full" onClick={() => onUseReel({ jobId: job.id, filename: job.output!.filename, name: job.composition.headline.replace(/\n/g, ' ').slice(0, 80) || 'Created news reel', caption: stale ? job.post_caption || '' : editorial.post_caption })}>Add to Reel post<ArrowRight className="h-4 w-4" /></Button></>}
          </section>}
          {jobs.filter(j => j.composition.image_id === composition.image_id).length > 1 && <label className="block space-y-2 text-xs text-zinc-500">Recent renders<Select ariaLabel="Recent renders" value={job?.id || ''} options={jobs.filter(j => j.composition.image_id === composition.image_id).map(j => ({ value: j.id, label: `${j.kind === 'preview' ? 'Preview' : 'MP4'} · ${new Date(j.created_at * 1000).toLocaleTimeString()} · ${j.state}` }))} onValueChange={id => { setSelectedJobId(id); setView('video'); }} /></label>}
        </aside>
      </div>}
  </div></div>;
}
