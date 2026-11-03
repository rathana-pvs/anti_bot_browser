import type { AccountHealth, AccountStage, Profile } from '../../types/profile';
import type { AccountTab } from './accountModel';

export interface StageEvidence { label: string; detail?: string; at?: string | null; executionId?: string; tab?: AccountTab }
export interface StageView { stage: AccountStage; reason: string; health?: AccountHealth; evidence?: StageEvidence[] }

const HISTORY_KIND: Record<string, string> = { warming: 'Warm-up', reel: 'Reel', photo: 'Photo', video: 'Feed video', text: 'Text post', live: 'Live' };
const HISTORY_STATUS: Record<string, string> = {
  skipped_auth_required: 'Facebook asked to log in', skipped_personal_profile: 'Personal profile was showing',
  skipped_session_unverified: 'Session not verified', failed_before_publish: 'Failed before posting', failed: 'Failed',
  uncertain: 'Not confirmed', needs_review: 'Needs review', failed_after_publish: 'Clicked but not found', published: 'Posted', completed: 'Finished',
};

/** Account/session health comes from history; job, browser and proxy errors have their own indicators. */
export function stageView(profile: Profile, health?: AccountHealth): StageView {
  const hold = profile.account?.on_hold;
  if (hold || health?.stage === 'on_hold') {
    const note = hold?.note || health?.hold_note;
    return { stage: 'on_hold', reason: `On hold${note ? `: ${note}` : ''}.`, health };
  }
  if (!health) return { stage: 'ready', reason: 'Stage not worked out yet.' };
  const evidence = (health.evidence || []).map(item => ({
    label: `${HISTORY_KIND[item.kind] || 'Post'} · ${HISTORY_STATUS[item.status] || item.status.replace(/_/g, ' ')}`,
    detail: item.error || undefined, at: item.at, executionId: item.execution_id,
  }));
  return { stage: health.stage, reason: health.reason, health, evidence };
}
