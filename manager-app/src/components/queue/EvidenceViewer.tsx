import { ReactNode, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { QueueExecutionItem } from '../../types/automation';
import { fetchExecutionEvidence, fetchExecutionEvidenceImage } from '../../services/api';
import { IconButton } from '../ui/IconButton';

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

  // Stay on the newest screenshot unless the user stepped back to an earlier one.
  const followLatest = useRef(true);
  useEffect(() => { setImages(null); setIndex(0); followLatest.current = true; }, [item.execution_id]);

  // New screenshots appear as a running item advances, so reload when its stage changes.
  useEffect(() => {
    let cancelled = false;
    fetchExecutionEvidence(item.execution_id)
      .then(names => {
        if (cancelled) return;
        setImages(names);
        setIndex(current => followLatest.current ? Math.max(0, names.length - 1) : Math.min(current, Math.max(0, names.length - 1)));
      })
      .catch(() => { if (!cancelled) setImages([]); });
    return () => { cancelled = true; };
  }, [item.execution_id, item.status, item.stage, item.evidence_dir]);

  const go = (next: number) => {
    setIndex(next);
    followLatest.current = next === (images?.length || 0) - 1;
  };

  const name = images?.[index];
  useEffect(() => {
    if (!name) { setUrl(null); return; }
    let objectUrl: string | null = null;
    let cancelled = false;
    setFailed(false);
    setUrl(null);
    fetchExecutionEvidenceImage(item.execution_id, name)
      .then(next => { if (cancelled) URL.revokeObjectURL(next); else { objectUrl = next; setUrl(next); } })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [item.execution_id, name]);

  if (images === null) return <div className="flex aspect-video items-center justify-center rounded-xl border border-zinc-800 bg-black/40"><Loader2 className="h-4 w-4 animate-spin text-zinc-500" /></div>;
  if (!images.length || !name) return <>{fallback}</>;

  return <figure className="space-y-1.5">
    <button type="button" onClick={() => url && onOpen(url, evidenceLabel(name))} aria-label={`Open screenshot: ${evidenceLabel(name)}`}
      className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-xl border border-zinc-800 bg-black hover:border-blue-600">
      {url ? <img src={url} alt={evidenceLabel(name)} className="h-full w-full object-contain" />
        : failed ? <span className="text-[11px] text-zinc-500">Couldn’t load this screenshot</span>
          : <Loader2 className="h-4 w-4 animate-spin text-zinc-500" />}
    </button>
    <figcaption className="flex items-center gap-1 text-[11px] text-zinc-400">
      <IconButton aria-label="Previous screenshot" className="h-6 w-6" disabled={index === 0} onClick={() => go(index - 1)}><ChevronLeft className="h-3.5 w-3.5" /></IconButton>
      <span className="min-w-0 flex-1 truncate text-center">{evidenceLabel(name)} <span className="text-zinc-600">· {index + 1} of {images.length}</span></span>
      <IconButton aria-label="Next screenshot" className="h-6 w-6" disabled={index === images.length - 1} onClick={() => go(index + 1)}><ChevronRight className="h-3.5 w-3.5" /></IconButton>
    </figcaption>
  </figure>;
}
