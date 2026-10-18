import { useEffect, useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import { isDesktopApp } from '../services/desktopEnvironment';
import { AppUpdateInfo, AppUpdateProgress, checkAppUpdate, downloadAppUpdate,
  getAppVersion, installAppUpdate, listenToAppUpdateProgress } from '../services/appUpdates';

export function AppUpdates() {
  const [version, setVersion] = useState('');
  const [update, setUpdate] = useState<AppUpdateInfo | null>(null);
  const [busy, setBusy] = useState<'checking' | 'downloading' | 'installing' | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<AppUpdateProgress>({ downloaded: 0, total: null });
  const desktop = isDesktopApp();
  useEffect(() => {
    if (!desktop) return;
    let disposed = false;
    let stop: (() => void) | undefined;
    getAppVersion().then(v => { if (!disposed) setVersion(v); }).catch(e => { if (!disposed) setError(String(e)); });
    listenToAppUpdateProgress(p => { if (!disposed) setProgress(p); }).then(unlisten => {
      if (disposed) unlisten(); else stop = unlisten;
    }).catch(e => { if (!disposed) setError(String(e)); });
    return () => { disposed = true; stop?.(); };
  }, [desktop]);
  const check = async () => {
    setBusy('checking'); setError(''); setDownloaded(false); setUpdate(null); setChecked(false);
    try { const result = await checkAppUpdate(); setUpdate(result); setVersion(result.currentVersion); setChecked(true); }
    catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  };
  const download = async () => {
    setBusy('downloading'); setError(''); setProgress({ downloaded: 0, total: null });
    try { await downloadAppUpdate(); setDownloaded(true); }
    catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  };
  const install = async () => {
    setBusy('installing'); setError('');
    try { await installAppUpdate(); }
    catch (e) { setError(String(e)); setBusy(null); }
  };
  const percent = progress.total ? Math.min(100, Math.round(progress.downloaded / progress.total * 100)) : null;
  return <div className="max-w-xl space-y-4 text-xs text-zinc-300">
    <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-4">
      <p className="font-medium text-zinc-100">Installed app {version ? `v${version}` : ''}</p>
      <p className="mt-2 leading-5 text-zinc-400">App updates include the interface, backend, and automation. Your profiles, media, queues, and saved settings are preserved.</p>
    </div>
    {!desktop ? <p>Open the desktop app to check for updates.</p> : <>
      <button type="button" disabled={busy !== null} onClick={() => void check()} className="flex items-center gap-2 rounded-lg border border-zinc-700 px-4 py-2 disabled:opacity-50">
        <RefreshCw className={`h-3.5 w-3.5 ${busy === 'checking' ? 'animate-spin' : ''}`} />
        {busy === 'checking' ? 'Checking…' : 'Check for updates'}
      </button>
      {checked && update?.notice && <p role="status" className="rounded-lg border border-zinc-700 bg-zinc-900 p-3 leading-5 text-zinc-300">{update.notice}</p>}
      {checked && !update?.version && !update?.notice && <p role="status" className="text-emerald-400">You have the latest published stable version.</p>}
      {update?.version && <div className="space-y-3 rounded-xl border border-blue-500/25 bg-blue-500/5 p-4">
        <p className="font-semibold text-zinc-100">Version {update.version} is available</p>
        {update.notes && <p className="max-h-40 overflow-auto whitespace-pre-wrap leading-5 text-zinc-400">{update.notes}</p>}
        <button type="button" disabled={busy !== null} onClick={() => void (downloaded ? install() : download())}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white hover:bg-blue-500 disabled:opacity-50">
          <Download className="h-3.5 w-3.5" />
          {busy === 'installing' ? 'Installing…' : busy === 'downloading' ? `Downloading${percent === null ? '…' : ` ${percent}%`}` : downloaded ? 'Install and restart' : 'Download update'}
        </button>
        {downloaded && <p className="text-emerald-400">Download signature verified.</p>}
        <p className="leading-5 text-zinc-400">Installation waits until automation and live broadcasts are idle. Administrator approval may be required. After restart, setup will check whether runtime dependencies need an update.</p>
      </div>}
    </>}
    {error && <p role="alert" className="rounded-lg border border-red-500/25 bg-red-500/5 p-3 leading-5 text-red-300">{error}</p>}
  </div>;
}
