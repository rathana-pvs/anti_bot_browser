import React, { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, Film, Image as ImageIcon, Loader2, Smartphone, Trash2, Upload, X, XCircle } from 'lucide-react';
import type { Profile } from '../../types/profile';
import { copyFileToPhone, fetchPhoneFiles, isPhoneFileType, PHONE_FILE_TYPES, PhoneFile, removePhoneFile } from '../../services/devices';

type Job = { key: string; file: File; state: 'waiting' | 'copying' | 'saved' | 'failed'; error?: string };

const sizeLabel = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
  : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

const dateLabel = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
};

let jobCounter = 0;

/** Copies photos and videos by hand into one phone account's gallery, for the person to use
 * on the phone themselves (profile picture, cover photo…). Nothing here is posted. */
export const PhoneFilesDialog: React.FC<{ profile: Profile; onClose: () => void }> = ({ profile, onClose }) => {
  const [files, setFiles] = useState<PhoneFile[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const running = useRef(false);
  const queue = useRef<Job[]>([]);
  const live = useRef(true);

  const reload = useCallback(() => {
    fetchPhoneFiles(profile.id)
      .then(next => { if (live.current) { setFiles(next); setLoadError(null); } })
      .catch(err => live.current && setLoadError(err.message || String(err)));
  }, [profile.id]);

  useEffect(() => {
    live.current = true;
    reload();
    return () => { live.current = false; };
  }, [reload]);

  const patch = (key: string, change: Partial<Job>) => setJobs(list => list.map(job => job.key === key ? { ...job, ...change } : job));

  // One file at a time: the phone saves each file before the next starts.
  const drain = async () => {
    if (running.current) return;
    running.current = true;
    try {
      while (queue.current.length) {
        const job = queue.current.shift()!;
        patch(job.key, { state: 'copying' });
        try {
          const saved = await copyFileToPhone(profile.id, job.file);
          patch(job.key, { state: 'saved' });
          if (live.current) setFiles(list => [saved, ...(list || [])]);
        } catch (err: any) {
          patch(job.key, { state: 'failed', error: err.message || String(err) });
        }
      }
    } finally {
      running.current = false;
    }
  };

  const add = (list: FileList | File[]) => {
    const picked = Array.from(list);
    if (!picked.length) return;
    const next: Job[] = picked.map(file => isPhoneFileType(file.name)
      ? { key: `j${++jobCounter}`, file, state: 'waiting' }
      : { key: `j${++jobCounter}`, file, state: 'failed', error: 'Only JPEG, PNG and MP4 files can be copied.' });
    setJobs(current => [...current, ...next]);
    queue.current.push(...next.filter(job => job.state === 'waiting'));
    void drain();
  };

  const retry = (job: Job) => {
    patch(job.key, { state: 'waiting', error: undefined });
    queue.current.push(job);
    void drain();
  };

  const remove = async (file: PhoneFile) => {
    setRemoving(file.id);
    setRemoveError(null);
    try {
      await removePhoneFile(profile.id, file.id);
      setFiles(list => (list || []).filter(item => item.id !== file.id));
    } catch (err: any) {
      setRemoveError(err.message || String(err));
    } finally {
      setRemoving(null);
    }
  };

  const busy = jobs.some(job => job.state === 'waiting' || job.state === 'copying');
  const close = () => { if (!busy) onClose(); };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div role="dialog" aria-modal="true" aria-label={`Files on phone for ${profile.name}`}
        className="flex max-h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100"><Smartphone className="h-4 w-4 text-zinc-400" />Send files to phone · {profile.name}</h2>
          <button onClick={close} disabled={busy} aria-label="Close" title={busy ? 'Wait for the copy to finish.' : undefined}
            className="rounded p-1 text-zinc-400 hover:text-zinc-200 disabled:opacity-40"><X className="h-4 w-4" /></button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-5 text-xs">
          <button type="button" onClick={() => picker.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
            onDrop={e => { e.preventDefault(); setDragging(false); add(e.dataTransfer.files); }}
            className={`flex flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-6 text-center transition-colors ${dragging ? 'border-blue-400 bg-blue-500/10' : 'border-zinc-700 bg-zinc-950 hover:border-zinc-500'}`}>
            <Upload className="h-5 w-5 text-zinc-400" />
            <span className="font-medium text-zinc-200">Drop photos or videos here, or click to browse</span>
            <span className="text-zinc-500">JPEG, PNG or MP4. They go to this account’s gallery only, ready for a profile picture, cover photo and so on.</span>
          </button>
          <input ref={picker} type="file" multiple accept={PHONE_FILE_TYPES} className="hidden"
            onChange={e => { if (e.target.files) add(e.target.files); e.target.value = ''; }} />

          {jobs.length > 0 && <ul aria-label="Copies" className="divide-y divide-zinc-800/70 rounded-lg border border-zinc-800">
            {jobs.map(job => (
              <li key={job.key} className="flex items-center gap-3 px-3 py-2">
                {job.state === 'saved' ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                  : job.state === 'failed' ? <XCircle className="h-4 w-4 shrink-0 text-red-400" />
                  : <Loader2 className={`h-4 w-4 shrink-0 text-zinc-400 ${job.state === 'copying' ? 'animate-spin' : 'opacity-40'}`} />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-zinc-200">{job.file.name}</span>
                  <span className={`block ${job.state === 'failed' ? 'text-red-300' : 'text-zinc-500'}`}>
                    {job.state === 'waiting' ? `Waiting · ${sizeLabel(job.file.size)}`
                      : job.state === 'copying' ? `Copying to the phone · ${sizeLabel(job.file.size)}`
                      : job.state === 'saved' ? 'Saved to the gallery' : job.error}
                  </span>
                </span>
                {job.state === 'failed' && isPhoneFileType(job.file.name) &&
                  <button onClick={() => retry(job)} className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-zinc-300 hover:bg-zinc-800">Retry</button>}
              </li>
            ))}
          </ul>}

          <section aria-label="Files on phone" className="flex flex-col gap-2">
            <h3 className="text-[10px] uppercase tracking-wide text-zinc-500">On the phone</h3>
            {loadError && <p role="alert" className="text-red-400">{loadError}</p>}
            {!files && !loadError && <p className="flex items-center gap-2 text-zinc-400"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading…</p>}
            {files && files.length === 0 && <p className="text-zinc-500">Nothing copied to this account yet.</p>}
            {files && files.length > 0 && <ul className="divide-y divide-zinc-800/70 rounded-lg border border-zinc-800">
              {files.map(file => (
                <li key={file.id} className="flex items-center gap-3 px-3 py-2">
                  {file.mime.startsWith('video/') ? <Film className="h-4 w-4 shrink-0 text-zinc-400" /> : <ImageIcon className="h-4 w-4 shrink-0 text-zinc-400" />}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-zinc-200" title={`Saved on the phone as ${file.album}/${file.name}`}>{file.original_name || file.name}</span>
                    <span className="block text-zinc-500">{sizeLabel(file.size_bytes)} · {file.album} · {dateLabel(file.copied_at)}</span>
                  </span>
                  <button onClick={() => void remove(file)} disabled={removing !== null} aria-label={`Remove ${file.original_name || file.name} from the phone`}
                    className="flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-zinc-300 hover:border-red-500/40 hover:text-red-300 disabled:opacity-50">
                    {removing === file.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}Remove
                  </button>
                </li>
              ))}
            </ul>}
            {removeError && <p role="alert" className="text-red-400">{removeError}</p>}
          </section>
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-3 text-xs">
          <span className="text-zinc-500">Files stay on the phone until you remove them. Posting never deletes them.</span>
          <button onClick={close} disabled={busy}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800 disabled:opacity-50">{busy ? 'Copying…' : 'Close'}</button>
        </div>
      </div>
    </div>
  );
};
