import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { configuredAI } from '../services/aiSettings';
import { compositionApi, type CompositionDirectorResult, type CompositionDraft } from '../services/compositionApi';
import { reelApi } from '../services/reelCreator';
import { sourceKind } from '../services/reelDocument';
import type { Composition, CompositionDoc, CompositionScene, PartsCatalog, QualityNote } from '../types/composition';
import type { CreatedReelMedia, DirectorTurn, ReelAsset, ReelGoal, ReelJob } from '../types/reelCreator';

type Busy = null | 'compose' | 'direct' | 'review' | 'photos' | 'render' | 'send';
const ACTIVE = ['queued', 'rendering', 'verifying'];
const DRAFT_KEY = 'ai_reel_studio_draft';
const message = (err: unknown) => err instanceof Error ? err.message : 'Something went wrong.';
const newId = () => crypto.randomUUID().replace(/-/g, '');
const remember = (key: string, value: string) => { try { localStorage.setItem(key, value); } catch { /* A convenience only. */ } };
const recall = (key: string) => { try { return localStorage.getItem(key); } catch { return null; } };

/** What a render depends on: locks are for the director only. */
export const compositionKey = (c: Composition) => JSON.stringify({ ...c, scenes: c.scenes.map(({ locked: _locked, ...s }) => s) });

const docOf = (draft: CompositionDraft): CompositionDoc => ({
  composition: draft.reel, caption: draft.caption, comment: draft.comment ?? '', goal: draft.goal ?? 'engage', hooks: draft.hooks ?? [],
  sourceText: draft.source_text, sourceUrl: draft.source_url, sourceTitle: draft.source_title ?? '', sourcePublisher: draft.source_publisher ?? '',
});
const snapshot = (doc: CompositionDoc) => ({ reel: doc.composition, caption: doc.caption, comment: doc.comment, goal: doc.goal, hooks: doc.hooks,
  source_text: doc.sourceText, source_url: doc.sourceUrl, source_title: doc.sourceTitle, source_publisher: doc.sourcePublisher });

export function useAIReelStudio(active: boolean, onUseReel: (media: CreatedReelMedia) => void) {
  const [catalog, setCatalog] = useState<PartsCatalog | null>(null);
  const [drafts, setDrafts] = useState<CompositionDraft[]>([]);
  const [doc, setDocState] = useState<CompositionDoc | null>(null);
  const [draftId, setDraftId] = useState(newId);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState('');
  const [notes, setNotes] = useState<QualityNote[]>([]);
  const [saveState, setSaveState] = useState('');
  const [jobs, setJobs] = useState<ReelJob[]>([]);
  const [render, setRender] = useState<{ jobId: string; key: string } | null>(null);
  const [directed, setDirected] = useState<(CompositionDirectorResult & { before: Composition }) | null>(null);
  const [turns, setTurns] = useState<DirectorTurn[]>([]);
  const docRef = useRef<CompositionDoc | null>(null);
  const draftRef = useRef({ id: draftId, revision: 0 });
  const savedHash = useRef('');
  const saveChain = useRef<Promise<unknown>>(Promise.resolve());

  const setDoc = useCallback((change: (doc: CompositionDoc) => CompositionDoc) => {
    setDocState(old => { if (!old) return old; const next = change(old); docRef.current = next; return next; });
  }, []);
  const setComposition = useCallback((change: (c: Composition) => Composition) => setDoc(d => ({ ...d, composition: change(d.composition) })), [setDoc]);

  const persist = useCallback(() => {
    const run = saveChain.current.then(async () => {
      const current = docRef.current;
      if (!current) return;
      const hash = JSON.stringify(snapshot(current));
      if (hash === savedHash.current) return;
      setSaveState('Saving…');
      const saved = await compositionApi.saveDraft(draftRef.current.id, draftRef.current.revision, snapshot(current));
      draftRef.current = { id: saved.id, revision: saved.revision };
      savedHash.current = hash;
      setSaveState('Saved');
      setDrafts(old => [saved, ...old.filter(d => d.id !== saved.id)]);
    });
    saveChain.current = run.catch(() => undefined);
    return run;
  }, []);

  const open = useCallback((draft: CompositionDraft | null) => {
    const id = draft?.id ?? newId();
    const next = draft ? docOf(draft) : null;
    draftRef.current = { id, revision: draft?.revision ?? 0 };
    savedHash.current = next ? JSON.stringify(snapshot(next)) : '';
    docRef.current = next;
    setDocState(next); setDraftId(id); setError(''); setNotes([]); setDirected(null); setTurns([]); setSaveState(draft ? 'Saved' : '');
    try { const saved = JSON.parse(localStorage.getItem(`ai_reel_render:${id}`) || 'null'); setRender(saved?.jobId ? saved : null); } catch { setRender(null); }
    if (draft) remember(DRAFT_KEY, id);
  }, []);

  useEffect(() => {
    if (!active || loaded) return;
    let cancelled = false;
    (async () => {
      try {
        const [parts, saved, renders] = await Promise.all([compositionApi.catalog(), compositionApi.drafts(), reelApi.jobs()]);
        if (cancelled) return;
        setCatalog(parts); setDrafts(saved); setJobs(renders);
        const remembered = recall(DRAFT_KEY);
        open(saved.find(d => d.id === remembered) ?? null);
        setLoaded(true);
      } catch (err) { if (!cancelled) setError(message(err)); }
    })();
    return () => { cancelled = true; };
  }, [active, loaded, open]);

  useEffect(() => {
    if (!loaded || !doc) return;
    const timer = window.setTimeout(() => { void persist().catch(err => { setSaveState(''); setError(message(err)); }); }, 650);
    return () => window.clearTimeout(timer);
  }, [doc, loaded, persist]);

  const job = useMemo(() => render ? jobs.find(j => j.id === render.jobId) ?? null : null, [jobs, render]);
  const fresh = !!doc && !!render && render.key === compositionKey(doc.composition);
  const working = !!job && ACTIVE.includes(job.state);
  useEffect(() => {
    if (!active || !working) return;
    let timer = 0, cancelled = false;
    const poll = async () => {
      try { const result = await reelApi.jobs(); if (!cancelled) setJobs(result); } catch (err) { if (!cancelled) setError(message(err)); }
      if (!cancelled) timer = window.setTimeout(poll, 1200);
    };
    timer = window.setTimeout(poll, 1200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [active, working]);

  const attempt = async (kind: Busy, work: () => Promise<void>) => {
    if (busy) return;
    setBusy(kind); setError('');
    try { await work(); } catch (err) { setError(message(err)); } finally { setBusy(null); }
  };

  /** Write and compose a reel from an article or its link, with the photos in order and what it should feel like. */
  const compose = (source: string, photos: ReelAsset[], scenario: string, recipe: string | null, goal: ReelGoal) => attempt('compose', async () => {
    const ai = configuredAI('write');
    let article = { text: source.trim(), url: '', title: '', publisher: '' };
    if (sourceKind(source) === 'url') {
      const read = await reelApi.article(source.trim());
      article = { text: read.text, url: read.url, title: read.title, publisher: read.publisher };
    }
    if (article.text.length < 80) throw new Error('Paste the full article text, or a link to it.');
    const result = await compositionApi.compose({ ...ai, text: article.text, source_url: article.url, source_title: article.title, publisher: article.publisher,
      goal, photos: photos.map(p => p.id), scenario: scenario.trim(), recipe });
    const next: CompositionDoc = { composition: result.composition, caption: result.caption, comment: result.comment, goal, hooks: result.hooks,
      sourceText: article.text, sourceUrl: article.url, sourceTitle: article.title, sourcePublisher: article.publisher };
    const id = newId();
    draftRef.current = { id, revision: 0 };
    savedHash.current = '';
    docRef.current = next;
    setDocState(next); setDraftId(id); setRender(null); setDirected(null); setTurns([]);
    setNotes([...result.fixes, ...result.problems]);
    remember(DRAFT_KEY, id);
  });

  const direct = (instruction: string) => attempt('direct', async () => {
    const base = docRef.current;
    if (!base) return;
    const result = await compositionApi.direct({ ...configuredAI('direct'), reel: base.composition, instruction: instruction.trim(),
      source_text: base.sourceText, goal: base.goal, history: turns.slice(-6) });
    if (docRef.current?.composition !== base.composition) throw new Error('The reel changed while the director was working. Send the instruction again.');
    if (result.changes.length) setComposition(() => result.reel);
    setDirected({ ...result, before: base.composition });
    setTurns(old => [...old, { instruction: instruction.trim().slice(0, 500), message: result.message.slice(0, 400) }].slice(-6));
  });

  const undoDirected = () => {
    if (!directed) return;
    const before = directed.before;
    setComposition(() => before);
    setDirected(null);
    setTurns(old => [...old, { instruction: 'Undo your last change.', message: 'Undone: the reel is back as it was.' }].slice(-6));
  };

  /** Run the quality rules over hand edits; what they change is listed for the operator. */
  const reviewNow = () => attempt('review', async () => {
    const base = docRef.current;
    if (!base) return;
    const result = await compositionApi.review(base.composition);
    setComposition(() => result.composition);
    setNotes([...result.fixes, ...result.problems]);
  });

  const editScene = (index: number, patch: Partial<CompositionScene>) =>
    setComposition(c => ({ ...c, scenes: c.scenes.map((s, i) => i === index ? { ...s, ...patch } : s) }));

  /** Photos fill scenes that have none, in order. */
  const addPhotos = () => attempt('photos', async () => {
    const picked = await reelApi.pickImages(8);
    if (!picked) return;
    if (picked.errors.length) setError(picked.errors.join(' '));
    const queue = [...picked.assets];
    setComposition(c => ({ ...c, scenes: c.scenes.map(s => {
      if (s.background.image_id || s.kind === 'end' || !queue.length) return s;
      const asset = queue.shift()!;
      return { ...s, background: { ...s.background, image_id: asset.id, focal_x: asset.focus?.x ?? .5, focal_y: asset.focus?.y ?? .4 } };
    }) }));
  });

  const startRender = (kind: 'final' | 'preview') => attempt('render', async () => {
    const base = docRef.current;
    if (!base) return;
    const key = compositionKey(base.composition);
    const result = await compositionApi.render(base.composition, kind, crypto.randomUUID(), base.caption);
    const tracked = { jobId: result.id, key };
    setRender(tracked);
    remember(`ai_reel_render:${draftRef.current.id}`, JSON.stringify(tracked));
    setJobs(old => [result, ...old.filter(j => j.id !== result.id)]);
  });

  /** The finished video goes to Posts with its caption and first comment. */
  const send = () => attempt('send', async () => {
    const base = docRef.current;
    if (!base || !job?.output || job.kind !== 'final' || !fresh) throw new Error('Make the final video first.');
    await persist();
    const cover = (await compositionApi.cover(base.composition)).filename;
    onUseReel({ jobId: job.id, filename: job.output.filename, postType: 'reel', thumbnail: job.output.thumbnail ?? undefined, draftId: draftRef.current.id,
      name: base.composition.scenes.find(s => s.headline)?.headline.slice(0, 80) || 'News reel', caption: base.caption,
      comment: base.goal === 'readmore' ? base.comment.trim() || undefined : undefined, goal: base.goal,
      websiteSource: { title: base.sourceTitle, text: base.sourceText, credit: base.sourcePublisher, cover_file: cover || job.output.thumbnail || '' } });
  });

  const discard = async () => {
    const id = draftRef.current.id;
    try { if (draftRef.current.revision > 0) await compositionApi.deleteDraft(id); } catch (err) { setError(message(err)); return; }
    setDrafts(old => old.filter(d => d.id !== id));
    open(null);
  };

  return {
    catalog, drafts, doc, draftId, loaded, busy, error, setError, notes, setNotes, saveState, job, fresh, working,
    open, discard, compose, direct, directed, undoDirected, reviewNow, editScene, setDoc, setComposition, addPhotos, startRender, send,
    cancelJob: () => job && attempt('render', async () => { const r = await reelApi.cancel(job.id); setJobs(old => old.map(j => j.id === r.id ? r : j)); }),
  };
}
