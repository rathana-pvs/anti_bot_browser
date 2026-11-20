import type { Profile } from '../../types/profile';

export type Readiness = { label: string; tone: 'ready' | 'busy' | 'warn' | 'muted'; detail?: string };

/** Whether the linked account's Facebook can take jobs: logged in, through "Get Facebook ready", and not on hold. */
export function facebookReadiness(profile?: Profile): Readiness {
  if (!profile) return { label: 'Installed', tone: 'muted' };
  if (profile.account?.on_hold) return { label: 'On hold', tone: 'warn', detail: profile.account.on_hold.note || undefined };
  const steps = profile.android_setup?.steps ?? {};
  const ready = steps.facebook_ready;
  if (ready?.status === 'passed') return { label: 'Ready', tone: 'ready' };
  if (ready?.status === 'running') return { label: 'Getting ready…', tone: 'busy' };
  if (ready?.status === 'failed') return { label: 'Not ready', tone: 'warn', detail: ready.reason || 'Get Facebook ready failed. Retry it on the account.' };
  if (steps.login?.status === 'passed') return { label: 'Logged in, not ready', tone: 'warn', detail: 'Run Get Facebook ready on the account.' };
  if (steps.save_details?.status === 'passed') return { label: 'Not logged in', tone: 'warn' };
  return { label: 'Setup not finished', tone: 'muted' };
}
