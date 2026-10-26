import { useEffect, useState } from 'react';
import { ImageOff, ImagePlus } from 'lucide-react';
import { reelApi } from '../../services/reelCreator';

const cache = new Map<string, string>();

/** Small preview of an imported photo or logo; fetched once per session. */
export function Thumb({ id, className = '' }: { id: string | null; className?: string }) {
  const [url, setUrl] = useState(id ? cache.get(id) ?? '' : '');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
    if (!id) { setUrl(''); return; }
    const known = cache.get(id);
    if (known) { setUrl(known); return; }
    let cancelled = false;
    void reelApi.thumbnail(id).then(blob => {
      const next = URL.createObjectURL(blob);
      cache.set(id, next);
      if (!cancelled) setUrl(next);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [id]);
  if (url) return <img src={url} alt="" className={`object-cover ${className}`} draggable={false} />;
  return <div className={`flex items-center justify-center bg-zinc-900 text-zinc-600 ${className}`}>{failed ? <ImageOff className="h-4 w-4" aria-hidden="true" /> : !id ? <ImagePlus className="h-4 w-4" aria-hidden="true" /> : null}</div>;
}
