import { useEffect, useState } from 'react';
import { Profile } from '../../types/profile';
import { QueueExecutionItem } from '../../types/automation';
import { fetchQueue } from '../../services/api';
import { ACTIVE_STATUSES, WAITING_STATUSES, sectionOf } from '../queue/queueModel';

export type AccountTab = 'browser' | 'overview' | 'settings' | 'activity';
export type HealthTone = 'ok' | 'warn' | 'bad' | 'muted';
export interface HealthSignal { label: string; tone: HealthTone; hint?: string }

export interface AccountActivity {
  postedToday: number;
  running: QueueExecutionItem | null;
  next: QueueExecutionItem | null;
  attention: QueueExecutionItem[];
  recent: QueueExecutionItem[];
}

const isToday = (iso?: string | null) => !!iso && new Date(iso).toDateString() === new Date().toDateString();

export function activityFor(profileId: string, executions: QueueExecutionItem[]): AccountActivity {
  const mine = executions.filter(item => item.profile_id === profileId);
  const byTime = (item: QueueExecutionItem) => new Date(item.scheduled_at).getTime() || 0;
  const waiting = mine.filter(item => WAITING_STATUSES.has(item.status)).sort((a, b) => byTime(a) - byTime(b));
  return {
    postedToday: mine.filter(item => item.status === 'published' && isToday(item.published_at || item.scheduled_at)).length,
    running: mine.find(item => ACTIVE_STATUSES.has(item.status)) || null,
    next: waiting[0] || null,
    attention: mine.filter(item => sectionOf(item) === 'attention'),
    recent: [...mine].sort((a, b) => (new Date(b.published_at || b.scheduled_at).getTime() || 0) - (new Date(a.published_at || a.scheduled_at).getTime() || 0)),
  };
}

/** Plain-language health from what the backend already reports. No login detection yet. */
export function healthFor(profile: Profile, activity?: AccountActivity): HealthSignal[] {
  const signals: HealthSignal[] = [];
  if (profile.status === 'error') signals.push({ label: 'Browser error', tone: 'bad', hint: 'The browser container reported an error. Open the account to restart it.' });
  if (profile.restart_required) signals.push({ label: 'Restart needed', tone: 'warn', hint: 'Saved settings apply after the next restart.' });
  const preflight = profile.effective_environment?.network_preflight;
  if (preflight) {
    signals.push(preflight.startsWith('passed:')
      ? { label: profile.network.proxy_host ? 'Proxy OK' : 'Network OK', tone: 'ok', hint: preflight }
      : { label: 'Network failed', tone: 'bad', hint: preflight });
  } else {
    signals.push({ label: profile.network.proxy_host ? 'Proxy' : 'Direct', tone: 'muted', hint: profile.network.proxy_host ? `${profile.network.proxy_host}:${profile.network.proxy_port}` : 'No proxy' });
  }
  signals.push(profile.live?.configured ? { label: 'Live ready', tone: 'ok' } : { label: 'No Live key', tone: 'muted' });
  if (activity?.attention.length) signals.push({ label: `${activity.attention.length} need review`, tone: 'warn' });
  return signals;
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
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const load = () => fetchQueue().then(data => { if (!cancelled) setExecutions(data.executions); }).catch(() => undefined);
    load();
    const timer = setInterval(load, intervalMs);
    return () => { cancelled = true; clearInterval(timer); };
  }, [active, intervalMs]);
  return executions;
}
