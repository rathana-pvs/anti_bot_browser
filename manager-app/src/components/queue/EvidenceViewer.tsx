import { ReactNode, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { QueueExecutionItem } from '../../types/automation';
import { EvidenceDiagnostics, fetchExecutionEvidenceReport, fetchExecutionEvidenceImage } from '../../services/api';
import { IconButton } from '../ui/IconButton';
import { Button } from '../ui/Button';

/** `03_post_clicked.png` → `Post clicked` */
export const evidenceLabel = (filename: string) => {
  const text = filename.replace(/\.png$/i, '').replace(/^\d+_/, '').replace(/[_-]+/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : filename;
};

/** Shows the run's screenshots, newest first, so the user can see what the browser actually did. */
export function EvidenceViewer({ item, fallback, onOpen }: {
  item: QueueExecutionItem; fallback?: ReactNode; onOpen: (url: string, label: string) => void;
}) {
  const [images, setImages] = useState<string[] | null>(null);
  const [index, setIndex] = useState(0);
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [listError, setListError] = useState(false);
  const [reload, setReload] = useState(0);
  const [diagnostics, setDiagnostics] = useState<EvidenceDiagnostics>();

  // Stay on the newest screenshot unless the user stepped back to an earlier one.
  const followLatest = useRef(true);
  useEffect(() => { setImages(null); setIndex(0); followLatest.current = true; }, [item.execution_id]);

  // New screenshots appear as a running item advances, so reload when its stage changes.
  useEffect(() => {
    setListError(false);
    let cancelled = false;
    const controller = new AbortController();
    setImages(null);
    setDiagnostics(undefined);
    fetchExecutionEvidenceReport(item.execution_id, controller.signal)
      .then(({ images: names, diagnostics: report }) => {
        if (cancelled) return;
        setDiagnostics(report);
        setImages(names);
        setIndex(current => followLatest.current ? Math.max(0, names.length - 1) : Math.min(current, Math.max(0, names.length - 1)));
      })
      .catch(() => { if (!cancelled) { setImages([]); setListError(true); } });
    return () => { cancelled = true; controller.abort(); };
  }, [item.execution_id, item.status, item.stage, item.evidence_dir, reload]);

  const go = (next: number) => {
    setIndex(next);
    followLatest.current = next === (images?.length || 0) - 1;
  };

  const name = images?.[index];
  useEffect(() => {
    if (!name) { setUrl(null); return; }
    let objectUrl: string | null = null;
    let cancelled = false;
    const controller = new AbortController();
    setFailed(false);
    setUrl(null);
    fetchExecutionEvidenceImage(item.execution_id, name, controller.signal)
      .then(next => { if (cancelled) URL.revokeObjectURL(next); else { objectUrl = next; setUrl(next); } })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [item.execution_id, name, reload]);

  if (images === null) return <div role="status" aria-label="Loading evidence" className="flex aspect-video items-center justify-center rounded-xl border border-zinc-800 bg-black/40"><Loader2 className="h-4 w-4 animate-spin text-zinc-500" /></div>;
  if (listError) return <div role="alert" className="space-y-2 rounded-xl border border-zinc-800 p-4 text-xs text-zinc-400">
    <p>Couldn’t load evidence screenshots. The request failed or timed out.</p>
    <Button size="sm" variant="outline" onClick={() => setReload(current => current + 1)}>Retry evidence</Button>
  </div>;
  const diagnosticSummary = diagnostics && !['completed', 'published'].includes(item.status) && <div className="space-y-1 rounded-xl border border-zinc-800 p-3 text-xs text-zinc-400">
    {diagnostics.failure_stage && <p>Failure stage: {diagnostics.failure_stage.replace(/_/g, ' ')}</p>}
    {diagnostics.failure_code && <p>Failure reason: {diagnostics.failure_code.replace(/_/g, ' ')}</p>}
    {diagnostics.cleanup_failure_code && <p>Cleanup failure: {diagnostics.cleanup_failure_code.replace(/_/g, ' ')}</p>}
    {diagnostics.encoder_exit_code !== undefined && <p>Video sender exit code: {diagnostics.encoder_exit_code}</p>}
    {diagnostics.encoder_failure_reason && <p>Sender failure: {diagnostics.encoder_failure_reason.replace(/_/g, ' ')}</p>}
    {diagnostics.final_browser_state && <p>Facebook state: {diagnostics.final_browser_state}</p>}
  </div>;
  if (!images.length || !name) return <>
    <p role="status" className="rounded-xl border border-zinc-800 p-4 text-xs text-zinc-500">{diagnostics ? 'A diagnostic report was saved. No browser screenshot was available for this failure.' : 'No evidence screenshots are attached to this attempt.'}</p>
    {diagnosticSummary}
    {fallback}
  </>;

  return <figure className="space-y-1.5">
    {diagnosticSummary}
    <button type="button" onClick={() => url && onOpen(url, evidenceLabel(name))} aria-label={`Open screenshot: ${evidenceLabel(name)}`}
      className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-xl border border-zinc-800 bg-black hover:border-blue-600">
      {url ? <img src={url} alt={evidenceLabel(name)} className="h-full w-full object-contain" onError={() => { setFailed(true); setUrl(null); }} />
        : failed ? <span className="text-[11px] text-zinc-500">Couldn’t load this screenshot</span>
          : <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />}
    </button>
    {failed && <Button size="sm" variant="outline" onClick={() => setReload(current => current + 1)}>Retry screenshot</Button>}
    <figcaption className="flex items-center gap-1 text-[11px] text-zinc-400">
      <IconButton aria-label="Previous screenshot" className="h-6 w-6" disabled={index === 0} onClick={() => go(index - 1)}><ChevronLeft className="h-3.5 w-3.5" /></IconButton>
      <span className="min-w-0 flex-1 truncate text-center">{evidenceLabel(name)} <span className="text-zinc-600">· {index + 1} of {images.length}</span></span>
      <IconButton aria-label="Next screenshot" className="h-6 w-6" disabled={index === images.length - 1} onClick={() => go(index + 1)}><ChevronRight className="h-3.5 w-3.5" /></IconButton>
    </figcaption>
  </figure>;
}
