import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { checkProxyGeography, testSavedProxySpeed } from '../services/api';
import { ProxyItem, savedSpeedSummary } from '../types/proxy';
import { cn } from './ui/cn';

/**
 * The proxy's saved check from the Proxies page, with an optional retest. A retest saves its
 * result like the Proxies page does, so the proxy's label updates everywhere.
 */
export function SavedProxyCheck({ proxy, disabled, onRetested }: {
  proxy: ProxyItem; disabled?: boolean; onRetested: () => Promise<void> | void;
}) {
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const retest = async () => {
    setTesting(true);
    setError('');
    // Speed decides Good or Poor; the exit-IP check refreshes location and timezone.
    const [speed, geo] = await Promise.allSettled([testSavedProxySpeed(proxy.id), checkProxyGeography(proxy.id)]);
    const failed = [speed, geo].find((result): result is PromiseRejectedResult => result.status === 'rejected');
    if (failed) setError(failed.reason instanceof Error ? failed.reason.message : 'Retest failed');
    try {
      await onRetested();
    } finally {
      setTesting(false);
    }
  };
  return <div className="space-y-1">
    <div className="flex items-center gap-2">
      <p className="min-w-0 flex-1 text-[11px] text-zinc-500">{testing ? 'Testing speed and location… this may take up to a minute.' : savedSpeedSummary(proxy)}</p>
      <button type="button" onClick={() => void retest()} disabled={disabled || testing}
        className="flex shrink-0 items-center gap-1 rounded border border-zinc-800 px-2 py-1 text-[11px] text-zinc-300 hover:border-zinc-700 disabled:opacity-50">
        <RefreshCw className={cn('h-3 w-3', testing && 'animate-spin')} aria-hidden="true" />{testing ? 'Testing…' : 'Retest'}
      </button>
    </div>
    {error && <p className="text-[11px] text-red-300">{error}</p>}
  </div>;
}
