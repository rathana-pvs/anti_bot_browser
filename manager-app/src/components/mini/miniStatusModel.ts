import { QueueDataResponse, QueueExecutionItem } from '../../types/automation';
import { sectionOf } from '../queue/queueModel';

/** What the small status panel shows, worked out from one queue list. */
export interface MiniSummary {
  running: QueueExecutionItem[];
  attention: QueueExecutionItem[];
  waiting: number;
  doneToday: number;
  /** The waiting job expected to start first. */
  next: QueueExecutionItem | null;
  /** Batches that still have waiting jobs, split by whether they are paused. */
  activeBatchIds: string[];
  pausedBatchIds: string[];
}

const DONE_STATUSES = new Set(['published', 'completed']);

const startOf = (item: QueueExecutionItem) => Date.parse(item.expected_start_at || item.scheduled_at || '') || Infinity;

const sameLocalDay = (iso: string | null | undefined, now: Date) => {
  if (!iso) return false;
  const at = new Date(iso);
  return !Number.isNaN(at.getTime()) && at.toDateString() === now.toDateString();
};

export function summarize(queue: Pick<QueueDataResponse, 'executions' | 'batches'>, now = new Date()): MiniSummary {
  const running: QueueExecutionItem[] = [];
  const attention: QueueExecutionItem[] = [];
  const waitingItems: QueueExecutionItem[] = [];
  let doneToday = 0;
  for (const item of queue.executions) {
    const section = sectionOf(item);
    if (section === 'now') running.push(item);
    else if (section === 'attention') attention.push(item);
    else if (section === 'next') waitingItems.push(item);
    else if (DONE_STATUSES.has(item.status) && sameLocalDay(item.published_at, now)) doneToday += 1;
  }
  const waitingBatches = new Set(waitingItems.map(item => item.batch_id).filter((id): id is string => Boolean(id)));
  const activeBatchIds: string[] = [];
  const pausedBatchIds: string[] = [];
  for (const batch of queue.batches) {
    if (!waitingBatches.has(batch.batch_id)) continue;
    (batch.paused ? pausedBatchIds : activeBatchIds).push(batch.batch_id);
  }
  const next = waitingItems.reduce<QueueExecutionItem | null>(
    (best, item) => (best === null || startOf(item) < startOf(best) ? item : best), null);
  return { running, attention, waiting: waitingItems.length, doneToday, next, activeBatchIds, pausedBatchIds };
}

/** When the job started working: the first recorded stage, else its last stage change. */
export function startedAt(item: QueueExecutionItem): number | null {
  const first = item.stage_history?.[0]?.timestamp || item.stage_updated_at;
  const at = Date.parse(first || '');
  return Number.isNaN(at) ? null : at;
}

/** "45s", "3 min", "1 h 05 min". */
export function shortDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
}

/** "in 4 min", "now" (already due), or null when the job has no start time. */
export function startsIn(item: QueueExecutionItem, now = Date.now()): string | null {
  const at = startOf(item);
  if (!Number.isFinite(at)) return null;
  return at <= now + 30_000 ? 'now' : `in ${shortDuration(at - now)}`;
}

/** 0–100 for a live broadcast with a known length; null for jobs whose length isn't known. */
export function liveProgress(item: QueueExecutionItem): number | null {
  if (item.post_type !== 'live' || !item.duration_seconds) return null;
  return Math.min(100, Math.max(0, Math.round(((item.elapsed_seconds || 0) / item.duration_seconds) * 100)));
}
