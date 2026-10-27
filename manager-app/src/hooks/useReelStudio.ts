import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { reelApi, type WriteRequest } from '../services/reelCreator';
import { configuredGemini } from '../services/newsSource';
import { addScene, KIND_NAMES, rewrittenWords, sceneWords, updatedStory, applyHouseStyle, applyWriting, houseStyleOf, datelineFor, liveVersion, photoVersion, emptyReel, fillPhotos, fillScenes, hasContent, headlines, insertIndex, MAX_SCENES, moveScene, placePhotos, reelKey, removeScene, setPhoto, sourceKind, withHook } from '../services/reelDocument';
import type { CreatedReelMedia, HouseStyle, Reel, ReelGoal, ReelAsset, ReelBrand, ReelCapabilities, ReelDraft, ReelJob, ReelRatio, ReelScene } from '../types/reelCreator';

interface Doc { reel: Reel; caption: string; goal: ReelGoal; comment: string; hooks: string[]; sourceText: string; sourceUrl: string; sourceTitle: string; sourcePublisher: string }
/** An article as read once: pasted text, or the text extracted from a link. */
interface Article { text: string; url: string; title: string; publisher: string; published: string }
type Busy = null | 'write' | 'hooks' | 'caption' | 'photos' | 'scene' | 'rewrite' | 'logo' | 'music' | 'render' | 'photo';
/** What the reel is used as: a still of one scene for a photo post, the Reel video, or the video for Live. */
export type ReelOutput = 'photo' | 'reel' | 'live';
type Tracked = { jobId: string; key: string };
/** The latest video render for the Reel and for Live; each is only usable while it matches the edits. */
type Renders = Partial<Record<'reel' | 'live', Tracked>>;

const ACTIVE = ['queued', 'rendering', 'verifying'];
const message = (err: unknown) => err instanceof Error ? err.message : 'Something went wrong.';
const newId = () => crypto.randomUUID().replace(/-/g, '');
const savedGoal = (): ReelGoal => { try { return localStorage.getItem('reel_creator_goal') === 'readmore' ? 'readmore' : 'engage'; } catch { return 'engage'; } };
const blank = (brand?: ReelBrand | null): Doc => ({ reel: emptyReel(brand), caption: '', goal: savedGoal(), comment: '', hooks: [], sourceText: '', sourceUrl: '', sourceTitle: '', sourcePublisher: '' });
const snapshot = (doc: Doc) => ({ reel: doc.reel, caption: doc.caption, goal: doc.goal, comment: doc.comment, hooks: doc.hooks, source_text: doc.sourceText, source_url: doc.sourceUrl,
  source_title: doc.sourceTitle, source_publisher: doc.sourcePublisher });
const savedRenders = (id: string): Renders => {
  try {
    const value = JSON.parse(localStorage.getItem(`reel_creator_render:${id}`) || 'null');
    return value?.jobId ? { reel: value } : value ?? {};   // older drafts tracked the Reel render alone
  } catch { return {}; }
};
const worthSaving = (doc: Doc) => hasContent(doc.reel) || doc.reel.scenes.some(s => s.image_id) || !!doc.sourceText;

export function useReelStudio(active: boolean, onUseReel: (media: CreatedReelMedia) => void) {
  const [capabilities, setCapabilities] = useState<ReelCapabilities | null>(null);
  const [drafts, setDrafts] = useState<ReelDraft[]>([]);
  const [jobs, setJobs] = useState<ReelJob[]>([]);
  const [houseStyles, setHouseStyles] = useState<HouseStyle[]>([]);
  const [doc, setDocState] = useState<Doc>(() => blank());
  const [draftId, setDraftId] = useState(newId);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  /** The scene words a rewrite replaced, while its notice offers Undo. */
  const [undo, setUndo] = useState<{ index: number; words: Partial<ReelScene> } | null>(null);
  const [rewriting, setRewriting] = useState<number | null>(null);
  const [saveState, setSaveState] = useState('');
  const [renders, setRenders] = useState<Renders>({});
  const [output, setOutput] = useState<ReelOutput>('reel');
  const [photoRatio, setPhotoRatio] = useState<ReelRatio>('4:5');

  const docRef = useRef(doc);
  const draftRef = useRef({ id: draftId, revision: 0 });
  const brandRef = useRef<ReelBrand | null>(null);
  const savedHash = useRef('');
  const saveChain = useRef<Promise<unknown>>(Promise.resolve());
  const requestIds = useRef(new Map<string, string>());
  const brandTimer = useRef<number>(0);

  const setDoc = useCallback((change: (doc: Doc) => Doc) => {
    docRef.current = change(docRef.current);
    setDocState(docRef.current);
  }, []);
  const setReel = useCallback((change: (reel: Reel) => Reel) => setDoc(d => ({ ...d, reel: change(d.reel) })), [setDoc]);

  const persist = useCallback(() => {
    const run = saveChain.current.then(async () => {
      const current = docRef.current;
      if (!worthSaving(current)) return;
      const hash = JSON.stringify(snapshot(current));
      if (hash === savedHash.current) return;
      const saved = await reelApi.saveDraft(draftRef.current.id, draftRef.current.revision, snapshot(current));
      draftRef.current = { id: saved.id, revision: saved.revision };
      savedHash.current = hash;
      setDrafts(old => [saved, ...old.filter(d => d.id !== saved.id)]);
      setSaveState('Saved');
    });
    saveChain.current = run.catch(() => undefined);
    return run;
  }, []);

  const open = useCallback((draft: ReelDraft | null) => {
    const id = draft?.id ?? newId();
    draftRef.current = { id, revision: draft?.revision ?? 0 };
    const next: Doc = draft ? { reel: draft.reel, caption: draft.caption, goal: draft.goal ?? 'engage', comment: draft.comment ?? '', hooks: draft.hooks ?? [], sourceText: draft.source_text, sourceUrl: draft.source_url,
      sourceTitle: draft.source_title ?? '', sourcePublisher: draft.source_publisher ?? '' } : blank(brandRef.current);
    savedHash.current = draft ? JSON.stringify(snapshot(next)) : '';
    docRef.current = next;
    setDocState(next); setDraftId(id); setError(''); setNotice(''); setUndo(null); setSaveState(draft ? 'Saved' : '');
    try { localStorage.setItem('reel_creator_draft', id); } catch { /* A remembered draft is a convenience. */ }
    setRenders(savedRenders(id));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [caps, saved, renders, brand, styles] = await Promise.all([reelApi.capabilities(true), reelApi.drafts(), reelApi.jobs(), reelApi.brand(), reelApi.houseStyles()]);
        if (cancelled) return;
        brandRef.current = brand;
        setCapabilities(caps); setDrafts(saved); setJobs(renders); setHouseStyles(styles);
        const remembered = localStorage.getItem('reel_creator_draft');
        open(saved.find(d => d.id === remembered) ?? saved[0] ?? null);
        setLoaded(true);
      } catch (err) { if (!cancelled) setError(message(err)); }
    })();
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (!loaded || worthSaving(doc) === false) return;
    if (savedHash.current === JSON.stringify(snapshot(doc))) return;
    setSaveState('Saving…');
    const timer = window.setTimeout(() => { void persist().catch(err => { setSaveState(''); setError(message(err)); }); }, 650);
    return () => window.clearTimeout(timer);
  }, [doc, loaded, persist]);

  /** The reel as the chosen output shows it; the preview and its video follow this. */
  const shown = useMemo(() => output === 'live' ? liveVersion(doc.reel) : output === 'photo' ? photoVersion(doc.reel, photoRatio) : doc.reel,
    [output, doc.reel, photoRatio]);
  const render = output === 'photo' ? null : renders[output] ?? null;
  const job = useMemo(() => render ? jobs.find(j => j.id === render.jobId) ?? null : null, [jobs, render]);
  const busyJob = jobs.some(j => ACTIVE.includes(j.state) && (j.id === renders.reel?.jobId || j.id === renders.live?.jobId));
  useEffect(() => {
    if (!loaded || !active || !busyJob) return;
    let timer = 0, cancelled = false;
    const poll = async () => {
      try { const result = await reelApi.jobs(); if (!cancelled) setJobs(result); } catch (err) { if (!cancelled) setError(message(err)); }
      if (!cancelled) timer = window.setTimeout(poll, 1200);
    };
    timer = window.setTimeout(poll, 1200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [loaded, active, busyJob]);

  const track = (result: ReelJob, key: string, slot: 'reel' | 'live') => {
    setRenders(old => {
      const next = { ...old, [slot]: { jobId: result.id, key } };
      try { localStorage.setItem(`reel_creator_render:${draftRef.current.id}`, JSON.stringify(next)); } catch { /* Tracking falls back to this session. */ }
      return next;
    });
    setJobs(old => [result, ...old.filter(j => j.id !== result.id)]);
  };

  const attempt = async (kind: Busy, work: () => Promise<void>) => {
    if (busy) return;
    setBusy(kind); setError(''); setNotice(''); setUndo(null);
    try { await work(); } catch (err) { setError(message(err)); } finally { setBusy(null); }
  };

  /** A pasted article as is; a web address is fetched and its text extracted. */
  const readArticle = async (input: string): Promise<Article> => {
    if (sourceKind(input) !== 'url') return { text: input.trim(), url: '', title: '', publisher: '', published: '' };
    const article = await reelApi.article(input.trim());
    return { text: article.text, url: article.url, title: article.title, publisher: article.publisher, published: article.published_date };
  };

  /** One call to the AI writer; `source` is an article already read, or a pasted article or web address. */
  const requestWriting = async (source: Article | string, look: WriteRequest['look'], extra: Pick<WriteRequest, 'scenes' | 'existing' | 'caption' | 'rewrite' | 'position' | 'replacing'> = {}, goal: ReelGoal = docRef.current.goal) => {
    const gemini = configuredGemini();
    const article = typeof source === 'string' ? await readArticle(source) : source;
    if (article.text.length < 80) throw new Error('Paste the full article text, or a link to it.');
    const written = await reelApi.write({ ...gemini, text: article.text, source_url: article.url, source_title: article.title, publisher: article.publisher, look, goal, ...extra });
    return { written, article };
  };

  /** Write a reel from an article. With a house style, its look is the one written for and the style is applied. */
  const create = (source: string, look: WriteRequest['look'], photos: ReelAsset[], goal: ReelGoal, house?: HouseStyle) => attempt('write', async () => {
    const chosen = house?.look ?? look;
    const { written, article } = await requestWriting(source, chosen, { scenes: photos.length ? Math.min(photos.length, MAX_SCENES) : undefined }, goal);
    setDoc(d => {
      const reel = fillPhotos(applyWriting(d.reel, written, chosen !== 'auto'), photos);
      return { ...d, goal, comment: '', reel: { ...(house ? applyHouseStyle(reel, house) : reel), dateline: d.reel.dateline || datelineFor(article.published) },
      hooks: written.hooks.slice(0, 4), sourceText: article.text, sourceUrl: article.url, sourceTitle: article.title, sourcePublisher: article.publisher };
    });
  });

  /**
   * A copy of this reel rewritten from newer reporting on the same story, opened as a new draft; the reel already
   * made stays as it is. The caption and first comment are cleared, since they describe the earlier version.
   */
  const updateStory = (input: string, mark: boolean) => attempt('write', async () => {
    const base = docRef.current;
    const count = Math.min(MAX_SCENES, Math.max(1, base.reel.scenes.filter(scene => scene.kind !== 'end').length));
    const { written, article } = await requestWriting(input, base.reel.look, { scenes: count }, base.goal);
    await persist();
    const next: Doc = { ...base, caption: '', comment: '', hooks: written.hooks.slice(0, 4),
      reel: updatedStory(base.reel, written, datelineFor(article.published), mark),
      sourceText: article.text, sourceUrl: article.url, sourceTitle: article.title, sourcePublisher: article.publisher };
    const id = newId();
    draftRef.current = { id, revision: 0 };
    savedHash.current = '';
    docRef.current = next;
    setDocState(next); setDraftId(id); setRenders({}); setSaveState('');
    try { localStorage.setItem('reel_creator_draft', id); } catch { /* A remembered draft is a convenience. */ }
    setNotice('Updated from the new reporting as a new reel. Check every scene, then generate a new caption.');
  });

  const saveHouseStyle = (name: string) => attempt(null, async () => {
    setHouseStyles(await reelApi.saveHouseStyle(houseStyleOf(docRef.current.reel, newId(), name)));
    setNotice(`Saved the house style "${name.trim()}".`);
  });
  const deleteHouseStyle = (id: string) => attempt(null, async () => { setHouseStyles(await reelApi.deleteHouseStyle(id)); });
  const pickHouseStyle = (house: HouseStyle) => setReel(reel => applyHouseStyle(reel, house));

  /** Which kind of reel this is. It steers the next hooks and caption; nothing is rewritten automatically. */
  const setGoal = (goal: ReelGoal) => {
    setDoc(d => ({ ...d, goal }));
    try { localStorage.setItem('reel_creator_goal', goal); } catch { /* Not remembering is harmless. */ }
  };

  /** The article this reel was written from, exactly as first read; a link is not fetched again. */
  const sourceInput = (): Article | string => {
    const d = docRef.current;
    return d.sourceText ? { text: d.sourceText, url: d.sourceUrl, title: d.sourceTitle, publisher: d.sourcePublisher, published: '' } : d.sourceUrl;
  };
  const moreHooks = () => attempt('hooks', async () => {
    const { written } = await requestWriting(sourceInput(), 'auto');
    setDoc(d => ({ ...d, hooks: written.hooks.slice(0, 4) }));
  });
  /**
   * New words for one scene in its current type, from the same article; its photo, credit, timing and styles stay.
   * When the article has no quote or figure for that type, the writer says so and the scene is left as it is.
   */
  const rewriteScene = (index: number) => attempt('rewrite', async () => {
    const reel = docRef.current.reel, scene = reel.scenes[index];
    if (!scene) return;
    const kind = scene.kind ?? 'story';
    const existing = reel.scenes.filter((s, i) => i !== index && s.kind !== 'end').map(s => s.headline.trim()).filter(Boolean).slice(0, MAX_SCENES);
    setRewriting(index);
    try {
      const { written } = await requestWriting(sourceInput(), 'auto', { rewrite: kind, position: Math.min(MAX_SCENES, index + 1), existing,
        replacing: [scene.headline, scene.detail].filter(Boolean).join('. ').slice(0, 300) });
      const words = rewrittenWords(written, kind);
      if (!words) throw new Error('The AI wrote nothing for this scene. Try again.');
      const before = sceneWords(scene);
      setReel(r => ({ ...r, scenes: r.scenes.map((s, i) => i === index ? { ...s, ...words } : s) }));
      setUndo({ index, words: before });
      setNotice(`Rewrote scene ${index + 1} as ${KIND_NAMES[kind] === 'End card' ? 'an end card' : `a ${KIND_NAMES[kind]}`} from the article.`);
    } finally { setRewriting(null); }
  });
  const undoRewrite = () => {
    if (!undo) return;
    setReel(r => ({ ...r, scenes: r.scenes.map((s, i) => i === undo.index ? { ...s, ...undo.words } : s) }));
    setUndo(null); setNotice('');
  };

  const rewriteCaption = () => attempt('caption', async () => {
    const { written } = await requestWriting(sourceInput(), 'auto', { caption: true });
    setDoc(d => ({ ...d, caption: written.caption, comment: written.comment }));
  });

  const importImages = async (limit: number, files?: File[]): Promise<ReelAsset[]> => {
    if (limit < 1) return [];
    if (files && files.length > limit) throw new Error(`Choose up to ${limit} photos.`);
    let result;
    if (!files) result = await reelApi.pickImages(Math.min(MAX_SCENES, limit));
    else {
      result = { assets: [] as ReelAsset[], errors: [] as string[] };
      for (const file of files) {
        try { result.assets.push(await reelApi.upload('image', file)); } catch (err) { result.errors.push(`${file.name}: ${message(err)}`); }
      }
    }
    if (!result) return [];
    if (result.errors.length) setError(result.errors.join(' '));
    return result.assets;
  };

  /** Write text for scenes that were just added, continuing what the reel already says. Photos stay either way. */
  const writeInto = async (indexes: number[]) => {
    if (!indexes.length || !(docRef.current.sourceText || docRef.current.sourceUrl)) return;
    try {
      const { written } = await requestWriting(sourceInput(), 'auto', { scenes: indexes.length, existing: headlines(docRef.current.reel) });
      setReel(reel => fillScenes(reel, indexes, written.scenes));
    } catch (err) { setError(`The new scene needs text: ${message(err)}`); }
  };

  /** Photos fill empty scenes first; each extra photo becomes a new scene written from the article. */
  const addPhotos = (files?: File[]) => attempt('photos', async () => {
    const room = docRef.current.reel.scenes.filter(s => !s.image_id).length + MAX_SCENES - docRef.current.reel.scenes.length;
    const assets = await importImages(room, files);
    if (!assets.length) return;
    let created: number[] = [];
    setReel(reel => { const placed = placePhotos(reel, assets); created = placed.created; return placed.reel; });
    await writeInto(created);
  });
  const addSceneWithText = () => attempt('scene', async () => {
    if (docRef.current.reel.scenes.length >= MAX_SCENES) return;
    const index = insertIndex(docRef.current.reel);
    setReel(addScene);
    await writeInto([index]);
  });
  const changePhoto = (index: number, files?: File[]) => attempt('photos', async () => {
    const [asset] = await importImages(1, files);
    if (asset) setReel(reel => setPhoto(reel, index, asset));
  });

  const setBrand = (patch: Partial<Pick<Reel, 'brand' | 'logo_id' | 'source'>>) => {
    setReel(reel => ({ ...reel, ...patch }));
    window.clearTimeout(brandTimer.current);
    brandTimer.current = window.setTimeout(() => {
      void reelApi.saveBrand(docRef.current.reel).then(value => { brandRef.current = value; }).catch(err => setError(message(err)));
    }, 600);
  };
  const uploadLogo = (file: File) => attempt('logo', async () => {
    const asset = await reelApi.upload('logo', file);
    setBrand({ logo_id: asset.id });
  });

  const pickMusic = () => attempt('music', async () => {
    const asset = await reelApi.pickAudio();
    if (asset) setReel(reel => ({ ...reel, music: 'custom', music_id: asset.id }));
  });
  const setMusic = (music: string) => setReel(reel => ({ ...reel, music, music_id: music === 'custom' ? reel.music_id : null }));

  const startRender = (kind: ReelJob['kind']) => attempt('render', async () => {
    if (output === 'photo') return;
    const slot = output;
    const { caption } = docRef.current;
    const reel = slot === 'live' ? liveVersion(docRef.current.reel) : docRef.current.reel;
    const key = reelKey(reel);
    const idKey = kind + key + caption;
    const requestId = requestIds.current.get(idKey) ?? crypto.randomUUID();
    requestIds.current.set(idKey, requestId);
    await persist();
    const result = await reelApi.render(reel, kind, requestId, caption);
    requestIds.current.delete(idKey);
    track(result, key, slot);
  });
  const updateJob = (action: 'cancel' | 'retry') => attempt(null, async () => {
    if (!job) return;
    const result = await reelApi[action](job.id);
    setJobs(old => old.map(j => j.id === job.id ? result : j));
  });

  const fresh = !!job && !!render && render.key === reelKey(shown);
  const makePhoto = (scene: number) => reelApi.photo(docRef.current.reel, scene, photoRatio);
  /** Hands the chosen output to Campaigns: a photo or the Reel to Posts, the Live video to Live. */
  const send = (scene: number) => attempt(output === 'photo' ? 'photo' : null, async () => {
    const { reel, caption, goal, comment } = docRef.current;
    const words = { name: reel.scenes.find(s => s.headline)?.headline.replace(/\n/g, ' ').slice(0, 80) || 'News reel', caption,
      comment: goal === 'readmore' ? comment.trim() || undefined : undefined };
    await persist();
    if (output === 'photo') {
      const photo = await makePhoto(scene);
      onUseReel({ jobId: photo.id, filename: photo.filename, postType: 'photo', ...words });
    } else if (job?.output && job.kind === 'final' && fresh) {
      onUseReel({ jobId: job.id, filename: job.output.filename, postType: output, thumbnail: job.output.thumbnail ?? undefined, ...words });
    }
  });
  const saveFile = (scene: number) => attempt(output === 'photo' ? 'photo' : null, async () => {
    if (output === 'photo') setNotice(await reelApi.savePhoto(await makePhoto(scene)));
    else if (job?.output) setNotice(await reelApi.export(job.id, job.output.filename));
  });

  const discard = async () => {
    const id = draftRef.current.id;
    try { if (draftRef.current.revision > 0) await reelApi.deleteDraft(id); } catch (err) { setError(message(err)); return; }
    const rest = drafts.filter(d => d.id !== id);
    setDrafts(rest);
    open(null);
  };

  return {
    capabilities, drafts, doc, draftId, houseStyles, saveHouseStyle, deleteHouseStyle, pickHouseStyle, loaded, busy, error, notice, saveState, job, fresh,
    setError, setNotice, setReel, setDoc, open, discard,
    create, updateStory, moreHooks, rewriteScene, undoRewrite, undo, rewriting, rewriteCaption, importImages,
    setGoal, addPhotos, changePhoto, setBrand, uploadLogo, pickMusic, setMusic, startRender, updateJob, send, saveFile, output, setOutput, photoRatio, setPhotoRatio, shown,
    pickHook: (hook: string) => setReel(reel => withHook(reel, hook)),
    moveScene: (from: number, to: number) => setReel(reel => moveScene(reel, from, to)),
    removeScene: (index: number) => setReel(reel => removeScene(reel, index)),
    addScene: addSceneWithText,
  };
}
