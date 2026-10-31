import { QueueExecutionItem } from '../../types/automation';

export type QueueSectionKey = 'attention' | 'now' | 'next' | 'finished';
export type QueueTypeFilter = 'all' | 'photo' | 'reel' | 'live' | 'warming';

export const FAILED_STATUSES = new Set(['failed', 'failed_before_publish', 'failed_after_publish']);
export const UNCERTAIN_STATUSES = new Set(['uncertain', 'needs_review']);
export const ACTIVE_STATUSES = new Set(['running', 'preparing']);
export const WAITING_STATUSES = new Set(['pending', 'ready']);
const OPEN_RECOVERY_STATES = new Set(['waiting_network', 'confirming_network', 'waiting_review', 'ready', 'retrying']);

export const isInterruptedRetry = (item: QueueExecutionItem) => item.status === 'cancelled'
  || (item.status === 'stopped' && item.post_type !== 'live');

export const isVideoFile = (file?: string) => /\.(mp4|mov|webm)$/i.test(file || '');
export const hasOpenRecovery = (item: QueueExecutionItem) => OPEN_RECOVERY_STATES.has(item.network_recovery?.state || '');
export const recoveryCancellable = (item: QueueExecutionItem) => Boolean(item.network_recovery)
  && !['cancelled', 'completed'].includes(item.network_recovery!.state) && !ACTIVE_STATUSES.has(item.status);

export const commentNeedsRetry = (item: QueueExecutionItem) => item.first_comment_status === 'failed_input_not_found'
  && [null, undefined, 'failed_to_start', 'failed_before_submission', 'running'].includes(item.comment_retry_status);

export function sectionOf(item: QueueExecutionItem): QueueSectionKey {
  if (ACTIVE_STATUSES.has(item.status)) return 'now';
  if (FAILED_STATUSES.has(item.status) || UNCERTAIN_STATUSES.has(item.status)) return 'attention';
  if (hasOpenRecovery(item)) return 'attention';
  if (item.status === 'published' && commentNeedsRetry(item)) return 'attention';
  if (WAITING_STATUSES.has(item.status)) return 'next';
  return 'finished';
}

export function itemKind(item: QueueExecutionItem): Exclude<QueueTypeFilter, 'all'> {
  if (item.post_type === 'live' || item.post_type === 'warming' || item.post_type === 'reel') return item.post_type;
  return 'photo';
}

export function kindLabel(item: QueueExecutionItem) {
  switch (itemKind(item)) {
    case 'live': return 'Live';
    case 'warming': return 'Warm-up';
    case 'reel': return 'Reel';
    default: return isVideoFile(item.media_file) ? 'Feed video' : item.media_file ? 'Photo' : 'Text post';
  }
}

const formatClock = (seconds = 0) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

/** One plain-language line that explains the row without opening it. */
export function describe(item: QueueExecutionItem): string {
  if (item.status === 'failed_after_publish') return 'Post was clicked but not found afterwards. Check before retrying.';
  if (UNCERTAIN_STATUSES.has(item.status)) return item.post_type === 'live'
    ? 'Broadcast outcome unknown. Confirm it ended.'
    : 'Clicked Post, but couldn’t confirm it appeared.';
  if (FAILED_STATUSES.has(item.status)) return item.error ? `Failed before posting · ${item.error}` : 'Failed before posting · safe to retry';
  if (item.status === 'published' && commentNeedsRetry(item)) return 'Post is live, first comment is missing.';
  if (hasOpenRecovery(item)) return 'Waiting for the network to recover.';
  if (item.post_type === 'live' && ACTIVE_STATUSES.has(item.status)) {
    return `${humanStage(item.stage) || 'Broadcasting'} · ${formatClock(item.elapsed_seconds)} / ${formatClock(item.duration_seconds)}`;
  }
  if (ACTIVE_STATUSES.has(item.status)) return humanStage(item.stage) || (item.status === 'preparing' ? 'Warming up before posting' : 'Starting');
  if (item.post_type === 'warming') {
    const surface = (item.warming_surface || item.warming_options?.surface) === 'profile' ? 'Profile'
      : (item.warming_surface || item.warming_options?.surface) === 'news_feed' ? 'News feed' : 'Feed or profile';
    const cycles = item.warming_scroll_actions != null ? `${item.warming_scroll_actions} scrolls`
      : item.warming_options?.random_scrolls ? `${item.warming_options.min_scrolls}–${item.warming_options.max_scrolls} cycles`
        : `${item.scrolls ?? 4} cycles`;
    return `${surface} · ${cycles}`;
  }
  if (item.post_type === 'live') return item.title || item.spun_caption || item.base_caption || 'Live broadcast';
  return item.spun_caption || item.base_caption || 'No caption';
}

/** Turns internal stage ids like `publish_clicked` into readable words. */
export function humanStage(stage?: string | null) {
  if (!stage) return '';
  const text = stage.replace(/[_-]+/g, ' ').trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function statusLabel(status: string) {
  if (status === 'failed_after_publish') return 'Failed after Post';
  if (FAILED_STATUSES.has(status)) return 'Failed';
  if (status === 'needs_review') return 'Needs review';
  if (status === 'uncertain') return 'Unconfirmed';
  if (status === 'completed') return 'Warmed';
  if (status.startsWith('skipped')) return 'Skipped';
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function formatTime(iso?: string | null) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const today = new Date();
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return date.toDateString() === today.toDateString()
    ? time
    : `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`;
}

/** Time on one line; the date goes above it when it isn't today. */
export function timeParts(iso?: string | null): { day: string | null; time: string } {
  if (!iso) return { day: null, time: '' };
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return { day: null, time: iso };
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return { day: date.toDateString() === new Date().toDateString() ? null : date.toLocaleDateString([], { month: 'short', day: 'numeric' }), time };
}

export function matchesSearch(item: QueueExecutionItem, query: string, profileName: string) {
  if (!query) return true;
  const q = query.toLowerCase();
  return [profileName, item.profile_id, item.batch_name, item.spun_caption, item.base_caption, item.title, item.media_file]
    .some(value => value?.toLowerCase().includes(q));
}
