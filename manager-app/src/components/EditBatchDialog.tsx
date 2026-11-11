import { useEffect, useMemo, useState } from 'react';
import { Film, Image as ImageIcon, Loader2, Lock, Paperclip, PauseCircle } from 'lucide-react';
import {
  BatchChanges, BatchEditInfo, BatchPostChange, fetchBatchEditInfo, getSharedMediaUrl, updateBatch, uploadMediaFiles,
} from '../services/api';
import { DailyBatch, QueuePostItem } from '../types/automation';
import { BatchSchedule, defaultStart, startFrom, startProblem } from './BatchSchedule';
import { useAppDialog } from './ui/AppDialogProvider';
import { Button } from './ui/Button';
import { Checkbox } from './ui/Checkbox';
import { Dialog } from './ui/Dialog';
import { Input } from './ui/Input';
import { Select } from './ui/Select';
import { restLabel, restOptions } from './restChoices';
import { Textarea } from './ui/Textarea';

interface Props {
  batch: DailyBatch;
  profileName: (profileId: string) => string;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}

interface PostDraft { base_caption: string; first_comment: string; media_file: string; media_name: string; ai_spin: boolean }

const WARM_UP_OPTIONS = [
  { value: 'off', label: 'No warm-up' },
  { value: 'brief', label: 'Brief warm-up' },
  { value: 'extended', label: 'Longer warm-up' },
];
const isVideo = (name: string) => /\.(mp4|mov|webm)$/i.test(name);

const draftOf = (post: QueuePostItem): PostDraft => ({
  base_caption: post.base_caption || '', first_comment: post.first_comment || '', media_file: post.media_file || '',
  media_name: post.media_file || '', ai_spin: post.ai_spin !== false,
});

/** Edits a batch's jobs that haven't started: name, pacing, each post's content and when it
 * starts. Jobs already working, posted or unconfirmed keep what they ran with. */
export function EditBatchDialog({ batch, profileName, onClose, onSaved }: Props) {
  const { showToast } = useAppDialog();
  const [info, setInfo] = useState<BatchEditInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState(batch.name);
  const [drafts, setDrafts] = useState<Record<string, PostDraft>>({});
  const [rest, setRest] = useState(batch.schedule_window.batch_iteration_delay_seconds ?? 0);
  const [warmUp, setWarmUp] = useState(batch.schedule_window.session_preparation_mode || 'off');
  const [retime, setRetime] = useState(false);
  const [mode, setMode] = useState<'now' | 'scheduled'>('scheduled');
  const [follow, setFollow] = useState(batch.schedule_window.timing === 'auto');
  const [start, setStart] = useState(() => defaultStart(batch.schedule_window.start_time || '09:00'));
  const [spread, setSpread] = useState(batch.schedule_window.spread_minutes ?? 30);
  const [plan, setPlan] = useState<{ seed: number | null; problem: string | null }>({ seed: null, problem: null });
  const [uploading, setUploading] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchBatchEditInfo(batch.batch_id).then(next => {
      setInfo(next);
      setDrafts(Object.fromEntries(next.batch.posts.map(post => [post.post_id, draftOf(post)])));
    }).catch(err => setLoadError(err.message || String(err)));
  }, [batch.batch_id]);

  const changeable = useMemo(() => new Set(info?.changeable_ids || []), [info]);
  const posts = info?.batch.posts || [];
  // Accounts whose jobs will be timed again, and the most jobs one of them has.
  const { accounts, perAccount } = useMemo(() => {
    const counts = new Map<string, number>();
    posts.forEach(post => post.executions.forEach(job => {
      if (changeable.has(job.execution_id)) counts.set(job.profile_id, (counts.get(job.profile_id) || 0) + 1);
    }));
    return { accounts: [...counts.keys()], perAccount: Math.max(1, ...counts.values()) };
  }, [posts, changeable]);

  const postChanges: BatchPostChange[] = posts.flatMap(post => {
    const draft = drafts[post.post_id];
    if (!draft || info?.content_locked[post.post_id]) return [];
    const before = draftOf(post);
    const change: BatchPostChange = { post_id: post.post_id };
    if (draft.base_caption !== before.base_caption) change.base_caption = draft.base_caption;
    if (draft.first_comment.trim() !== before.first_comment.trim()) change.first_comment = draft.first_comment;
    if (draft.media_file !== before.media_file) change.media_file = draft.media_file;
    if (draft.ai_spin !== before.ai_spin) change.ai_spin = draft.ai_spin;
    return Object.keys(change).length > 1 ? [change] : [];
  });

  const timing = !retime ? null : mode === 'now' ? 'now' : follow ? 'auto' : 'exact';
  const summary = [
    name.trim() !== batch.name && 'new name',
    ...postChanges.map(change => `post ${posts.findIndex(p => p.post_id === change.post_id) + 1}: ${
      [change.base_caption !== undefined && 'caption', change.first_comment !== undefined && 'first comment',
        change.media_file !== undefined && 'media', change.ai_spin !== undefined && 'AI spin'].filter(Boolean).join(', ')}`),
    rest !== (batch.schedule_window.batch_iteration_delay_seconds ?? 0) && `rest between posts → ${restLabel(rest)}`,
    warmUp !== (batch.schedule_window.session_preparation_mode || 'off') && `warm-up → ${WARM_UP_OPTIONS.find(o => o.value === warmUp)?.label}`,
    timing === 'now' && 'start now',
    timing === 'auto' && 'new planned times (Follow active hours)',
    timing === 'exact' && startFrom(start.date, start.time) && `start ${startFrom(start.date, start.time)!.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}`,
  ].filter((line): line is string => !!line);

  const problem = !name.trim() ? 'Give the batch a name.'
    : posts.some(post => {
      const draft = drafts[post.post_id];
      return draft && !info?.content_locked[post.post_id] && !draft.media_file.trim() && !draft.base_caption.trim();
    }) ? 'Every post needs media or a caption.'
      : timing === 'exact' ? startProblem(start.date, start.time)
        : timing === 'auto' ? plan.problem : null;

  const replaceMedia = async (post: QueuePostItem, file: File) => {
    if (post.type === 'reel' && !isVideo(file.name)) { setError('A reel needs a video file.'); return; }
    setUploading(post.post_id);
    setError(null);
    try {
      const form = new FormData();
      form.append('files', file);
      const uploaded = (await uploadMediaFiles(form)).files[0];
      setDrafts(current => ({ ...current, [post.post_id]: { ...current[post.post_id], media_file: uploaded.filename, media_name: uploaded.original_name || uploaded.filename } }));
    } catch (err: any) { setError(err.message || 'Could not upload the file.'); }
    finally { setUploading(null); }
  };

  const save = async () => {
    if (problem || !summary.length) return;
    const changes: BatchChanges = { expected_ids: info?.changeable_ids || [] };
    if (name.trim() !== batch.name) changes.name = name.trim();
    if (postChanges.length) changes.posts = postChanges;
    if (rest !== (batch.schedule_window.batch_iteration_delay_seconds ?? 0)) changes.batch_iteration_delay_seconds = rest;
    if (warmUp !== (batch.schedule_window.session_preparation_mode || 'off')) changes.session_preparation_mode = warmUp as BatchChanges['session_preparation_mode'];
    if (timing === 'auto') changes.schedule_window = { timing, ...(plan.seed ? { plan_seed: plan.seed } : {}) };
    else if (timing === 'now') changes.schedule_window = { timing, spread_minutes: spread };
    else if (timing === 'exact') changes.schedule_window = { timing, start_at: startFrom(start.date, start.time)!.toISOString(), spread_minutes: spread };
    setSaving(true);
    setError(null);
    try {
      const result = await updateBatch(batch.batch_id, changes);
      const started = result.started_while_editing;
      showToast(result.changes.length ? `Saved · ${result.jobs_changed} job${result.jobs_changed === 1 ? '' : 's'} changed.` : 'Nothing to change.', 'success');
      if (started.length) {
        const names = [...new Set(started.map(s => s.profile_id ? profileName(s.profile_id) : 'An account'))].join(', ');
        showToast(`${names} started while you were editing and kept the old version.`, 'info');
      }
      await onSaved();
      onClose();
    } catch (err: any) { setError(err.message || 'Could not save the batch.'); }
    finally { setSaving(false); }
  };

  const total = info?.total ?? 0;
  const started = total - (info?.changeable ?? 0);

  return <Dialog open onOpenChange={open => { if (!open && !saving) onClose(); }} title={`Edit ${batch.name}`}
    description="Changes apply only to jobs that haven't started. Jobs already working or posted keep what they had."
    className="max-w-2xl"
    footer={<>
      <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
      <Button onClick={save} loading={saving} disabled={!info || !!problem || !summary.length}>Save changes</Button>
    </>}>
    <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1 text-xs">
      {loadError && <p role="alert" className="text-red-400">{loadError}</p>}
      {!info && !loadError && <p className="flex items-center gap-2 text-zinc-500"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading the batch…</p>}
      {info && <>
        <p className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-zinc-300">
          <strong className="text-white">{info.changeable} of {total}</strong> job{total === 1 ? '' : 's'} can still change
          {started > 0 && <> · {started} already started or finished</>}
          {batch.paused && <span className="ml-2 inline-flex items-center gap-1 text-amber-300"><PauseCircle className="h-3.5 w-3.5" />Paused</span>}
        </p>
        {!batch.paused && info.changeable > 0 && <p className="text-[11px] text-zinc-500">
          The batch keeps running while you edit. A job that starts before you save keeps the old version. Pause the batch first for a big change.
        </p>}

        <label className="block space-y-1"><span className="text-zinc-400">Batch name</span>
          <Input value={name} onChange={e => setName(e.target.value)} maxLength={120} /></label>

        <section className="space-y-2">
          <h4 className="font-medium text-zinc-200">Posts</h4>
          {posts.map((post, index) => {
            const draft = drafts[post.post_id];
            const locked = info.content_locked[post.post_id];
            const waiting = post.executions.filter(job => changeable.has(job.execution_id)).length;
            return <div key={post.post_id} className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
              <div className="flex items-center gap-2 text-zinc-300">
                <span className="font-mono text-[10px] text-zinc-500">#{index + 1}</span>
                {post.type === 'reel' ? <Film className="h-3.5 w-3.5 text-purple-400" /> : <ImageIcon className="h-3.5 w-3.5 text-blue-400" />}
                <span className="capitalize">{post.type === 'warming' ? 'Warm-up' : post.type}</span>
                <span className="ml-auto text-[11px] text-zinc-500">{waiting} of {post.executions.length} account{post.executions.length === 1 ? '' : 's'} still to post</span>
              </div>
              {locked ? <p className="flex items-center gap-1.5 text-[11px] text-zinc-500"><Lock className="h-3 w-3" />{locked}</p>
                : waiting === 0 ? <p className="text-[11px] text-zinc-500">Every account has already started this post.</p>
                  : draft && <>
                    <div className="flex items-center gap-2">
                      {draft.media_file && !isVideo(draft.media_file) && <img src={getSharedMediaUrl(draft.media_file)} alt="" className="h-12 w-12 rounded object-cover" />}
                      <span className="min-w-0 flex-1 truncate text-zinc-400">{draft.media_name || 'No media · text post'}</span>
                      <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 hover:border-zinc-600">
                        {uploading === post.post_id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Paperclip className="h-3 w-3" />}Replace media
                        <input type="file" className="hidden" accept={post.type === 'reel' ? 'video/*' : 'image/*,video/*'} disabled={!!uploading}
                          onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void replaceMedia(post, file); }} />
                      </label>
                    </div>
                    <Textarea rows={3} value={draft.base_caption} placeholder="Caption"
                      onChange={e => setDrafts(d => ({ ...d, [post.post_id]: { ...d[post.post_id], base_caption: e.target.value } }))} />
                    <Input value={draft.first_comment} placeholder="First comment (optional)"
                      onChange={e => setDrafts(d => ({ ...d, [post.post_id]: { ...d[post.post_id], first_comment: e.target.value } }))} />
                    <label className="flex items-center gap-2 text-zinc-400">
                      <Checkbox checked={draft.ai_spin} onCheckedChange={on => setDrafts(d => ({ ...d, [post.post_id]: { ...d[post.post_id], ai_spin: on } }))} />
                      AI spin · each account gets its own wording
                    </label>
                  </>}
            </div>;
          })}
        </section>

        <section className="space-y-2">
          <label className="flex items-center gap-2 font-medium text-zinc-200">
            <Checkbox checked={retime} disabled={!accounts.length} onCheckedChange={setRetime} />Change when it starts
          </label>
          {!accounts.length && <p className="text-[11px] text-zinc-500">No job is waiting, so there is nothing to retime.</p>}
          {retime && <BatchSchedule profileIds={accounts} postsPerAccount={perAccount} mode={mode} onMode={setMode}
            date={start.date} time={start.time} onStart={(date, time) => setStart({ date, time })}
            spread={spread} onSpread={setSpread} follow={follow} onFollow={setFollow}
            onPlan={(seed, problem) => setPlan({ seed, problem })} excludeBatchId={batch.batch_id} />}
        </section>

        <section className="grid grid-cols-2 gap-3">
          <label className="space-y-1"><span className="block text-zinc-400">Rest between an account's posts</span>
            <Select value={String(rest)} onValueChange={v => setRest(Number(v))}
              options={restOptions(rest)} /></label>
          <label className="space-y-1"><span className="block text-zinc-400">Warm-up before posting</span>
            <Select value={warmUp} onValueChange={v => setWarmUp(v as typeof warmUp)} options={WARM_UP_OPTIONS} /></label>
        </section>

        {summary.length > 0 && <div className="rounded-lg border border-blue-900/60 bg-blue-950/30 px-3 py-2 text-blue-100">
          <p className="font-medium">Will change</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-blue-200/90">{summary.map(line => <li key={line}>{line}</li>)}</ul>
        </div>}
        {(error || problem) && <p role="alert" className="text-red-400">{error || problem}</p>}
      </>}
    </div>
  </Dialog>;
}
