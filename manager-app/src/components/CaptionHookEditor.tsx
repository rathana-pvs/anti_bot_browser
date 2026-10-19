import { useEffect, useRef, useState } from 'react';
import { Checkbox } from './ui/Checkbox';
import { API_BASE } from '../services/api';

import { HookDraft, hookFingerprint, hookIsApproved } from '../services/captionHooks';
export { hookIsApproved } from '../services/captionHooks';
export type { HookDraft } from '../services/captionHooks';

export function CaptionHookEditor({ caption, media, type, value, recentHooks, onChange }: {
  caption: string; media: string; type: 'photo' | 'reel'; value?: HookDraft;
  recentHooks: string[]; onChange: (draft: HookDraft) => void;
}) {
  const draft = value || { enabled: false };
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const fingerprint = hookFingerprint(caption, media, type, draft);
  const current = useRef('');
  // Include edits/enablement so late requests cannot approve an older selection.
  current.current = JSON.stringify([fingerprint, draft.enabled, draft.selected]);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const update = (patch: Partial<HookDraft>) => onChange({ ...draft, ...patch, approval: undefined });
  const finalCaption = (draft.selected || '').trim() + (caption ? '\n\n' + caption : '');
  const approved = hookIsApproved(caption, media, type, draft);
  const request = async (approve: boolean) => {
    const started = current.current;
    setBusy(true); setMessage('');
    try {
      let body: object;
      if (approve) {
        if (draft.sourceFingerprint !== fingerprint) throw new Error('Context changed. Generate hooks again.');
        body = { generation_id: draft.generationId, hook: draft.selected };
      } else {
        let keys: { gemini?: string; gemini_model?: string } = {};
        try { keys = JSON.parse(localStorage.getItem('app_api_keys') || '{}'); } catch { /* handled below */ }
        if (!keys.gemini) throw new Error('Add your Gemini key in Settings first.');
        body = { api_key: keys.gemini, model: keys.gemini_model || 'gemini-3.8-flash', context: {
          original_caption: caption, media_file: media, post_type: type,
          description: draft.description || '', audience: draft.audience || '',
          language: draft.language || 'Match caption', tone: draft.tone || 'Friendly',
          avoid: draft.avoid || '', recent_hooks: recentHooks.slice(-30),
        } };
      }
      const response = await fetch(`${API_BASE}/ai/caption-hooks${approve ? '/approve' : ''}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!mounted.current || current.current !== started) return;
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Hook request failed.');
      if (approve) {
        if (result.final_caption !== finalCaption) throw new Error('Preview changed. Review again.');
        onChange({ ...draft, approval: result });
      } else if (result.status === 'ready') {
        onChange({ ...draft, candidates: result.candidates, generationId: result.generation_id,
          sourceFingerprint: fingerprint, selected: '', approval: undefined });
        if (result.candidates.length < 3) setMessage('Fewer than three valid options were returned. Choose one or regenerate.');
      } else {
        setMessage(result.message || 'No hooks available.');
      }
    } catch (error) {
      if (mounted.current && current.current === started) setMessage(error instanceof Error ? error.message : 'Hook request failed.');
    } finally { if (mounted.current) setBusy(false); }
  };
  const cls = 'w-full rounded bg-zinc-950 border border-zinc-700 px-2 py-1 text-xs text-zinc-200';
  return <div className="space-y-2 rounded border border-zinc-800 p-2 text-xs">
    <label className="flex items-center gap-2"><Checkbox checked={draft.enabled} onCheckedChange={enabled => update({ enabled })} />Generate opening hook</label>
    {draft.enabled && <>
      <p className="text-zinc-400">Your caption and the context below go to your configured Gemini provider. Media is not uploaded. Review facts before approving.</p>
      {(['description', 'audience', 'language', 'tone', 'avoid'] as const).map(field => <label key={field} className="block capitalize">{field === 'avoid' ? 'Words to avoid (comma-separated)' : field}
        <input aria-label={`Hook ${field}`} className={cls} value={draft[field] || ''} placeholder={field === 'language' ? 'Match caption' : field === 'tone' ? 'Friendly' : ''}
          onChange={event => update({ [field]: event.target.value, generationId: undefined, candidates: undefined })} />
      </label>)}
      <button type="button" className="rounded bg-blue-700 px-2 py-1 disabled:opacity-50" disabled={busy} onClick={() => request(false)}>{busy ? 'Working…' : 'Generate hooks'}</button>
      {draft.sourceFingerprint === fingerprint && draft.candidates?.map(candidate => <button key={candidate.id} type="button"
        className="block w-full rounded border border-zinc-700 p-2 text-left hover:border-blue-500" onClick={() => update({ selected: candidate.hook })}>{candidate.hook}</button>)}
      {draft.generationId && draft.sourceFingerprint !== fingerprint && <p role="status">Caption or media changed. Generate hooks again.</p>}
      {draft.generationId && draft.sourceFingerprint === fingerprint && <>
        <label className="block">Selected hook (editable)<textarea aria-label="Selected hook" className={cls} maxLength={300} rows={2} value={draft.selected || ''} onChange={e => update({ selected: e.target.value })} /></label>
        <p className="text-zinc-400">Final caption preview</p>
        <pre className="whitespace-pre-wrap break-words rounded bg-zinc-950 p-2 font-sans">{finalCaption}</pre>
        <button type="button" disabled={busy || !draft.selected?.trim() || approved} className="rounded bg-emerald-800 px-2 py-1 disabled:opacity-50" onClick={() => request(true)}>{approved ? 'Caption approved' : 'Approve this caption'}</button>
      </>}
      <button type="button" className="ml-2 underline" onClick={() => update({ enabled: false })}>Keep original caption</button>
      {message && <p role="status" className="text-amber-300">{message}</p>}
    </>}
  </div>;
}
