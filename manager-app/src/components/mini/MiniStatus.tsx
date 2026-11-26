import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ChevronDown, ChevronUp, ExternalLink, Eye, Monitor, PanelRightClose, Pause, Pin, PinOff, Play, Smartphone, X,
} from 'lucide-react';
import { fetchProfiles, fetchQueue, setBatchPaused } from '../../services/api';
import { setVisibleInterval } from '../../services/visibleInterval';
import type { Profile } from '../../types/profile';
import type { QueueDataResponse, QueueExecutionItem } from '../../types/automation';
import { describe, kindLabel } from '../queue/queueModel';
import { isAndroid } from '../accounts/accountModel';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { cn } from '../ui/cn';
import { LiveTarget, liveTargetOf } from '../live/liveWindows';
import { liveProgress, shortDuration, startedAt, startsIn, summarize } from './miniStatusModel';

const QUEUE_POLL_MS = 3000;
const PROFILE_POLL_MS = 30000;

/** The queue (one day of finished history) and account names, refreshed while the panel shows. */
function useMiniStatusData() {
  const [queue, setQueue] = useState<QueueDataResponse | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [offline, setOffline] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const loadQueue = () => fetchQueue(1)
      .then(data => { if (!cancelled) { setQueue(data); setOffline(false); } })
      .catch(() => { if (!cancelled) setOffline(true); });
    const loadProfiles = () => fetchProfiles().then(list => { if (!cancelled) setProfiles(list); }).catch(() => undefined);
    loadQueue();
    loadProfiles();
    const stopQueue = setVisibleInterval(loadQueue, QUEUE_POLL_MS);
    const stopProfiles = setVisibleInterval(loadProfiles, PROFILE_POLL_MS);
    return () => { cancelled = true; stopQueue(); stopProfiles(); };
  }, [reloadKey]);
  return { queue, profiles, offline, reload: () => setReloadKey(key => key + 1) };
}

/** Re-renders every second so elapsed times move between polls. */
function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

export interface MiniStatusProps {
  /** `docked` sits inside the main window; `window` fills the floating window. */
  mode: 'docked' | 'window';
  onOpenQueue: (executionId?: string, profileId?: string) => void;
  /** Opens the account's live screen (its own window on desktop). */
  onOpenLive: (target: LiveTarget) => void;
  /** Window only: bring the main window forward where it was. */
  onOpenApp?: () => void;
  /** Docked: move the panel into its own window. */
  onPopOut?: () => void;
  /** Docked: hide the panel. Window: dock it back into the main window. */
  onClose: () => void;
  /** Window only: keep the window above other apps. */
  onTop?: boolean;
  onToggleOnTop?: () => void;
}

export function MiniStatus({ mode, onOpenQueue, onOpenLive, onOpenApp, onPopOut, onClose, onTop, onToggleOnTop }: MiniStatusProps) {
  const { queue, profiles, offline, reload } = useMiniStatusData();
  const now = useNow();
  const [collapsed, setCollapsed] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const summary = useMemo(() => queue ? summarize(queue) : null, [queue]);
  const byId = useMemo(() => new Map(profiles.map(profile => [profile.id, profile])), [profiles]);

  const accountName = useCallback((item: QueueExecutionItem) => byId.get(item.profile_id)?.name || item.profile_id, [byId]);
  const onPhone = (item: QueueExecutionItem) => {
    const profile = byId.get(item.profile_id);
    return profile ? isAndroid(profile) : false;
  };

  const allPaused = !!summary && summary.activeBatchIds.length === 0 && summary.pausedBatchIds.length > 0;
  const canPause = !!summary && summary.activeBatchIds.length > 0;
  const togglePause = async () => {
    if (!summary) return;
    const ids = allPaused ? summary.pausedBatchIds : summary.activeBatchIds;
    setPausing(true);
    setActionError(null);
    const results = await Promise.allSettled(ids.map(id => setBatchPaused(id, !allPaused)));
    setPausing(false);
    if (results.some(result => result.status === 'rejected')) {
      setActionError(allPaused ? 'Some batches didn’t resume. Try again.' : 'Some batches didn’t pause. Try again.');
    }
    reload();
  };

  const headline = offline ? 'Can’t reach the worker'
    : !summary ? 'Loading…'
      : summary.running.length > 0 ? `${summary.running.length} running`
        : allPaused ? 'Queue paused'
          : summary.waiting > 0 ? 'Waiting for next job' : 'Idle';
  const dot = offline ? 'bg-danger'
    : summary?.attention.length ? 'bg-warning'
      : summary?.running.length ? 'bg-success' : 'bg-zinc-500';

  // The floating window follows the panel's height, so collapsing shrinks it to the title bar.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (mode !== 'window' || !rootRef.current) return;
    const element = rootRef.current;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        void import('@tauri-apps/api/core')
          .then(({ invoke }) => invoke('set_mini_height', { height: element.offsetHeight }))
          .catch(() => undefined);
      });
    });
    observer.observe(element);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [mode]);

  const stat = (label: string, value: number | string, tone?: 'warning', onClick?: () => void) => (
    <button type="button" onClick={onClick} disabled={!onClick}
      className={cn('rounded-control px-1 py-1.5 text-center transition-colors enabled:hover:bg-zinc-700/60 disabled:cursor-default',
        tone === 'warning' ? 'bg-warning/15 text-warning' : 'bg-elevated text-foreground')}>
      <div className="text-base font-semibold leading-5">{value}</div>
      <div className={cn('text-[10px]', tone === 'warning' ? 'text-warning' : 'text-muted')}>{label}</div>
    </button>
  );

  return <div ref={rootRef} role="region" aria-label="Worker status"
    className={cn('flex w-full flex-col overflow-hidden bg-surface text-xs text-foreground select-none',
      mode === 'docked' ? 'rounded-panel border border-border shadow-panel' : 'border border-border')}>
    <div data-tauri-drag-region={mode === 'window' ? true : undefined}
      className={cn('flex items-center gap-2 py-1 pl-3 pr-1', mode === 'window' && 'cursor-grab active:cursor-grabbing')}>
      <span aria-hidden="true" className={cn('h-2 w-2 shrink-0 rounded-full', dot)} data-tauri-drag-region={mode === 'window' ? true : undefined} />
      <span className="min-w-0 flex-1 truncate font-semibold" data-tauri-drag-region={mode === 'window' ? true : undefined}>{headline}</span>
      {mode === 'window' && onToggleOnTop && (
        <IconButton aria-label={onTop ? 'Stop keeping on top' : 'Keep on top'} title={onTop ? 'Stop keeping on top' : 'Keep on top'}
          className="h-7 w-7" onClick={onToggleOnTop}>
          {onTop ? <Pin className="h-3.5 w-3.5" /> : <PinOff className="h-3.5 w-3.5" />}
        </IconButton>
      )}
      {mode === 'docked' && onPopOut && (
        <IconButton aria-label="Open in its own window" title="Open in its own window" className="h-7 w-7" onClick={onPopOut}>
          <ExternalLink className="h-3.5 w-3.5" />
        </IconButton>
      )}
      <IconButton aria-label={collapsed ? 'Expand' : 'Collapse'} title={collapsed ? 'Expand' : 'Collapse'} className="h-7 w-7"
        aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>
        {collapsed ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronUp className="h-3.5 w-3.5" />}
      </IconButton>
      <IconButton aria-label={mode === 'window' ? 'Dock back into the app' : 'Hide status'}
        title={mode === 'window' ? 'Dock back into the app' : 'Hide status'} className="h-7 w-7" onClick={onClose}>
        {mode === 'window' ? <PanelRightClose className="h-3.5 w-3.5" /> : <X className="h-3.5 w-3.5" />}
      </IconButton>
    </div>

    {!collapsed && <>
      {offline && <p className="border-t border-border px-3 py-2 text-muted">
        The status will come back once the app’s background service answers again.
      </p>}

      {summary && <>
        <div className="grid grid-cols-4 gap-1.5 px-3 pb-2 pt-1">
          {stat('Running', summary.running.length, undefined, () => onOpenQueue())}
          {stat('Waiting', summary.waiting, undefined, () => onOpenQueue())}
          {stat('Done today', summary.doneToday)}
          {stat('Check', summary.attention.length, summary.attention.length ? 'warning' : undefined,
            summary.attention.length ? () => onOpenQueue(summary.attention[0].execution_id) : undefined)}
        </div>

        <ul className="max-h-[260px] overflow-y-auto">
          {summary.running.map(item => {
            const started = startedAt(item);
            const progress = liveProgress(item);
            const Icon = onPhone(item) ? Smartphone : Monitor;
            return <li key={item.execution_id} className="flex items-center gap-2 border-t border-border px-3 py-2">
              <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onOpenQueue(item.execution_id, item.profile_id)}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-medium">{accountName(item)}</span>
                  <span className="shrink-0 text-[10px] text-muted">
                    {kindLabel(item)}{started ? ` · ${shortDuration(now - started)}` : ''}
                  </span>
                </div>
                <div className="truncate text-muted">{describe(item)}</div>
                {progress !== null && <div className="mt-1 h-1 overflow-hidden rounded-full bg-elevated">
                  <div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${progress}%` }} />
                </div>}
              </button>
              <IconButton aria-label={`Watch ${accountName(item)} live`} title="Watch live" className="h-7 w-7"
                onClick={() => {
                  const profile = byId.get(item.profile_id);
                  onOpenLive(profile ? liveTargetOf(profile) : { id: item.profile_id, name: accountName(item), phone: false });
                }}>
                <Eye className="h-3.5 w-3.5" />
              </IconButton>
            </li>;
          })}

          {summary.attention.slice(0, 3).map(item => (
            <li key={item.execution_id} className="flex items-center gap-2 border-t border-border bg-warning/10 px-3 py-2">
              <AlertTriangle aria-hidden="true" className="h-4 w-4 shrink-0 text-warning" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-warning">{accountName(item)}</div>
                <div className="truncate text-muted">{describe(item)}</div>
              </div>
              <Button size="sm" variant="outline" className="min-h-7 px-2" onClick={() => onOpenQueue(item.execution_id, item.profile_id)}>
                Review
              </Button>
            </li>
          ))}
          {summary.attention.length > 3 && <li className="border-t border-border px-3 py-1.5">
            <button type="button" className="text-warning hover:underline" onClick={() => onOpenQueue()}>
              {summary.attention.length - 3} more to check
            </button>
          </li>}
        </ul>

        {actionError && <p role="alert" className="border-t border-border px-3 py-1.5 text-danger">{actionError}</p>}

        <div className="flex items-center gap-2 border-t border-border px-3 py-2">
          <span className="min-w-0 flex-1 truncate text-muted">
            {summary.next
              ? `Next: ${accountName(summary.next)} ${startsIn(summary.next, now) ?? ''}`.trim()
              : 'Nothing waiting'}
          </span>
          {(canPause || allPaused) && (
            <Button size="sm" variant="outline" className="min-h-7 px-2" loading={pausing} onClick={() => void togglePause()}
              title={allPaused ? 'Let waiting jobs start again' : 'Nothing new starts; jobs already working finish'}>
              {allPaused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
              {allPaused ? 'Resume' : 'Pause'}
            </Button>
          )}
          {mode === 'window' && onOpenApp && (
            <Button size="sm" variant="ghost" className="min-h-7 px-2" onClick={onOpenApp}>
              Open app
            </Button>
          )}
        </div>
      </>}
    </>}
  </div>;
}
