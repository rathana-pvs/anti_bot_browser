import { useEffect, useState } from 'react';
import { Check, CircleAlert, Copy, Eye, EyeOff, Loader2, MonitorSmartphone, RefreshCw } from 'lucide-react';
import { fetchWebAccess, newWebAccessPassword, setWebAccess, type WebAccessStatus } from '../services/webAccess';

function CopyField({ label, value, secret = false }: { label: string; value: string; secret?: boolean }) {
  const [visible, setVisible] = useState(!secret);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* Clipboard can be unavailable; the value stays selectable. */ }
  };
  return <div>
    <p className="text-[11px] font-medium text-zinc-400">{label}</p>
    <div className="mt-1 flex items-center gap-1.5">
      <code className="min-w-0 flex-1 select-all truncate rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-100">
        {visible ? value : '•'.repeat(16)}
      </code>
      {secret && <button type="button" onClick={() => setVisible((v) => !v)} aria-label={`${visible ? 'Hide' : 'Show'} ${label.toLowerCase()}`}
        className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200">
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>}
      <button type="button" onClick={() => { void copy(); }} aria-label={`Copy ${label.toLowerCase()}`}
        className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200">
        {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
      </button>
    </div>
  </div>;
}

export function WebAccessSettings() {
  const [status, setStatus] = useState<WebAccessStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const waiting = Boolean(status && status.remembered && !status.enabled);
  useEffect(() => {
    let active = true;
    const load = () => fetchWebAccess()
      .then((next) => { if (active) { setStatus(next); setError(null); } })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : 'Could not read web access status.'); });
    void load();
    // While it is turning back on after a restart, follow along until it is on.
    const timer = waiting ? window.setInterval(() => { void load(); }, 3000) : undefined;
    return () => { active = false; if (timer) window.clearInterval(timer); };
  }, [waiting]);

  const on = Boolean(status && (status.enabled || status.remembered));
  const [confirmNew, setConfirmNew] = useState(false);
  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await newWebAccessPassword());
      setConfirmNew(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'A new password could not be made.');
    } finally {
      setBusy(false);
    }
  };
  const toggle = async () => {
    if (!status) return;
    setBusy(true);
    setError(null);
    try {
      setStatus(await setWebAccess(!on));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Web access could not be changed.');
    } finally {
      setBusy(false);
    }
  };

  const fromWeb = status?.opened_from_web ?? false;
  return <div className="max-w-xl space-y-4">
    <div className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <MonitorSmartphone className="mt-0.5 h-5 w-5 shrink-0 text-zinc-400" />
          <div>
            <p className="text-xs font-semibold text-zinc-100">Open this app in a web browser</p>
            <p className="mt-1 text-[11px] leading-5 text-zinc-500">
              {status?.enabled ? 'On. Your Tailscale devices can open the address below.'
                : status?.remembered ? (status.starting ? 'Turning back on after the app restarted…' : 'Set to on, but it could not start.')
                : 'Off. Turn it on to use the app from your phone or another computer.'}
            </p>
          </div>
        </div>
        {status && !fromWeb && <button type="button" onClick={() => { void toggle(); }} disabled={busy}
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-semibold disabled:opacity-60 ${on
            ? 'border border-zinc-700 text-zinc-200 hover:bg-zinc-800' : 'bg-blue-600 text-white hover:bg-blue-500'}`}>
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          {on ? 'Turn off' : 'Turn on web access'}
        </button>}
        {!status && !error && <Loader2 className="h-4 w-4 animate-spin text-zinc-500" aria-label="Loading" />}
      </div>

      {status?.enabled && status.url && <div className="space-y-3 border-t border-zinc-800 pt-3">
        <CopyField label="Address" value={status.url} />
        {status.username && <CopyField label="Username" value={status.username} />}
        {status.password && <CopyField label="Password" value={status.password} secret />}
        {status.password && (confirmNew
          ? <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
              <p className="min-w-0 flex-1 text-[11px] leading-5 text-amber-200/90">Devices signed in now will need the new password.</p>
              <button type="button" onClick={() => setConfirmNew(false)} disabled={busy}
                className="rounded-lg px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800">Cancel</button>
              <button type="button" onClick={() => { void regenerate(); }} disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-60">
                {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}Make new password
              </button>
            </div>
          : <button type="button" onClick={() => setConfirmNew(true)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800">
              <RefreshCw className="h-3.5 w-3.5" />Make a new password
            </button>)}
      </div>}

      {status?.error && !error && <p role="status" className="flex items-start gap-1.5 text-xs leading-5 text-amber-300">
        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{status.error}{status.starting ? ' Trying again every 30 seconds.' : ''}
      </p>}
      {error && <p role="alert" className="flex items-start gap-1.5 text-xs leading-5 text-red-400">
        <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}
      </p>}
      {fromWeb && <p className="text-[11px] leading-5 text-zinc-500">You opened the app from the web. Turn web access on or off, and see its password, in the app on the PC.</p>}
    </div>

    <ul className="list-disc space-y-1.5 pl-5 text-[11px] leading-5 text-zinc-500">
      <li>Only devices signed in to your Tailscale network can reach the address, and every visit asks for the username and password.</li>
      <li>It stays on when the app or PC restarts. The username and password stay the same, even if you turn it off and on, until you make a new password.</li>
      <li>Install Tailscale on your phone or other computer and sign in to the same account to use it.</li>
    </ul>
  </div>;
}
