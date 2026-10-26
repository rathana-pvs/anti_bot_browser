import { Film, Image as ImageIcon, X } from 'lucide-react';

export interface LightboxMedia {
  url: string;
  type: 'photo' | 'reel';
  name: string;
  caption?: string;
  comment?: string;
}

export function MediaLightbox({ media, onClose }: { media: LightboxMedia | null; onClose: () => void }) {
  if (!media) return null;
  return (
    <div className="fixed inset-0 z-[60] bg-black/85 backdrop-blur-md flex items-center justify-center p-4 sm:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`Preview ${media.name}`}
        className="relative max-w-4xl w-full max-h-[90vh] bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between p-3.5 border-b border-zinc-800 bg-zinc-900/80">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className={`px-2.5 py-0.5 rounded text-[11px] font-semibold flex items-center gap-1.5 border ${media.type === 'reel'
              ? 'bg-purple-950 text-purple-300 border-purple-800/80' : 'bg-blue-950 text-blue-300 border-blue-800/80'}`}>
              {media.type === 'reel' ? <Film className="w-3.5 h-3.5" /> : <ImageIcon className="w-3.5 h-3.5" />}
              {media.type === 'reel' ? 'Video' : 'Photo'}
            </span>
            <span className="text-xs text-zinc-300 font-mono truncate">{media.name}</span>
          </div>
          <button type="button" onClick={onClose} aria-label="Close preview"
            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 flex items-center justify-center bg-black/95 p-4 overflow-hidden min-h-[350px]">
          {media.type === 'reel'
            ? <video src={media.url} controls autoPlay className="max-h-[65vh] max-w-full rounded-lg" />
            : <img src={media.url} alt={media.name} className="max-h-[65vh] max-w-full object-contain rounded-lg" />}
        </div>
        {(media.caption || media.comment) && (
          <div className="p-3.5 border-t border-zinc-800 bg-zinc-900/80 space-y-1.5 text-xs">
            {media.caption && <p className="text-zinc-200"><span className="text-zinc-400">Caption: </span>{media.caption}</p>}
            {media.comment && <p className="text-blue-400 text-[11px]"><span className="text-zinc-400">First comment: </span>{media.comment}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
