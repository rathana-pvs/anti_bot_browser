import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BadgeCheck,
  BrainCircuit,
  CheckCircle2,
  Loader2,
  PanelLeft,
  PanelLeftClose,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import { activateBrain, fetchBrains, rollbackBrain, uploadBrainPackage, validateBrain } from '../services/api';
import { BrainCatalogResponse, BrainVersion } from '../types/brain';
import { Select } from './ui/Select';

interface BrainPanelProps {
  embedded?: boolean;
  isSidebarOpen?: boolean;
  onToggleSidebar?: () => void;
}

function displayVersion(version?: BrainVersion): string {
  if (!version) return 'Unavailable';
  return version.directory === 'bundled_default'
    ? `Bundled Default · v${version.version || 'unknown'}`
    : `v${version.version || version.directory}`;
}

export const BrainPanel: React.FC<BrainPanelProps> = ({ embedded = false, isSidebarOpen, onToggleSidebar }) => {
  const uploadInputRef = React.useRef<HTMLInputElement | null>(null);
  const [catalog, setCatalog] = useState<BrainCatalogResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const load = useCallback(async () => {
    setBusyAction('refresh');
    try {
      const next = await fetchBrains();
      setCatalog(next);
      setSelectedId((current) => current && next.brains.some((brain) => brain.id === current)
        ? current
        : next.brains[0]?.id || null);
      setMessage(null);
    } catch (error: any) {
      setMessage({ kind: 'error', text: error.message || 'Could not load workflow Brains.' });
    } finally {
      setBusyAction(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const selected = useMemo(
    () => catalog?.brains.find((brain) => brain.id === selectedId) || null,
    [catalog, selectedId],
  );

  const perform = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusyAction(key);
    setMessage(null);
    try {
      await action();
      const next = await fetchBrains();
      setCatalog(next);
      setMessage({ kind: 'success', text: success });
    } catch (error: any) {
      setMessage({ kind: 'error', text: error.message || 'Brain operation failed.' });
    } finally {
      setBusyAction(null);
    }
  };

  const activeVersion = selected?.versions.find((version) => version.directory === selected.active_version);

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    await perform(
      'upload',
      () => uploadBrainPackage(file, false),
      `${file.name} was validated and installed. Review it below, then activate it when ready.`,
    );
  };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          {onToggleSidebar && (
            <button onClick={onToggleSidebar} aria-label={isSidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
              className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-white">
              {isSidebarOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeft className="h-4 w-4" />}
            </button>
          )}
          {!embedded && <BrainCircuit className="h-4 w-4 text-violet-400" />}
          <span className="text-xs text-zinc-400">{catalog ? `${catalog.brains.length} installed` : 'Installed brains'}</span>
          <span className="text-zinc-700">·</span>
          <span className="text-xs text-zinc-500">Engine {catalog?.engine_version || '—'}</span>
        </div>
        <div className="flex items-center gap-2">
          <input ref={uploadInputRef} type="file" accept=".zip,.tar.gz,.tgz,application/zip,application/gzip"
            onChange={handleUpload} className="hidden" />
          <button onClick={() => uploadInputRef.current?.click()} disabled={busyAction !== null}
            className="flex items-center gap-1.5 rounded-md bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500 disabled:opacity-50">
            {busyAction === 'upload' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            Upload package
          </button>
          <button onClick={load} disabled={busyAction !== null} aria-label="Refresh brains" title="Refresh brains"
            className="rounded-md border border-zinc-800 p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-white disabled:opacity-50">
            {busyAction === 'refresh' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {message && (
          <div role={message.kind === 'error' ? 'alert' : 'status'} className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${
            message.kind === 'success' ? 'border-emerald-900/50 bg-emerald-950/20 text-emerald-300' : 'border-rose-900/50 bg-rose-950/20 text-rose-300'
          }`}>
            {message.kind === 'success' ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
            {message.text}
          </div>
        )}
        {!catalog && busyAction === 'refresh' ? (
          <div className="flex h-32 items-center justify-center gap-2 text-xs text-zinc-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading brains…
          </div>
        ) : catalog?.brains.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-800 py-8 text-center text-xs text-zinc-500">No brain packages installed. Upload a package to get started.</div>
        ) : selected && (
          <>
            <Select ariaLabel="Select brain" value={selectedId || ''} onValueChange={setSelectedId}
              options={catalog?.brains.map((brain) => ({ value: brain.id, label: brain.versions.find(v => v.directory === brain.active_version)?.name || brain.id.replace(/_/g, ' ') })) || []} />
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-zinc-100">{activeVersion?.name || selected.id}</h3>
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{selected.channel}</span>
                </div>
                <span className="text-xs text-emerald-400">Active · {displayVersion(activeVersion)}</span>
              </div>
              <p className="mt-1 text-xs leading-5 text-zinc-500">{activeVersion?.description || 'Workflow package.'}</p>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-400">
                <span>API {activeVersion?.brain_api_version != null ? `v${activeVersion.brain_api_version}` : '—'}</span>
                <span>Languages: {activeVersion?.supported_locales?.join(', ') || '—'}</span>
                <span>Themes: {activeVersion?.supported_themes?.join(', ') || '—'}</span>
              </div>
            </div>
            <section className="overflow-hidden rounded-lg border border-zinc-800">
              <div className="flex items-center justify-between gap-2 border-b border-zinc-800 bg-zinc-900/40 px-3 py-2">
                <h4 className="text-xs font-medium text-zinc-300">Versions <span className="ml-1 text-zinc-600">{selected.versions.length}</span></h4>
                <button onClick={() => perform(`rollback:${selected.id}`, () => rollbackBrain(selected.id), 'Rolled back to the previous validated version.')}
                  disabled={!selected.previous_version || busyAction !== null}
                  className="flex items-center gap-1.5 rounded px-2 py-1 text-[11px] text-zinc-400 hover:bg-zinc-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-35">
                  <RotateCcw className="h-3 w-3" /> Roll back
                </button>
              </div>
              <div className="divide-y divide-zinc-800/60">
                {selected.versions.map((version) => {
                  const isActive = version.directory === selected.active_version;
                  const key = `${selected.id}:${version.directory}`;
                  return (
                    <div key={key} className="px-3 py-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          {version.status === 'valid' ? <BadgeCheck className="h-3.5 w-3.5 shrink-0 text-emerald-400" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-400" />}
                          <span className="text-xs font-medium text-zinc-200">{displayVersion(version)}</span>
                          {isActive && <span className="rounded bg-violet-500/10 px-1.5 py-0.5 text-[10px] text-violet-300">Active</span>}
                          <span className="text-[10px] text-zinc-600">{version.install_source === 'local_upload' ? 'Local upload' : version.install_source === 'bundled' ? 'Bundled' : version.install_source || 'Local'}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <button onClick={() => perform(`validate:${key}`, () => validateBrain(selected.id, version.directory), `${displayVersion(version)} passed schema and compatibility validation.`)}
                            disabled={busyAction !== null || version.status !== 'valid'}
                            className="rounded border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-40">
                            {busyAction === `validate:${key}` ? 'Validating…' : 'Validate'}
                          </button>
                          {!isActive && <button onClick={() => perform(`activate:${key}`, () => activateBrain(selected.id, version.directory), `${displayVersion(version)} is now active for new jobs.`)}
                            disabled={busyAction !== null || version.status !== 'valid'}
                            className="rounded bg-violet-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-violet-500 disabled:opacity-35">
                            {busyAction === `activate:${key}` ? 'Activating…' : 'Activate'}
                          </button>}
                        </div>
                      </div>
                      {version.error && <p className="mt-1 break-words text-[11px] text-red-400">{version.error}</p>}
                      <details className="mt-1.5 text-[11px]">
                        <summary className="w-fit cursor-pointer text-zinc-500 hover:text-zinc-300">Package details</summary>
                        <div className="mt-2 space-y-2 rounded bg-zinc-950 p-2.5 text-zinc-400">
                          {version.description && <p className="leading-5">{version.description}</p>}
                          {version.change_log && <div><span className="text-zinc-600">Release notes</span><p className="mt-1 whitespace-pre-wrap break-words leading-5">{version.change_log}</p></div>}
                          <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-1.5">
                            <dt className="text-zinc-600">Validation</dt><dd>{version.status}</dd>
                            <dt className="text-zinc-600">Brain API</dt><dd>{version.brain_api_version != null ? `v${version.brain_api_version}` : '—'}</dd>
                            <dt className="text-zinc-600">Languages</dt><dd>{version.supported_locales?.join(', ') || '—'}</dd>
                            <dt className="text-zinc-600">Themes</dt><dd>{version.supported_themes?.join(', ') || '—'}</dd>
                            <dt className="text-zinc-600">Directory</dt><dd className="break-all font-mono">{version.directory}</dd>
                            <dt className="text-zinc-600">Digest</dt><dd className="break-all font-mono select-text">{version.digest || '—'}</dd>
                            {version.archive_sha256 && <><dt className="text-zinc-600">Archive digest</dt><dd className="break-all font-mono select-text">{version.archive_sha256}</dd></>}
                            {version.released_at && <><dt className="text-zinc-600">Released</dt><dd>{new Date(version.released_at).toLocaleString()}</dd></>}
                            {version.installed_at && <><dt className="text-zinc-600">Installed</dt><dd>{new Date(version.installed_at).toLocaleString()}</dd></>}
                          </dl>
                        </div>
                      </details>
                    </div>
                  );
                })}
              </div>
            </section>
            <details className="text-[11px] text-zinc-500">
              <summary className="flex w-fit cursor-pointer items-center gap-1.5 hover:text-zinc-300"><ShieldCheck className="h-3.5 w-3.5" /> Runtime safeguards</summary>
              <p className="mt-2 leading-5">Packages are checked for schema and engine compatibility. Brains contain no executable code. Running jobs keep their selected version, and the host engine checks the live screen before publishing.</p>
            </details>
          </>
        )}
      </div>
    </div>
  );
};
