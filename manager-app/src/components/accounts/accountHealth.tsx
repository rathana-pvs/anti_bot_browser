import { useEffect, useState } from 'react';
import { CircleCheck, Flame, Hand, LucideIcon, Moon, Pin, Sprout, TriangleAlert } from 'lucide-react';
import { AccountHealth, AccountStage, Profile } from '../../types/profile';
import { fetchAccountHealth } from '../../services/api';
import { cn } from '../ui/cn';
import { AccountActivity, AccountTab, attentionBreakdown } from './accountModel';
import { describe, kindLabel } from '../queue/queueModel';

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
  attention: { label: 'Needs attention', icon: TriangleAlert, tone: 'text-red-300', suggestion: 'Check this account before posting.' },
  on_hold: { label: 'On hold', icon: Hand, tone: 'text-sky-300', suggestion: 'Nothing automatic runs: no posts, no warming, no cloud jobs. Release the hold when you are done, e.g. after changing the proxy.' },
};

/** Something the user can open to see why: a queue item (with its screenshots) or an account tab. */
export interface StageEvidence { label: string; detail?: string; at?: string | null; executionId?: string; tab?: AccountTab }
export interface StageView { stage: AccountStage; reason: string; health?: AccountHealth; evidence?: StageEvidence[] }

const HISTORY_KIND: Record<string, string> = { warming: 'Warm-up', reel: 'Reel', photo: 'Photo', video: 'Feed video', text: 'Text post', live: 'Live' };
const HISTORY_STATUS: Record<string, string> = {
  skipped_auth_required: 'Facebook asked to log in', skipped_personal_profile: 'Personal profile was showing',
  skipped_session_unverified: 'Session not verified', failed_before_publish: 'Failed before posting', failed: 'Failed',
  uncertain: 'Not confirmed', needs_review: 'Needs review', failed_after_publish: 'Clicked but not found', published: 'Posted', completed: 'Finished',
};

function historyEvidence(health?: AccountHealth): StageEvidence[] {
  return (health?.evidence || []).map(item => ({
    label: `${HISTORY_KIND[item.kind] || 'Post'} · ${HISTORY_STATUS[item.status] || item.status.replace(/_/g, ' ')}`,
    detail: item.error || undefined, at: item.at, executionId: item.execution_id,
  }));
}

/** Problems the app already knows about locally (browser, network, open queue items). */
function localAttention(profile: Profile, activity?: AccountActivity): { reason: string; evidence: StageEvidence[] } | null {
  if (profile.status === 'error') return { reason: 'The browser reported an error.', evidence: [{ label: 'Open the browser to see it', tab: 'browser' }] };
  if (profile.restart_required) return { reason: 'Saved settings apply after the next restart.', evidence: [{ label: 'Review the saved settings', tab: 'settings' }] };
  const preflight = profile.effective_environment?.network_preflight;
  if (preflight && !preflight.startsWith('passed:')) return { reason: 'The network check did not pass.', evidence: [{ label: 'Network check result', detail: preflight, tab: 'settings' }] };
  if (activity?.attention.length) {
    const open = attentionBreakdown(activity.attention);
    const evidence = activity.attention.slice(0, 5).map(item => ({
      label: `${kindLabel(item)} · ${describe(item)}`, at: item.stage_updated_at || item.scheduled_at, executionId: item.execution_id,
    }));
    if (open.unconfirmed) return { reason: `${open.unconfirmed} post${open.unconfirmed > 1 ? 's' : ''} could not be confirmed.`, evidence };
    return { reason: 'Open items in the queue need you.', evidence };
  }
  return null;
}

/** The stage shown for an account: local problems first, then the history-based stage. */
export function stageView(profile: Profile, health?: AccountHealth, activity?: AccountActivity): StageView {
  // The user's own hold comes first, even before local problems.
  const hold = profile.account?.on_hold;
  if (hold || health?.stage === 'on_hold') {
    const note = hold?.note || health?.hold_note;
    return { stage: 'on_hold', reason: `On hold${note ? `: ${note}` : ''}.`, health };
  }
  const local = localAttention(profile, activity);
  if (local) return { stage: 'attention', reason: local.reason, health, evidence: local.evidence };
  if (!health) return { stage: 'ready', reason: 'Stage not worked out yet.' };
  return { stage: health.stage, reason: health.reason, health, evidence: historyEvidence(health) };
}

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
