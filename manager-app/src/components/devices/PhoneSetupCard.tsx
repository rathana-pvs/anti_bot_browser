import { useCallback, useEffect, useState } from 'react';
import { Check, Circle, Loader2, RefreshCw, RotateCcw, X } from 'lucide-react';
import { Profile } from '../../types/profile';
import { SetupProgress } from '../../types/device';
import { confirmAccountLogin, fetchAccountSetup, startAccountSetup, startFacebookReady } from '../../services/devices';
import { Button } from '../ui/Button';
import { cn } from '../ui/cn';

const card = 'rounded-xl border border-zinc-800 bg-zinc-950/40 p-4';
const POLL_MS = 2000;

const ICON = {
  passed: <Check className="h-3.5 w-3.5 text-emerald-300" aria-label="Done" />,
  failed: <X className="h-3.5 w-3.5 text-red-300" aria-label="Failed" />,
  running: <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-300" aria-label="Running" />,
  pending: <Circle className="h-3 w-3 text-zinc-600" aria-label="Not done yet" />,
};

/** Sets a phone account up on its phone, step by step, and ends with the operator's Facebook login. */
export function PhoneSetupCard({ profile, onOpenScreen, onChanged }: {
  profile: Profile; onOpenScreen: () => void; onChanged: () => Promise<void> | void;
}) {
  const [progress, setProgress] = useState<SetupProgress | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'action' | 'recheck' | null>(null);

  const read = useCallback(async () => {
    try {
      setProgress(await fetchAccountSetup(profile.id));
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not read the setup');
    }
  }, [profile.id]);

  useEffect(() => { void read(); }, [read]);
  // "Get Facebook ready" runs after setup is done, so a running step counts too.
  const running = progress?.state === 'running' || !!progress?.steps.some(step => step.status === 'running');
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void read(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, read]);
  // A finished run changed the account (network check, details); refresh it once.
  const finished = progress && !running;
  useEffect(() => { if (finished) void onChanged(); }, [finished]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (action: () => Promise<SetupProgress>, kind: 'action' | 'recheck' = 'action') => {
    setBusy(kind);
    setError('');
    try {
      setProgress(await action());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Something went wrong');
    } finally {
      setBusy(null);
    }
  };

  const failed = progress?.steps.find(step => step.status === 'failed');
  const ready = progress?.steps.find(step => step.key === 'facebook_ready');
  const started = progress && progress.state !== 'not_started';
  // A new run checks every step again and repairs what changed; the login stays as it was.
  const recheck = <Button size="sm" variant="outline" loading={busy === 'recheck'} disabled={busy === 'action'}
    onClick={() => void act(() => startAccountSetup(profile.id), 'recheck')}><RefreshCw className="mr-1.5 h-3.5 w-3.5" />Recheck</Button>;
  return <section className={card} aria-labelledby="overview-phone-setup">
    <h3 id="overview-phone-setup" className="mb-1 text-sm font-semibold text-zinc-100">Set up on the phone</h3>
    <p className="mb-3 text-[11px] text-zinc-500">Installs the companion app, sends the proxy, turns on the kill switch and checks the
      network before Facebook is installed. Steps that are already done are skipped, so Retry and Recheck are always safe.</p>
    {progress && <ol className="space-y-1.5">
      {progress.steps.map(step => <li key={step.key} className="flex items-start gap-2">
        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">{ICON[step.status]}</span>
        <span className="min-w-0">
          <span className={cn(step.status === 'pending' ? 'text-zinc-500' : 'text-zinc-200')}>{step.label}</span>
          {step.status === 'failed' && step.reason && <span className="block break-words text-[11px] text-red-300">{step.reason}</span>}
        </span>
      </li>)}
    </ol>}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {progress?.state === 'waiting_login' ? <>
        <Button size="sm" variant="outline" onClick={onOpenScreen}>Open the phone screen</Button>
        <Button size="sm" loading={busy === 'action'} disabled={busy === 'recheck'} onClick={() => void act(() => confirmAccountLogin(profile.id))}>I’m logged in</Button>
        {recheck}
        <span className="text-[11px] text-zinc-500">Log in to Facebook on the phone screen, then confirm.</span>
      </> : !running && progress?.state === 'done' ? <>
        {ready?.status !== 'passed' && <Button size="sm" variant="outline" loading={busy === 'action'} disabled={busy === 'recheck'} onClick={() => void act(() => startFacebookReady(profile.id))}>
          {ready?.status === 'failed' ? <><RotateCcw className="mr-1.5 h-3.5 w-3.5" />Retry</> : 'Get Facebook ready'}</Button>}
        {recheck}
        {ready?.status !== 'passed' && <span className="text-[11px] text-zinc-500">Opens Facebook’s gallery once so its first-use screens never stop a post. Nothing is posted.</span>}
      </> : !running && progress?.state !== 'done' && <Button size="sm" loading={busy === 'action'} onClick={() => void act(() => startAccountSetup(profile.id))}>
        {failed ? <><RotateCcw className="mr-1.5 h-3.5 w-3.5" />Retry</> : started ? 'Continue setup' : 'Start setup'}</Button>}
      {running && <span className="flex items-center gap-1.5 text-[11px] text-zinc-400"><Loader2 className="h-3 w-3 animate-spin" />
        Setting up… this can take a few minutes. You can leave this page.</span>}
    </div>
    {error && <p role="alert" className="mt-2 break-words text-[11px] text-red-300">{error}</p>}
  </section>;
}
