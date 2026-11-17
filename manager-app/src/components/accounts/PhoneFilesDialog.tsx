import React, { useCallback, useEffect, useRef, useState } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { CheckCircle2, Film, Image as ImageIcon, Loader2, Smartphone, Trash2, Upload, X, XCircle } from 'lucide-react';
import type { Profile } from '../../types/profile';
import { isDesktopApp } from '../../services/desktopEnvironment';
import { copyFileToPhone, copyPathToPhone, droppedPaths, fetchPhoneFiles, isPhoneFileType, PHONE_FILE_TYPES, PhoneFile, removePhoneFile } from '../../services/devices';

/** A picked or dropped File, or a dropped file's path when the web view gives only that (Linux). */
type Job = { key: string; name: string; size: number | null; source: File | string; state: 'selected' | 'waiting' | 'copying' | 'saved' | 'failed'; error?: string };

const sizeLabel = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB`
  : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

const dateLabel = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
};

let jobCounter = 0;

/** A picked or dropped File, or a dropped file's path. */
export type PhoneDrop = File | string;

/** Nothing is copied yet: the person picks every file first, then sends them together. */
function toJobs(list: ArrayLike<PhoneDrop>): Job[] {
  return Array.from(list).map(source => {
    const name = typeof source === 'string' ? source.split('/').pop() || source : source.name;
    const job = { key: `j${++jobCounter}`, name, size: typeof source === 'string' ? null : source.size, source };
    return isPhoneFileType(name) ? { ...job, state: 'selected' as const }
      : { ...job, state: 'failed' as const, error: 'Only JPEG, PNG and MP4 files can be copied.' };
  });
}

/** Copies photos and videos by hand into one phone account's gallery, for the person to use
 * on the phone themselves (profile picture, cover photo…). Nothing here is posted. */
export const PhoneFilesDialog: React.FC<{
  profile: Profile; onClose: () => void;
  /** Files dropped on the phone's screen, listed ready to send. */
  initialFiles?: PhoneDrop[];
}> = ({ profile, onClose, initialFiles }) => {
  const [files, setFiles] = useState<PhoneFile[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>(() => initialFiles ? toJobs(initialFiles) : []);
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  // Files go only to the account on screen; checked when the dialog opens and on Check again.
  const [onScreen, setOnScreen] = useState<boolean | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const running = useRef(false);
  const queue = useRef<Job[]>([]);
  const live = useRef(true);

  const reload = useCallback(() => {
    fetchPhoneFiles(profile.id)
      .then(next => { if (live.current) { setFiles(next.files); setOnScreen(next.on_screen); setLoadError(null); } })
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
          const saved = typeof job.source === 'string'
            ? await copyPathToPhone(profile.id, job.source) : await copyFileToPhone(profile.id, job.source);
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

  const [dropError, setDropError] = useState<string | null>(null);
  const add = (list: FileList | PhoneDrop[]) => {
    if (!list.length) return;
    setDropError(null);
    const next = toJobs(list);
    setJobs(current => [...current, ...next]);
  };

  // In the desktop app the window itself receives dropped files and reports their paths; the
  // web view is not given them (on Linux it gets neither the files nor their addresses).
  const addRef = useRef(add);
  addRef.current = add;
  useEffect(() => {
    if (!isDesktopApp()) return;
    let stop: (() => void) | undefined;
    let closed = false;
    void import('@tauri-apps/api/webview').then(({ getCurrentWebview }) => getCurrentWebview().onDragDropEvent(event => {
      const drag = event.payload;
      if (drag.type === 'enter' || drag.type === 'over') setDragging(true);
      else if (drag.type === 'leave') setDragging(false);
      else {
        setDragging(false);
        if (drag.paths.length) addRef.current(drag.paths);
      }
    })).then(unlisten => { if (closed) unlisten(); else stop = unlisten; }).catch(() => undefined);
    return () => { closed = true; stop?.(); };
  }, []);

  const selected = jobs.filter(job => job.state === 'selected');
  const send = () => {
    selected.forEach(job => patch(job.key, { state: 'waiting' }));
    queue.current.push(...selected);
    void drain();
  };
  const unselect = (key: string) => setJobs(list => list.filter(job => job.key !== key));

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

  // A Radix dialog, so it stacks correctly over the phone screen's own dialog.
  return (
    <DialogPrimitive.Root open onOpenChange={open => { if (!open) close(); }}>
      <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-[310] bg-black/80 backdrop-blur-sm" />
      <DialogPrimitive.Content aria-describedby={undefined} aria-label={`Files on phone for ${profile.name}`} data-phone-files-dialog=""
        onPointerDownOutside={e => e.preventDefault()} onInteractOutside={e => e.preventDefault()}
        className="fixed left-1/2 top-1/2 z-[311] flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl outline-none">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <DialogPrimitive.Title className="flex items-center gap-2 text-sm font-semibold text-zinc-100"><Smartphone className="h-4 w-4 text-zinc-400" />Send files to phone · {profile.name}</DialogPrimitive.Title>
          <button onClick={close} disabled={busy} aria-label="Close" title={busy ? 'Wait for the copy to finish.' : undefined}
            className="rounded p-1 text-zinc-400 hover:text-zinc-200 disabled:opacity-40"><X className="h-4 w-4" /></button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-5 text-xs">
          <button type="button" onClick={() => picker.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
            onDrop={e => {
              e.preventDefault();
              setDragging(false);
              // Linux's web view often hands dropped files over as file:// addresses, with no File objects.
              if (e.dataTransfer.files.length) add(e.dataTransfer.files);
              else {
                const paths = droppedPaths(e.dataTransfer);
                if (paths.length) add(paths);
                // The desktop app's own drop event brings these files; only a browser has nothing else.
                else if (!isDesktopApp()) setDropError('The dropped files couldn’t be read. Click the box to choose them instead.');
              }
            }}
            className={`flex flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-6 text-center transition-colors ${dragging ? 'border-blue-400 bg-blue-500/10' : 'border-zinc-700 bg-zinc-950 hover:border-zinc-500'}`}>
            <Upload className="h-5 w-5 text-zinc-400" />
            <span className="font-medium text-zinc-200">Drop photos or videos here, or click to browse</span>
            <span className="text-zinc-500">JPEG, PNG or MP4. They go to this account’s gallery only, ready for a profile picture, cover photo and so on.</span>
          </button>
          {dropError && <p role="alert" className="-mt-2 text-red-300">{dropError}</p>}
          <input ref={picker} type="file" multiple accept={PHONE_FILE_TYPES} className="hidden"
            onChange={e => { if (e.target.files) add(e.target.files); e.target.value = ''; }} />

          {jobs.length > 0 && <ul aria-label="Copies" className="divide-y divide-zinc-800/70 rounded-lg border border-zinc-800">
            {jobs.map(job => (
              <li key={job.key} className="flex items-center gap-3 px-3 py-2">
                {job.state === 'saved' ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                  : job.state === 'failed' ? <XCircle className="h-4 w-4 shrink-0 text-red-400" />
                  : job.state === 'selected' ? (/\.mp4$/i.test(job.name) ? <Film className="h-4 w-4 shrink-0 text-zinc-400" /> : <ImageIcon className="h-4 w-4 shrink-0 text-zinc-400" />)
                  : <Loader2 className={`h-4 w-4 shrink-0 text-zinc-400 ${job.state === 'copying' ? 'animate-spin' : 'opacity-40'}`} />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-zinc-200">{job.name}</span>
                  <span className={`block ${job.state === 'failed' ? 'text-red-300' : 'text-zinc-500'}`}>
                    {job.state === 'selected' ? `Ready to send${job.size != null ? ` · ${sizeLabel(job.size)}` : ''}`
                      : job.state === 'waiting' ? `Waiting${job.size != null ? ` · ${sizeLabel(job.size)}` : ''}`
                      : job.state === 'copying' ? `Copying to the phone${job.size != null ? ` · ${sizeLabel(job.size)}` : ''}`
                      : job.state === 'saved' ? 'Saved to the gallery' : job.error}
                  </span>
                </span>
                {job.state === 'failed' && isPhoneFileType(job.name) &&
                  <button onClick={() => retry(job)} className="rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-zinc-300 hover:bg-zinc-800">Retry</button>}
                {(job.state === 'selected' || (job.state === 'failed' && !isPhoneFileType(job.name))) &&
                  <button onClick={() => unselect(job.key)} aria-label={`Remove ${job.name} from the list`} title="Remove from the list"
                    className="rounded p-1 text-zinc-500 hover:text-zinc-200"><X className="h-3.5 w-3.5" /></button>}
              </li>
            ))}
          </ul>}
          {onScreen === false && <p role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-amber-200">
            <span className="min-w-0 flex-1">{profile.name} isn’t on the phone’s screen, so files can’t be sent or removed. Switch to it on the Devices page first.</span>
            <button type="button" onClick={reload} className="rounded-md border border-amber-900/60 px-2 py-1 hover:bg-amber-950/40">Check again</button>
          </p>}
          {selected.length > 0 && <div className="flex justify-end">
            <button type="button" onClick={send} disabled={onScreen === false}
              className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50">
              <Upload className="h-3.5 w-3.5" />Send {selected.length} file{selected.length === 1 ? '' : 's'} to phone
            </button>
          </div>}

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
                  <button onClick={() => void remove(file)} disabled={removing !== null || onScreen === false} aria-label={`Remove ${file.original_name || file.name} from the phone`}
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
      </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};
