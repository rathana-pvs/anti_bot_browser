import React, { useEffect, useRef, useState } from 'react';
import { KeyRound, Loader2, RefreshCw, X } from 'lucide-react';
import {
  fetchProxySettings, fetchWebshareReplace, saveWebshareKey, startWebshareReplace,
  ProxySettings, WebshareReplaceRun,
} from '../services/api';
import { useAppDialog } from './ui/AppDialogProvider';

/** Webshare key and replacement state for the Proxies page. The key itself never reaches the page. */
export function useWebshare(onRefresh: () => void) {
  const { showAlert, showConfirm } = useAppDialog();
  const [settings, setSettings] = useState<ProxySettings | null>(null);
  const [run, setRun] = useState<WebshareReplaceRun>({ state: 'idle' });
  const [keyOpen, setKeyOpen] = useState(false);
  const lastRun = useRef<string | undefined>(undefined);
  const running = run.state === 'running';

  useEffect(() => {
    fetchProxySettings().then(setSettings).catch(() => {});
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
      : `✓ ${r.old} → ${r.new}${(r.accounts || []).length ? ` · ${(r.accounts || []).length} account(s) marked Proxy replaced` : ''}`);
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

  const keyDialog = keyOpen && <WebshareKeyDialog settings={settings} onClose={() => setKeyOpen(false)} onSaved={setSettings} />;
  return { settings, keySet: !!settings?.webshare_key_set, run, running, replace, openKey: () => setKeyOpen(true), keyDialog };
}

export const WebshareButton: React.FC<{ keySet: boolean; disabled?: boolean; onClick: () => void }> = ({ keySet, disabled, onClick }) => (
  <button onClick={onClick} disabled={disabled}
    title={keySet ? 'Webshare API key saved on this PC' : 'Add a Webshare API key to replace bad proxies from here'}
    className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-40">
    <KeyRound className={`h-3.5 w-3.5 ${keySet ? 'text-emerald-400' : 'text-zinc-400'}`} />Webshare
  </button>
);

export const WebshareRunBar: React.FC<{ run: WebshareReplaceRun }> = ({ run }) => run.state !== 'running' ? null : (
  <div role="status" className="flex items-center gap-2 rounded-lg border border-blue-900/50 bg-blue-950/20 px-3 py-2 text-xs text-blue-200">
    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
    Webshare is replacing proxies: {run.done ?? 0} of {run.total ?? 0} done. Each one can take a minute.
  </div>
);

const WebshareKeyDialog: React.FC<{ settings: ProxySettings | null; onClose: () => void; onSaved: (s: ProxySettings) => void }> = ({ settings, onClose, onSaved }) => {
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (value: string) => {
    setSaving(true);
    setError(null);
    try {
      onSaved(await saveWebshareKey(value));
      onClose();
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md overflow-hidden rounded-xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-sm font-semibold text-zinc-100">Webshare API key</h2>
          <button onClick={onClose} disabled={saving} aria-label="Close" className="rounded p-1 text-zinc-400 hover:text-zinc-200"><X className="h-4 w-4" /></button>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); if (key.trim()) void save(key); }} className="space-y-4 p-5 text-xs">
          <p className="text-zinc-500">
            Saved only on this PC and used only to talk to Webshare. Find it in your Webshare dashboard under API keys.
          </p>
          {settings?.webshare_key_set && <p className="text-zinc-300">A key is saved{settings.webshare_key_hint ? ` (${settings.webshare_key_hint})` : ''}. Enter a new one to replace it.</p>}
          <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} aria-label="Webshare API key"
            placeholder="Paste your API key"
            className="w-full rounded-md border border-zinc-800 bg-zinc-950 p-2.5 font-mono text-xs text-zinc-200 outline-none focus:border-zinc-700" />
          <p className="text-[11px] text-amber-300/80">
            Turn off automatic replacement in Webshare's proxy settings. Otherwise Webshare may change an account's IP without this app knowing, and the account won't rest after the move.
          </p>
          {error && <p className="text-red-400">{error}</p>}
          <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
            {settings?.webshare_key_set && <button type="button" disabled={saving} onClick={() => void save('')}
              className="mr-auto px-2 py-1.5 text-red-300 hover:text-red-200 disabled:opacity-40">Remove key</button>}
            <button type="button" disabled={saving} onClick={onClose}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-zinc-300 hover:bg-zinc-800">Cancel</button>
            <button type="submit" disabled={saving || !key.trim()}
              className="flex items-center gap-1.5 rounded-md bg-white px-4 py-1.5 font-semibold text-zinc-950 hover:bg-zinc-200 disabled:opacity-50">
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{saving ? 'Checking…' : 'Save key'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
