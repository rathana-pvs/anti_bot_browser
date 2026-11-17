import React, { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchWebshareReplace, startWebshareReplace, WebshareReplaceRun } from '../services/api';
import { useAppDialog } from './ui/AppDialogProvider';

/** Webshare replacement state for the Proxies page. Each Webshare source keeps its own API key,
 * set in the source's settings; a proxy is replaced with its own source's key. */
export function useWebshare(onRefresh: () => void) {
  const { showAlert, showConfirm } = useAppDialog();
  const [run, setRun] = useState<WebshareReplaceRun>({ state: 'idle' });
  const lastRun = useRef<string | undefined>(undefined);
  const running = run.state === 'running';

  useEffect(() => {
    fetchWebshareReplace().then(setRun).catch(() => {});
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await fetchWebshareReplace();
        setRun(next);
        if (next.state !== 'running') onRefresh();
      } catch { /* the next poll tries again */ }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [running, onRefresh]);

  // Show what happened once, when a run this page started finishes.
  useEffect(() => {
    if (run.state !== 'finished' || !run.run_id || lastRun.current !== run.run_id) return;
    lastRun.current = undefined;
    const results = run.results || [];
    const replaced = results.filter(r => !r.error);
    const waiting = replaced.reduce((n, r) => n + (r.accounts || []).length, 0);
    const lines = results.map(r => r.error
      ? `✗ ${r.old || r.proxy_id}: ${r.error}`
      : `✓ ${r.old} → ${r.new}${r.already_replaced ? ' (Webshare had already swapped it; added the new IP)' : ''}${(r.accounts || []).length ? ` · ${(r.accounts || []).length} account(s) marked Proxy replaced` : ''}`);
    if (waiting) lines.push('', 'New proxies are in Available. Change the proxy of each marked account (right-click → Change proxy) when it can rest.');
    void showAlert(lines.join('\n'), {
      title: `Replaced ${replaced.length} of ${results.length} ${results.length === 1 ? 'proxy' : 'proxies'}`,
      variant: replaced.length === results.length ? 'success' : 'warning',
    });
  }, [run, showAlert]);

  const replace = async (ids: string[], accountCount: number) => {
    if (!ids.length) return;
    const confirmed = await showConfirm(
      `Webshare gives ${ids.length === 1 ? 'this proxy' : `each of these ${ids.length} proxies`} a new IP in the same country. `
      + 'This uses your Webshare replacements and cannot be undone.'
      + ' New proxies go to Available.'
      + (accountCount ? ` ${accountCount} ${accountCount === 1 ? 'account stays' : 'accounts stay'} on the old IP, marked Proxy replaced, until you change its proxy yourself. The old IP stops working right away.` : ''),
      { title: 'Replace IP with Webshare', confirmLabel: `Replace ${ids.length === 1 ? 'proxy' : `${ids.length} proxies`}`, variant: 'danger' },
    );
    if (!confirmed) return;
    try {
      const started = await startWebshareReplace(ids);
      lastRun.current = started.run_id;
      setRun(started);
    } catch (err: any) {
      await showAlert(err.message || String(err), { title: 'Could not replace proxies', variant: 'danger' });
    }
  };

  return { run, running, replace };
}

export const WebshareRunBar: React.FC<{ run: WebshareReplaceRun }> = ({ run }) => run.state !== 'running' ? null : (
  <div role="status" className="flex items-center gap-2 rounded-lg border border-blue-900/50 bg-blue-950/20 px-3 py-2 text-xs text-blue-200">
    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
    Webshare is replacing proxies: {run.done ?? 0} of {run.total ?? 0} done. Each one can take a minute.
  </div>
);
