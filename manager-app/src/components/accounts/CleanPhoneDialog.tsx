import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Circle, Loader2, RotateCcw, X } from 'lucide-react';
import { Profile } from '../../types/profile';
import { CleanProgress, fetchPhoneClean, startPhoneClean } from '../../services/devices';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import { cn } from '../ui/cn';

const POLL_MS = 1000;

const ICON = {
  passed: <Check className="h-3.5 w-3.5 text-emerald-300" aria-label="Done" />,
  failed: <X className="h-3.5 w-3.5 text-red-300" aria-label="Failed" />,
  running: <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-300" aria-label="Running" />,
  pending: <Circle className="h-3 w-3 text-zinc-600" aria-label="Not done yet" />,
};

/** Shows a phone user's clean step by step while it runs on the phone. `onFinished` runs once,
 * when the clean has ended (the account is gone when it succeeded). */
export function CleanPhoneDialog({ profile, initial, onClose, onFinished }: {
  profile: Profile; initial: CleanProgress; onClose: () => void; onFinished: (progress: CleanProgress) => void;
}) {
  const [progress, setProgress] = useState(initial);
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState(false);
  const reported = useRef<CleanProgress | null>(null);
  const running = progress.state === 'running';

  const read = useCallback(async () => {
    try {
      setProgress(await fetchPhoneClean(profile.id));
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not read the progress');
    }
  }, [profile.id]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void read(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, read]);

  useEffect(() => {
    if (running || reported.current === progress) return;
    reported.current = progress;
    onFinished(progress);
  }, [running, progress, onFinished]);

  const retry = async () => {
    setRetrying(true);
    setError('');
    try {
      setProgress(await startPhoneClean(profile.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start the clean');
    } finally {
      setRetrying(false);
    }
  };

  const userLabel = `${profile.runtime?.android_user_name || 'User'} (user ${progress.android_user_id})`;
  const left = progress.result?.media_left ?? 0;
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title={`Clean ${userLabel}`}
    description={`For ${profile.name}. The Android user stays on the phone.`}
    footer={<>
      {progress.state === 'failed' && <Button size="sm" variant="outline" loading={retrying} onClick={() => void retry()}>
        <RotateCcw className="mr-1.5 h-3.5 w-3.5" />Try again</Button>}
      <Button size="sm" variant={running ? 'ghost' : 'primary'} onClick={onClose}>{running ? 'Hide' : 'Close'}</Button>
    </>}>
    <ol className="space-y-2">
      {progress.steps.map(step => <li key={step.key} className="flex items-start gap-2 text-sm">
        <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center">{ICON[step.status]}</span>
        <span className="min-w-0">
          <span className={cn(step.status === 'pending' ? 'text-zinc-500' : 'text-zinc-200')}>{step.label}</span>
          {step.detail && <span className={cn('block break-words text-[11px]',
            step.status === 'failed' ? 'text-red-300' : 'text-zinc-500')}>{step.detail}</span>}
        </span>
      </li>)}
    </ol>
    <p role="status" className={cn('mt-4 text-xs', progress.state === 'failed' ? 'text-red-300'
      : progress.state === 'done' && left ? 'text-amber-300' : 'text-zinc-400')}>
      {running ? 'Cleaning… this can take a minute. You can hide this; it keeps going on the phone.'
        : progress.state === 'failed' ? 'The clean stopped. The account was kept, so you can try again.'
        : left ? `Cleaned, but ${left} gallery file${left === 1 ? '' : 's'} stayed on the phone. The account was deleted.`
        : 'Done. The Android user is empty and ready for a new account and a fresh login.'}
    </p>
    {error && <p role="alert" className="mt-2 break-words text-[11px] text-red-300">{error}</p>}
  </Dialog>;
}
