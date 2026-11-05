import { useEffect, useRef, useState } from 'react';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';
import { uploadMediaFiles } from '../services/api';
import { reelApi } from '../services/reelCreator';
import { articleOptions, captionsFingerprint, captionsReady, captionSourceOf, emptyArticle, knownCaptions, readMoreFingerprint, readMoreReady, websiteRequest, openWebsiteManagement, WEBSITE_CONNECTIONS_CHANGED } from '../services/websites';
import type { ReadMoreDraft, ReelSource, WebsiteArticle, WebsiteConnection, WebsitePreparation } from '../services/websites';

interface Catalog { articles: (WebsiteArticle & { status: string })[]; categories: { id: string; name: string }[]; authors: { id: string; name: string }[] }
const cls = 'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-xs text-zinc-200';
const button = 'rounded-lg border border-zinc-700 px-3 py-2 text-xs text-blue-300 disabled:opacity-40';
export function ReadMorePanel({ value, onChange, type, media, caption, profiles, disabled = false, reelSource }: {
  value?: ReadMoreDraft; onChange: (value: ReadMoreDraft) => void; type: 'photo' | 'reel' | 'live';
  media: string; caption: string; profiles: { id: string; name: string }[]; disabled?: boolean; reelSource?: ReelSource;
}) {
  const draft = value || { enabled: false, connectionId: '', article: emptyArticle(), uniqueCaptions: false };
  const [connections, setConnections] = useState<WebsiteConnection[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const ids = profiles.map(profile => profile.id);
  const activeConnection = connections.find(connection => connection.id === draft.connectionId);
  const activeSchema = activeConnection?.schema;
  const fieldLimit = (name: string, fallback: number, cap: number) => Math.min(activeSchema?.fields[name]?.maxLength || fallback, cap);
  const fingerprint = readMoreFingerprint(draft, type, media, caption, ids);
  const latest = useRef(fingerprint); latest.current = fingerprint;
  const captionsKey = captionsFingerprint(draft, type, media, caption, ids);
  const latestCaptions = useRef(captionsKey); latestCaptions.current = captionsKey;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!draft.enabled) return;
    let active = true, sequence = 0;
    const load = async () => {
      const request = ++sequence;
      try {
        const saved = await websiteRequest<WebsiteConnection[]>('');
        if (active && request === sequence) setConnections(saved);
      } catch (error) {
        if (active && request === sequence) setMessage(error instanceof Error ? error.message : 'Could not load websites.');
      }
    };
    void load();
    window.addEventListener(WEBSITE_CONNECTIONS_CHANGED, load);
    return () => { active = false; window.removeEventListener(WEBSITE_CONNECTIONS_CHANGED, load); };
  }, [draft.enabled]);
  useEffect(() => {
    setCatalog(null);
    if (!draft.enabled || !activeConnection) return;
    let active = true;
    websiteRequest<Catalog>(`/${draft.connectionId}/catalog`).then(result => { if (active) setCatalog(result); })
      .catch(error => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [draft.connectionId, draft.enabled, activeConnection]);
  const update = (patch: Partial<ReadMoreDraft>) => onChange({ ...draft, ...patch });
  const article = (patch: Partial<WebsiteArticle>) => update({ article: { ...draft.article, ...patch } });
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setMessage('');
    try { await action(); } catch (error) { if (mounted.current) setMessage(error instanceof Error ? error.message : 'Website request failed.'); }
    finally { if (mounted.current) setBusy(false); }
  };
  const geminiKeys = () => {
    let keys: { gemini?: string; gemini_model?: string } = {};
    try { keys = JSON.parse(localStorage.getItem('app_api_keys') || '{}'); } catch { /* Backend reports missing key. */ }
    return { api_key: keys.gemini || '', model: keys.gemini_model || 'gemini-3.8-flash' };
  };
  const generateCaptions = () => run(async () => {
    if (!profiles.length) throw new Error('Choose accounts first.');
    const started = latestCaptions.current;
    const known = knownCaptions(draft, caption, ids);
    const result = await websiteRequest<WebsitePreparation>('/captions', {
      title: draft.article.title, content: draft.article.content, caption, profiles: [...ids].sort(), post_type: type, media_file: media,
      known_captions: known, ...geminiKeys(),
    });
    if (!mounted.current || latestCaptions.current !== started) return;
    onChange({ ...draft, captionsPreparationId: result.id, captionsFor: started, captionSource: captionSourceOf(draft.article, caption),
      captions: Object.fromEntries(ids.map(id => [id, known[id] ?? result.destinations[id].caption])) });
    setMessage('Captions ready. Nothing was published. Review them above.');
  });
  const prepare = () => run(async () => {
    if (!profiles.length) throw new Error('Choose accounts first.');
    if (!draft.connectionId) throw new Error('Connect and choose a website first.');
    const updating = Boolean(draft.article.id);
    const requestId = draft.requestFingerprint === fingerprint && draft.requestId ? draft.requestId : crypto.randomUUID().replace(/-/g, '');
    const next = { ...draft, requestId, requestFingerprint: fingerprint };
    onChange(next); // Retain request identity even if the website response is lost.
    const preparation = await websiteRequest<WebsitePreparation>('/prepare', {
      request_id: requestId, connection_id: draft.connectionId, article: draft.article,
      profiles: [...ids].sort(), post_type: type, media_file: media, caption: draft.uniqueCaptions ? '' : caption,
      unique_captions: draft.uniqueCaptions, known_captions: knownCaptions(draft, caption, ids), ...geminiKeys(),
    });
    if (!mounted.current || latest.current !== fingerprint) return;
    const published = { ...next, article: { ...next.article, id: preparation.article.id } };
    onChange({ ...published, preparation, preparedFingerprint: readMoreFingerprint(published, type, media, caption, ids),
      captionsFor: draft.uniqueCaptions ? captionsFingerprint(published, type, media, caption, ids) : draft.captionsFor,
      captionSource: draft.uniqueCaptions ? captionSourceOf(published.article, caption) : undefined,
      captions: Object.fromEntries(Object.entries(preparation.destinations).map(([id, destination]) => [id, destination.caption])) });
    const done = `Article ${updating ? 'updated' : 'published'}. Review the comment links below.`;
    setMessage(done);
    if (reelSource) await reelApi.saveArticleLink(reelSource.draftId, { connection_id: draft.connectionId, url: preparation.article.url, article: published.article,
      source_title: reelSource.title, source_text: reelSource.text }).catch(() => setMessage(`${done} The reel could not remember this article, so choose it in the list next time.`));
  });
  const regenerate = (profileId: string) => run(async () => {
    const started = latestCaptions.current;
    const result = await websiteRequest<{ caption: string }>('/regenerate-hook', {
      preparation_id: draft.captionsPreparationId, profile_id: profileId,
      other_captions: Object.values(draft.captions || {}), ...geminiKeys(),
    });
    if (mounted.current && latestCaptions.current === started) update({ captions: { ...draft.captions, [profileId]: result.caption } });
  });
  const ready = readMoreReady(draft, type, media, caption, ids);
  const haveCaptions = captionsReady(draft, type, media, caption, ids);
  const someCaptions = Object.keys(knownCaptions(draft, caption, ids)).length > 0;
  return <fieldset disabled={disabled || busy} className="space-y-3 rounded-xl border border-zinc-700 p-3 text-xs">
    <label className="flex items-center gap-2 text-zinc-200"><Checkbox checked={draft.uniqueCaptions} onCheckedChange={uniqueCaptions => update({ uniqueCaptions })} />Unique caption for each account · 10–30 words · replaces the caption above</label>
    {draft.uniqueCaptions && <div className="space-y-2">
      <p className="text-zinc-400">Written by AI from {draft.article.content.trim() ? 'the article text' : 'your caption'}. Nothing is published to a website.</p>
      <button type="button" className={button} onClick={generateCaptions} disabled={!profiles.length || busy || haveCaptions}>{busy ? 'Working…' : someCaptions ? 'Generate captions for new accounts' : 'Generate captions'}</button>
      {!profiles.length && <p className="text-amber-300">Choose accounts first.</p>}
      {haveCaptions && profiles.map(profile => <div key={profile.id} className="space-y-1 rounded-lg bg-zinc-950 p-2">
        <label className="block text-zinc-300">{profile.name}<textarea rows={2} className={cls} value={draft.captions?.[profile.id] ?? ''}
          onChange={event => update({ captions: { ...draft.captions, [profile.id]: event.target.value } })} /></label>
        <button type="button" className={button} onClick={() => regenerate(profile.id)}>Regenerate caption</button>
      </div>)}
    </div>}
    <label className="flex items-center gap-2 text-zinc-200"><Checkbox checked={draft.enabled} onCheckedChange={enabled => update({ enabled })} />Read more · website article</label>
    {draft.enabled && <>
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-40 flex-1"><Select ariaLabel="Website connection" value={draft.connectionId} placeholder="Choose website"
          options={connections.map(connection => ({ value: connection.id, label: connection.name }))} onValueChange={connectionId => update({ connectionId, article: { ...draft.article, id: '', cover_id: '', category: '', author: '', custom_fields: {} } })} /></div>
        <button type="button" className={button} onClick={openWebsiteManagement}>Manage websites</button>
      </div>
      {connections.length === 0 && <p className="text-zinc-400">Add a website in Settings → Websites, then select it here.</p>}
      <Select ariaLabel="Website article" value={draft.article.id || 'new'} placeholder="New article" options={articleOptions(catalog?.articles || [], draft.article, id => { const item = catalog!.articles.find(entry => entry.id === id)!; return `${item.title} · ${item.status}`; })} onValueChange={id => {
        if (id === 'new') return update({ article: { ...draft.article, id: '', cover_id: '' } });
        const found = catalog?.articles.find(item => item.id === id);
        if (found) { const { status: _status, ...article } = found; update({ article }); }
      }} />
      <input className={cls} aria-label="Article title" placeholder="Article title" maxLength={fieldLimit('title', 300, 300)} value={draft.article.title} onChange={event => article({ title: event.target.value })} />
      <textarea className={cls} aria-label="Article excerpt" placeholder={`Article excerpt (up to ${fieldLimit('excerpt', 160, 1000)} characters)`} maxLength={fieldLimit('excerpt', 160, 1000)} rows={2} value={draft.article.excerpt} onChange={event => article({ excerpt: event.target.value })} />
      <textarea className={cls} aria-label="Article content" placeholder="Full article text" maxLength={120000} rows={5} value={draft.article.content} onChange={event => article({ content: event.target.value })} />
      <div className="flex flex-wrap items-center gap-2 text-zinc-400">
        <label className={`${button} cursor-pointer`}>Choose cover image<input type="file" accept="image/jpeg,image/png,image/webp,image/avif" className="hidden" onChange={event => {
          const file = event.currentTarget.files?.[0]; event.currentTarget.value = '';
          if (file) void run(async () => {
            if (file.size > 20 * 1024 * 1024) throw new Error('Cover image must be at most 20 MB.');
            const data = new FormData(); data.append('files', file);
            const result = await uploadMediaFiles(data);
            article({ cover_file: result.files[0].filename, cover_id: '' });
          });
        }} /></label>
        <span className="break-all">{draft.article.cover_file || (draft.article.cover_id ? 'Existing article cover' : 'Cover image required')}</span>
      </div>
      <details className="space-y-2 text-zinc-300"><summary className="cursor-pointer">SEO & article details</summary>
        {([['seo_title', 'SEO title'], ['seo_description', 'SEO description'], ['slug', 'Article slug'], ['alt', 'Cover alt text'], ['tags', 'Tags, separated by commas'], ['credit', 'Source credit']] as const).map(([field, label]) => <label key={field} className="block space-y-1">{label}
          <input className={cls} value={draft.article[field]} maxLength={fieldLimit(field, field === 'seo_description' ? 160 : field === 'tags' ? 1000 : 300, field === 'seo_description' || field === 'tags' ? 1000 : 300)} onChange={event => article({ [field]: event.target.value })} /></label>)}
        <Select ariaLabel="Article category" value={draft.article.category || 'none'} options={[{ value: 'none', label: 'No category' }, ...(catalog?.categories || []).map(item => ({ value: item.id, label: item.name }))]} onValueChange={category => article({ category: category === 'none' ? '' : category })} />
        <Select ariaLabel="Article author" value={draft.article.author || 'none'} options={[{ value: 'none', label: 'Default author' }, ...(catalog?.authors || []).map(item => ({ value: item.id, label: item.name }))]} onValueChange={author => article({ author: author === 'none' ? '' : author })} />
      </details>
      {Object.entries(activeSchema?.fields || {}).filter(([key]) => key.startsWith('custom_fields.')).map(([key, field]) => {
        const name = key.slice('custom_fields.'.length);
        const value = draft.article.custom_fields?.[name] ?? field.default;
        const change = (next: string | number | boolean | null) => article({ custom_fields: { ...draft.article.custom_fields, [name]: next } });
        return <label className="block space-y-1 text-zinc-300" key={key}>{field.label || name}{field.required ? ' *' : ''}
          {field.type === 'boolean' ? <Checkbox checked={value === true} onCheckedChange={change} /> :
            <input className={cls} type={field.type === 'number' ? 'number' : 'text'} maxLength={field.maxLength} required={field.required}
              value={value == null ? '' : String(value)} onChange={event => change(field.type === 'number' ? event.target.value === '' ? null : Number(event.target.value) : event.target.value)} />}
        </label>;
      })}
      {!draft.uniqueCaptions && !caption.trim() && <p className="text-amber-300">No caption yet. Add one above, or every account posts without a caption.</p>}
      <p className="text-zinc-400">{draft.article.id ? 'Updates this article on the website immediately instead of making a new one,' : 'Publishes the article immediately'} and creates a unique comment link for every account. {type === 'live' ? 'Live comments are posted after the broadcast ends.' : 'Links are posted in the first comment.'}</p>
      {draft.uniqueCaptions && !haveCaptions && <p className="text-amber-300">Generate the captions above first.</p>}
      <button type="button" className={button} onClick={prepare} disabled={!connections.some(connection => connection.id === draft.connectionId) || !profiles.length || busy || Boolean(draft.preparation && ready) || (draft.uniqueCaptions && !haveCaptions)}>{busy ? (draft.article.id ? 'Updating…' : 'Publishing…') : draft.article.id ? 'Update article & create links' : 'Publish article & create links'}</button>
      {draft.preparation && !ready && <p className="text-amber-300">Content or accounts changed. Prepare and review again before posting.</p>}
      {draft.preparation && ready && <div className="space-y-3">
        <a href={draft.preparation.article.url} target="_blank" rel="noreferrer" className="text-blue-300 underline">Open published article</a>
        {profiles.map(profile => {
          const destination = draft.preparation!.destinations[profile.id];
          return <div key={profile.id} className="space-y-1 rounded-lg bg-zinc-950 p-2">
            {draft.uniqueCaptions ? <p className="text-zinc-300">{profile.name}</p> : <label className="block text-zinc-300">{profile.name}<textarea rows={2} className={cls} value={draft.captions?.[profile.id] ?? destination.caption}
              onChange={event => update({ captions: { ...draft.captions, [profile.id]: event.target.value } })} /></label>}
            <a className="block break-all text-blue-300" href={destination.link} target="_blank" rel="noreferrer">{destination.link}</a>
          </div>;
        })}
      </div>}
    </>}
    {message && <p role="status" className="text-zinc-300">{message}</p>}
  </fieldset>;
}
