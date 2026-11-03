import { useEffect, useState } from 'react';
import { CircleCheck, Flame, Hand, LucideIcon, Moon, Pin, Sprout, TriangleAlert } from 'lucide-react';
import { AccountHealth, AccountStage } from '../../types/profile';
import { fetchAccountHealth } from '../../services/api';
import { cn } from '../ui/cn';
import type { StageView } from './accountStage';
export { stageView } from './accountStage';
export type { StageEvidence, StageView } from './accountStage';

/** System folders: one per stage. They cannot be renamed, deleted or dropped onto.
 * All fill themselves from history except On hold, which the user sets from the account menu. */
export const STAGE_FOLDER_PREFIX = '__stage__';
export const stageFolder = (stage: AccountStage) => `${STAGE_FOLDER_PREFIX}${stage}`;

export const STAGE_ORDER: AccountStage[] = ['new', 'warming_up', 'ready', 'resting', 'attention', 'on_hold'];

export const STAGE_META: Record<AccountStage, { label: string; icon: LucideIcon; tone: string; suggestion: string }> = {
  new: { label: 'New', icon: Sprout, tone: 'text-blue-300', suggestion: 'Warm-up only for now; posting is not suggested yet.' },
  warming_up: { label: 'Warming up', icon: Flame, tone: 'text-amber-300', suggestion: 'Post lightly while the account builds history.' },
  ready: { label: 'Ready', icon: CircleCheck, tone: 'text-zinc-400', suggestion: 'Normal posting.' },
  resting: { label: 'Resting', icon: Moon, tone: 'text-violet-300', suggestion: 'Better to wait before posting again.' },
  attention: { label: 'Needs attention', icon: TriangleAlert, tone: 'text-red-300', suggestion: 'Check login, Facebook verification and the selected Page before posting.' },
  on_hold: { label: 'On hold', icon: Hand, tone: 'text-sky-300', suggestion: 'Nothing automatic runs: no posts, no warming, no cloud jobs. Release the hold when you are done, e.g. after changing the proxy.' },
};

export function restingLeft(until: string | null | undefined) {
  const ms = until ? new Date(until).getTime() - Date.now() : 0;
  if (!(ms > 0)) return '';
  const hours = Math.round(ms / 3600000);
  return hours >= 24 ? `${Math.round(hours / 24)}d` : hours >= 1 ? `${hours}h` : `${Math.max(1, Math.round(ms / 60000))}m`;
}

export function stageLabel(view: StageView) {
  if (view.stage === 'warming_up' && view.health?.proxy_restart) return `${STAGE_META.warming_up.label} · new proxy`;
  const left = view.stage === 'resting' ? restingLeft(view.health?.until) : '';
  return left ? `${STAGE_META[view.stage].label} · ${left}` : STAGE_META[view.stage].label;
}

/** "tomorrow 14:00" style end of a proxy restart. */
export function restartEnds(health?: AccountHealth) {
  const until = health?.proxy_restart?.until;
  if (!until) return '';
  const date = new Date(until);
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return date.toDateString() === new Date().toDateString() ? `today ${time}` : `tomorrow ${time}`;
}

const HEALTH_CHANGED = 'account-health-changed';
/** Asks every open stage view to reload now, e.g. after a hold changes. */
export const refreshAccountHealth = () => window.dispatchEvent(new Event(HEALTH_CHANGED));

/** "1 of 2 suggested posts today", or '' when a daily count would not help. */
export function todayCount(view: StageView) {
  const health = view.health;
  return health && health.daily_limit > 0 && view.stage !== 'attention' && view.stage !== 'on_hold'
    ? `${health.posts_today} of ${health.daily_limit} suggested posts today.` : '';
}

export function stageTooltip(view: StageView) {
  const today = todayCount(view);
  const evidence = view.evidence?.length ? ` Evidence: ${view.evidence[0].label}${view.evidence.length > 1 ? ` (+${view.evidence.length - 1} more)` : ''}. Open the account to view it.` : '';
  return `${stageLabel(view)}: ${view.reason}${today ? ` ${today}` : ''} ${STAGE_META[view.stage].suggestion}${view.health?.manual ? ' Stage set by hand.' : ''}${evidence}`;
}

/** Stage icon, with its label unless `compact`. Ready stays quiet in lists. */
export function StageBadge({ view, compact = false, quietReady = false, className }: {
  view: StageView; compact?: boolean; quietReady?: boolean; className?: string;
}) {
  const meta = STAGE_META[view.stage];
  const Icon = meta.icon;
  const label = stageLabel(view);
  const showLabel = !compact && !(quietReady && view.stage === 'ready');
  return <span title={stageTooltip(view)} aria-label={`Account stage: ${label}`} role="img"
    className={cn('inline-flex shrink-0 items-center gap-1 text-[11px]', meta.tone, quietReady && view.stage === 'ready' && 'opacity-60', className)}>
    <span className="relative inline-flex">
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {view.health?.manual && <Pin className="absolute -right-1.5 -top-1.5 h-2.5 w-2.5 text-zinc-300" aria-hidden="true" />}
    </span>
    {showLabel && <span className="whitespace-nowrap">{label}</span>}
  </span>;
}

/** Polls the account stages while `active`. */
export function useAccountHealth(active = true, intervalMs = 30000) {
  const [health, setHealth] = useState<Record<string, AccountHealth>>({});
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const load = () => fetchAccountHealth().then(data => { if (!cancelled) setHealth(data.profiles); }).catch(() => undefined);
    load();
    const timer = setInterval(load, intervalMs);
    window.addEventListener(HEALTH_CHANGED, load);
    return () => { cancelled = true; clearInterval(timer); window.removeEventListener(HEALTH_CHANGED, load); };
  }, [active, intervalMs, reloadKey]);
  return { health, reload: () => setReloadKey(key => key + 1) };
}
