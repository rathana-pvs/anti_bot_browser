import { useEffect, useState } from 'react';
import { Profile } from '../../types/profile';
import { QueueExecutionItem } from '../../types/automation';
import { fetchQueue } from '../../services/api';
import { ACTIVE_STATUSES, FAILED_STATUSES, UNCERTAIN_STATUSES, WAITING_STATUSES, commentNeedsRetry, hasOpenRecovery, sectionOf } from '../queue/queueModel';

export type AccountTab = 'browser' | 'overview' | 'settings' | 'activity';
export type HealthTone = 'ok' | 'warn' | 'bad' | 'muted';
export interface HealthSignal {
  label: string; tone: HealthTone; hint?: string; detail?: string;
  /** Clicking the signal opens this account's items in the Queue. */
  opensQueue?: boolean;
}

export type ActivityPeriod = 1 | 3 | 7 | 30;
export const PERIOD_OPTIONS: { value: ActivityPeriod; label: string; column: string }[] = [
  { value: 1, label: 'Today', column: 'Today' },
  { value: 3, label: '3 days', column: 'Last 3 days' },
  { value: 7, label: '1 week', column: 'Last 7 days' },
  { value: 30, label: '1 month', column: 'Last 30 days' },
];

/** Start of the window: midnight today, then back `days - 1` calendar days. */
export function periodStart(days: ActivityPeriod) {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (days - 1));
  return start.getTime();
}

export interface AccountActivity {
  /** Counts within the selected period. */
  posted: number;
  warmed: number;
  running: QueueExecutionItem | null;
  next: QueueExecutionItem | null;
  /** Unresolved items within the selected period. */
  attention: QueueExecutionItem[];
  recent: QueueExecutionItem[];
}

const finishedAt = (item: QueueExecutionItem) => new Date(item.published_at || item.stage_updated_at || item.scheduled_at).getTime() || 0;

export function activityFor(profileId: string, executions: QueueExecutionItem[], period: ActivityPeriod = 1): AccountActivity {
  const mine = executions.filter(item => item.profile_id === profileId);
  const since = periodStart(period);
  const inPeriod = mine.filter(item => finishedAt(item) >= since);
  const byTime = (item: QueueExecutionItem) => new Date(item.scheduled_at).getTime() || 0;
  const waiting = mine.filter(item => WAITING_STATUSES.has(item.status)).sort((a, b) => byTime(a) - byTime(b));
  return {
    posted: inPeriod.filter(item => item.status === 'published').length,
    warmed: inPeriod.filter(item => item.status === 'completed').length,
    running: mine.find(item => ACTIVE_STATUSES.has(item.status)) || null,
    next: waiting[0] || null,
    attention: inPeriod.filter(item => sectionOf(item) === 'attention'),
    recent: [...mine].sort((a, b) => (new Date(b.published_at || b.scheduled_at).getTime() || 0) - (new Date(a.published_at || a.scheduled_at).getTime() || 0)),
  };
}

/** Accounts that live on an Android user of a physical phone, not a browser container. */
export const isAndroid = (profile: Profile) => profile.runtime?.kind === 'android_user';

export function phoneLabel(profile: Profile) {
  return profile.runtime ? `Phone · Android user ${profile.runtime.android_user_id}` : '';
}

/** Browser problems shown under the status. */
export function browserNotes(profile: Profile): HealthSignal[] {
  const notes: HealthSignal[] = [];
  if (profile.status === 'error') notes.push({ label: 'Browser error', tone: 'bad', hint: 'The browser container reported an error. Open the account to restart it.' });
  if (profile.restart_required) notes.push({ label: 'Restart needed', tone: 'warn', hint: 'Saved settings apply after the next restart.' });
  return notes;
}

export { networkSignal } from './networkSignal';

export function liveSignal(profile: Profile): HealthSignal {
  return profile.live?.configured
    ? { label: 'Ready', tone: 'ok', hint: 'A Facebook Live stream key is saved.' }
    : { label: 'Not set up', tone: 'muted', hint: 'Add a stream key in Settings → Facebook Live.' };
}

/** Open queue items that need the user, as clickable signals. */
export function openItemSignals(activity: AccountActivity): HealthSignal[] {
  const open = attentionBreakdown(activity.attention);
  const signals: HealthSignal[] = [];
  if (open.unconfirmed) signals.push({ label: `${open.unconfirmed} unconfirmed`, tone: 'warn', opensQueue: true,
    hint: 'Post was clicked but not confirmed on Facebook. Check the page, then mark it posted or not posted.' });
  if (open.failed) signals.push({ label: `${open.failed} failed`, tone: 'bad', opensQueue: true,
    hint: 'Stopped before posting, so nothing went out. Retry or remove them.' });
  if (open.comment) signals.push({ label: `${open.comment} comment missing`, tone: 'warn', opensQueue: true,
    hint: 'The post is up but its first comment isn’t. Retry the comment only.' });
  return signals;
}

/**
 * Splits open queue items by what the user has to do. Unconfirmed items can mean a
 * post already went out, so they are kept apart from plain failures that are safe to retry.
 */
export function attentionBreakdown(items: QueueExecutionItem[]) {
  let unconfirmed = 0, failed = 0, comment = 0;
  for (const item of items) {
    if (UNCERTAIN_STATUSES.has(item.status) || item.status === 'failed_after_publish' || hasOpenRecovery(item)) unconfirmed += 1;
    else if (FAILED_STATUSES.has(item.status)) failed += 1;
    else if (item.status === 'published' && commentNeedsRetry(item)) comment += 1;
  }
  return { unconfirmed, failed, comment };
}

/** Failed-before-posting items safe to delete: nothing was published and no recovery is pending. */
export function clearableFailures(profileId: string, executions: QueueExecutionItem[], before?: number) {
  return executions.filter(item => item.profile_id === profileId
    && (item.status === 'failed' || item.status === 'failed_before_publish')
    && !hasOpenRecovery(item)
    && (before === undefined || finishedAt(item) < before));
}

export function needsAttention(profile: Profile, activity?: AccountActivity) {
  return profile.status === 'error' || Boolean(profile.restart_required)
    || Boolean(profile.effective_environment?.network_preflight && !profile.effective_environment.network_preflight.startsWith('passed:'))
    || Boolean(activity?.attention.length);
}

export function warmupLabel(profile: Profile) {
  return profile.account?.warming_complete ? 'Warmed up' : `Warm-up week ${profile.account?.warming_week || 1}`;
}

export const STATUS_LABEL: Record<Profile['status'], string> = { running: 'Running', paused: 'Paused', stopped: 'Stopped', error: 'Error' };
export const STATUS_TONE: Record<Profile['status'], string> = {
  running: 'text-emerald-400', paused: 'text-amber-400', stopped: 'text-zinc-500', error: 'text-red-400',
};
export const TONE_CLASS: Record<HealthTone, string> = {
  ok: 'text-emerald-300', warn: 'text-amber-300', bad: 'text-red-300', muted: 'text-zinc-500',
};

/** Polls the queue while `active`, so account pages can show today's work. */
export function useQueueExecutions(active: boolean, intervalMs = 8000) {
  const [executions, setExecutions] = useState<QueueExecutionItem[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const load = () => fetchQueue().then(data => { if (!cancelled) setExecutions(data.executions); }).catch(() => undefined);
    load();
    const timer = setInterval(load, intervalMs);
    return () => { cancelled = true; clearInterval(timer); };
  }, [active, intervalMs, reloadKey]);
  return { executions, reload: () => setReloadKey(key => key + 1) };
}
