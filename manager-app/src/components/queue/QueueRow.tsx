import { ExternalLink, Eye, Film, Flame, Image as ImageIcon, Loader2, MessageCircle, Play, Radio, RotateCcw, ShieldAlert, Square, Type } from 'lucide-react';
import { QueueExecutionItem } from '../../types/automation';
import { getSharedMediaUrl } from '../../services/api';
import { NetworkRecoveryStatus } from '../NetworkRecoveryStatus';
import { Button } from '../ui/Button';
import { cn } from '../ui/cn';
import {
  ACTIVE_STATUSES, FAILED_STATUSES, UNCERTAIN_STATUSES, commentNeedsRetry, describe, formatTime,
  hasOpenRecovery, isVideoFile, itemKind, kindLabel, statusLabel,
} from './queueModel';

export interface QueueRowActions {
  open: (item: QueueExecutionItem) => void;
  runNow: (item: QueueExecutionItem) => void;
  retryComment: (item: QueueExecutionItem) => void;
  stopLive: (item: QueueExecutionItem) => void;
  watch: (item: QueueExecutionItem) => void;
  preview: (item: QueueExecutionItem) => void;
}

const KIND_ICON = { photo: ImageIcon, reel: Film, live: Radio, warming: Flame } as const;

export function QueueThumb({ item, onPreview, size = 'sm' }: { item: QueueExecutionItem; onPreview?: () => void; size?: 'sm' | 'lg' }) {
  const box = size === 'lg' ? 'w-full aspect-[4/5] rounded-xl' : 'w-10 h-10 rounded-lg';
  const kind = itemKind(item);
  if (!item.media_file) {
    const Icon = kind === 'photo' ? Type : KIND_ICON[kind];
    return <div className={cn(box, 'shrink-0 flex items-center justify-center border border-zinc-800 bg-zinc-900/60',
      kind === 'warming' ? 'text-orange-400' : 'text-zinc-500')}><Icon className="w-4 h-4" /></div>;
  }
  const video = kind === 'reel' || kind === 'live' || isVideoFile(item.media_file);
  const url = getSharedMediaUrl(item.media_file);
  return <button type="button" onClick={(e) => { e.stopPropagation(); onPreview?.(); }} aria-label={`Preview ${item.media_file}`}
    className={cn(box, 'relative shrink-0 overflow-hidden border border-zinc-800 bg-black hover:border-blue-500 transition-colors')}>
    {video ? <>
      <video src={url} className="w-full h-full object-cover opacity-80" preload={size === 'lg' ? 'metadata' : 'none'} />
      <span className="absolute inset-0 flex items-center justify-center bg-black/30"><Play className="w-4 h-4 text-white fill-white" /></span>
    </> : <img src={url} alt="" loading="lazy" className="w-full h-full object-cover" onError={(e) => { (e.target as HTMLElement).style.display = 'none'; }} />}
  </button>;
}


export function QueueRow({ item, profileName, selected, actions, busy, onContextMenu, onMenuKey }: {
  item: QueueExecutionItem; profileName: string; selected: boolean; actions: QueueRowActions; busy?: boolean;
  onContextMenu?: (event: React.MouseEvent) => void; onMenuKey?: (event: React.KeyboardEvent) => void;
}) {
  const kind = itemKind(item);
  const KindIcon = KIND_ICON[kind];
  const active = ACTIVE_STATUSES.has(item.status);
  const failed = FAILED_STATUSES.has(item.status);
  const uncertain = UNCERTAIN_STATUSES.has(item.status);
  const timeTone = active ? 'text-blue-300' : uncertain || hasOpenRecovery(item) ? 'text-amber-300' : failed ? 'text-red-300' : 'text-zinc-300';

  let action: React.ReactNode = null;
  if (uncertain) {
    action = <Button size="sm" variant="secondary" className="bg-amber-950 text-amber-200 hover:bg-amber-900" onClick={() => actions.open(item)}>
      <ShieldAlert className="w-3.5 h-3.5" />{kind === 'live' ? 'Confirm outcome' : 'Was it posted?'}</Button>;
  } else if (item.status === 'failed_after_publish') {
    action = <Button size="sm" variant="outline" onClick={() => actions.open(item)}>Check</Button>;
  } else if (failed) {
    action = <Button size="sm" variant="outline" onClick={() => actions.runNow(item)} loading={busy}><RotateCcw className="w-3.5 h-3.5" />Retry</Button>;
  } else if (item.status === 'published' && commentNeedsRetry(item)) {
    action = <Button size="sm" variant="outline" onClick={() => actions.retryComment(item)} loading={busy || item.comment_retry_status === 'running'}>
      <MessageCircle className="w-3.5 h-3.5" />Retry comment</Button>;
  } else if (kind === 'live' && item.status === 'running') {
    action = <Button size="sm" variant="outline" className="border-rose-800 text-rose-200" onClick={() => actions.stopLive(item)}
      loading={busy} disabled={['stopping', 'ending', 'ended'].includes(item.stage || '')}><Square className="w-3 h-3" />Stop Live</Button>;
  } else if (active) {
    action = <Button size="sm" variant="ghost" onClick={() => actions.watch(item)}><Eye className="w-3.5 h-3.5" />Watch</Button>;
  } else if (item.status === 'pending') {
    action = <Button size="sm" variant="ghost" onClick={() => actions.runNow(item)} loading={busy}><Play className="w-3 h-3" />Run now</Button>;
  } else if (item.status === 'ready') {
    action = <span className="rounded-full bg-cyan-950 px-2 py-0.5 text-[10px] font-semibold text-cyan-300" title="Warm-up done; waiting for a publishing slot.">Prepared</span>;
  } else if (item.post_url) {
    action = <a href={item.post_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
      className="inline-flex items-center gap-1 rounded-control px-2.5 py-1.5 text-xs text-blue-300 hover:bg-elevated"><ExternalLink className="w-3.5 h-3.5" />View</a>;
  } else if (item.status.startsWith('skipped')) {
    action = <Button size="sm" variant="ghost" onClick={() => actions.runNow(item)} loading={busy}><RotateCcw className="w-3.5 h-3.5" />Re-run</Button>;
  } else {
    action = <span className="text-[11px] text-zinc-500">{statusLabel(item.status)}</span>;
  }

  return <div tabIndex={0} aria-current={selected || undefined} aria-label={`${profileName}, ${kindLabel(item)}, ${statusLabel(item.status)}`}
    onClick={() => actions.open(item)}
    onContextMenu={onContextMenu}
    onKeyDown={(e) => {
      onMenuKey?.(e);
      if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); actions.open(item); }
    }}
    className={cn('grid cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 grid-cols-[4.5rem_2.5rem_minmax(0,1fr)_auto] items-center gap-3 px-3 py-2.5 transition-colors',
      selected ? 'bg-blue-950/30' : 'hover:bg-zinc-900/60')}>
    <span className={cn('whitespace-nowrap font-mono text-xs', timeTone)}>{active ? 'now' : formatTime(item.published_at || item.scheduled_at)}</span>
    <QueueThumb item={item} onPreview={() => actions.preview(item)} />
    <div className="min-w-0">
      <div className="flex min-w-0 items-center gap-1.5 text-xs">
        <span className="truncate font-medium text-zinc-100">{profileName}</span>
        <span className="flex shrink-0 items-center gap-1 text-zinc-500"><KindIcon className="w-3 h-3" />{kindLabel(item)}</span>
        {item.batch_name && <span className="hidden truncate text-zinc-600 md:inline">· {item.batch_name}</span>}
      </div>
      <p className={cn('mt-0.5 truncate text-xs', failed ? 'text-red-300/90' : uncertain ? 'text-amber-200/90' : 'text-zinc-400')}>
        {active && <Loader2 className="mr-1 inline w-3 h-3 animate-spin text-blue-400" />}{describe(item)}
      </p>
      {hasOpenRecovery(item) && <NetworkRecoveryStatus recovery={item.network_recovery} />}
    </div>
    <div className="flex shrink-0 items-center" onClick={(e) => e.stopPropagation()}>{action}</div>
  </div>;
}
