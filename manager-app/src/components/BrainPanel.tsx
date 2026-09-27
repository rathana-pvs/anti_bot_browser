import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BadgeCheck,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  FileCode2,
  Fingerprint,
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

interface BrainPanelProps {
  isSidebarOpen?: boolean;
  onToggleSidebar?: () => void;
}

function displayVersion(version?: BrainVersion): string {
  if (!version) return 'Unavailable';
  return version.directory === 'bundled_default'
    ? `Bundled Default · v${version.version || 'unknown'}`
    : `v${version.version || version.directory}`;
}

export const BrainPanel: React.FC<BrainPanelProps> = ({ isSidebarOpen, onToggleSidebar }) => {
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
    <div className="flex-1 min-w-0 flex flex-col h-full bg-background overflow-hidden">
      <header className="h-14 border-b border-border px-3 sm:px-6 flex items-center justify-between gap-2 shrink-0 bg-surface/50">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          {onToggleSidebar && (
            <button
              onClick={onToggleSidebar}
              className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors mr-1"
              title={isSidebarOpen ? 'Collapse Sidebar' : 'Expand Sidebar'}
            >
              {isSidebarOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeft className="w-4 h-4" />}
            </button>
          )}
          <div className="p-2 rounded-lg bg-violet-500/10 border border-violet-500/30 text-violet-300">
            <BrainCircuit className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-zinc-100 truncate">Workflow Brains</h2>
            <p className="hidden sm:block text-xs text-zinc-400 truncate">Validated workflow contracts, immutable versions, and safe activation.</p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <input
            ref={uploadInputRef}
            type="file"
            accept=".zip,.tar.gz,.tgz,application/zip,application/gzip"
            onChange={handleUpload}
            className="hidden"
          />
          <button
            onClick={() => uploadInputRef.current?.click()}
            disabled={busyAction !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-violet-500 text-white hover:bg-violet-400 disabled:opacity-50 text-xs font-medium transition-colors"
            title="Upload a local Brain package"
          >
            {busyAction === 'upload' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            <span className="hidden sm:inline">Upload package</span>
          </button>
          <button
            onClick={load}
            disabled={busyAction !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-zinc-900 border border-zinc-800 text-zinc-300 hover:text-white disabled:opacity-50 text-xs font-medium transition-colors"
            title="Refresh local registry"
          >
            {busyAction === 'refresh' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </header>

      {message && (
        <div className={`mx-6 mt-4 p-3 rounded-lg border text-xs flex items-center gap-2 ${
          message.kind === 'success'
            ? 'bg-emerald-950/60 border-emerald-800/80 text-emerald-300'
            : 'bg-rose-950/60 border-rose-800/80 text-rose-300'
        }`}>
          {message.kind === 'success' ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
          {message.text}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-3 sm:p-6">
        <div className="max-w-6xl mx-auto space-y-5">
          <section className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <StatusCard icon={<ShieldCheck className="w-4 h-4" />} label="Runtime boundary" value="Capability limited" detail="No executable Brain code" tone="emerald" />
            <StatusCard icon={<FileCode2 className="w-4 h-4" />} label="Engine contract" value={`API v1 · Engine ${catalog?.engine_version || '—'}`} detail="Schema checked before use" tone="violet" />
            <StatusCard icon={<Fingerprint className="w-4 h-4" />} label="Job behavior" value="Version pinned" detail="Running jobs cannot switch" tone="sky" />
          </section>

          {!catalog && busyAction === 'refresh' ? (
            <div className="h-64 flex items-center justify-center text-zinc-500 text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" /> Loading Brain registry…
            </div>
          ) : catalog?.brains.length === 0 ? (
            <div className="border border-dashed border-zinc-800 rounded-xl p-12 text-center text-zinc-500 text-sm">
              No Brain packages are installed.
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)] gap-5">
              <aside className="rounded-xl border border-zinc-800 bg-zinc-950/60 overflow-hidden self-start">
                <div className="px-4 py-3 border-b border-zinc-800 text-[11px] font-semibold tracking-wider text-zinc-500 uppercase">
                  Installed families
                </div>
                {catalog?.brains.map((brain) => (
                  <button
                    key={brain.id}
                    onClick={() => setSelectedId(brain.id)}
                    className={`w-full px-4 py-3 flex items-center justify-between text-left border-b border-zinc-900 last:border-0 transition-colors ${
                      selectedId === brain.id ? 'bg-violet-500/10' : 'hover:bg-zinc-900/80'
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-zinc-200 truncate">{brain.id.replace(/_/g, ' ')}</div>
                      <div className="text-[11px] text-zinc-500 mt-0.5">{brain.versions.length} installed version{brain.versions.length === 1 ? '' : 's'}</div>
                    </div>
                    <ChevronRight className="w-4 h-4 text-zinc-600" />
                  </button>
                ))}
              </aside>

              {selected && (
                <main className="space-y-4 min-w-0">
                  <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-5">
                    <div className="flex flex-col sm:flex-row items-start justify-between gap-3 sm:gap-4">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="text-base font-semibold text-zinc-100">{activeVersion?.name || selected.id}</h3>
                          <span className="px-2 py-0.5 rounded-full text-[10px] uppercase tracking-wide bg-emerald-500/10 text-emerald-300 border border-emerald-500/30">
                            {selected.channel}
                          </span>
                        </div>
                        <p className="text-xs text-zinc-400 mt-1 max-w-2xl">{activeVersion?.description || 'Declarative workflow package.'}</p>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="text-[10px] uppercase tracking-wider text-zinc-500">Active</div>
                        <div className="text-sm text-emerald-300 font-medium mt-1">{displayVersion(activeVersion || selected.versions[0])}</div>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5 pt-4 border-t border-zinc-800/80">
                      <Metric label="Brain API" value={`v${activeVersion?.brain_api_version || '—'}`} />
                      <Metric label="Locales" value={activeVersion?.supported_locales?.join(', ') || '—'} />
                      <Metric label="Themes" value={activeVersion?.supported_themes?.join(', ') || '—'} />
                      <Metric label="Digest" value={activeVersion?.digest?.slice(0, 12) || '—'} mono />
                    </div>
                  </div>

                  <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 overflow-hidden">
                    <div className="px-5 py-3 border-b border-zinc-800 flex items-center justify-between">
                      <div>
                        <h4 className="text-sm font-medium text-zinc-200">Installed versions</h4>
                        <p className="text-[11px] text-zinc-500 mt-0.5">Only validated packages can be activated.</p>
                      </div>
                      <button
                        onClick={() => perform(`rollback:${selected.id}`, () => rollbackBrain(selected.id), 'Rolled back to the previous validated version.')}
                        disabled={!selected.previous_version || busyAction !== null}
                        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-zinc-700 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-35 disabled:cursor-not-allowed"
                      >
                        <RotateCcw className="w-3.5 h-3.5" /> Roll back
                      </button>
                    </div>
                    <div className="divide-y divide-zinc-900">
                      {selected.versions.map((version) => {
                        const isActive = version.directory === selected.active_version;
                        const key = `${selected.id}:${version.directory}`;
                        return (
                          <div key={version.directory} className="p-4 flex flex-wrap sm:flex-nowrap items-center gap-3 sm:gap-4">
                            <div className={`w-9 h-9 rounded-lg flex items-center justify-center border ${
                              version.status === 'valid'
                                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
                                : 'bg-rose-500/10 border-rose-500/20 text-rose-300'
                            }`}>
                              {version.status === 'valid' ? <BadgeCheck className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-zinc-200">{displayVersion(version)}</span>
                                {isActive && <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/10 border border-violet-500/30 text-violet-300">ACTIVE</span>}
                                <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-400 uppercase">
                                  {version.install_source === 'local_upload' ? 'Local upload' : 'Bundled'}
                                </span>
                              </div>
                              <p className="text-[11px] text-zinc-500 mt-1 truncate">{version.status === 'valid' ? version.change_log : version.error}</p>
                            </div>
                            <button
                              onClick={() => perform(`validate:${key}`, () => validateBrain(selected.id, version.directory), `${displayVersion(version)} passed schema and compatibility validation.`)}
                              disabled={busyAction !== null || version.status !== 'valid'}
                              className="px-2.5 py-1.5 rounded border border-zinc-700 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
                            >
                              {busyAction === `validate:${key}` ? 'Validating…' : 'Validate'}
                            </button>
                            <button
                              onClick={() => perform(`activate:${key}`, () => activateBrain(selected.id, version.directory), `${displayVersion(version)} is now active for new jobs.`)}
                              disabled={busyAction !== null || isActive || version.status !== 'valid'}
                              className="px-3 py-1.5 rounded bg-violet-500 text-white text-xs font-medium hover:bg-violet-400 disabled:opacity-35 disabled:cursor-not-allowed"
                            >
                              {isActive ? 'Active' : busyAction === `activate:${key}` ? 'Activating…' : 'Activate'}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 flex items-start gap-3">
                    <ShieldCheck className="w-4 h-4 text-amber-300 mt-0.5 shrink-0" />
                    <div>
                      <div className="text-xs font-medium text-amber-200">Safety boundary enabled</div>
                      <p className="text-[11px] text-amber-200/60 mt-1">This first slice applies validated locator labels. The host engine retains the one-shot publish gate and rechecks the live screen before sending the final click.</p>
                    </div>
                  </div>
                </main>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const StatusCard: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: 'emerald' | 'violet' | 'sky';
}> = ({ icon, label, value, detail, tone }) => {
  const tones = {
    emerald: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20',
    violet: 'text-violet-300 bg-violet-500/10 border-violet-500/20',
    sky: 'text-sky-300 bg-sky-500/10 border-sky-500/20',
  };
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-lg border flex items-center justify-center ${tones[tone]}`}>{icon}</div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
        <div className="text-sm font-medium text-zinc-200 mt-0.5">{value}</div>
        <div className="text-[11px] text-zinc-500">{detail}</div>
      </div>
    </div>
  );
};

const Metric: React.FC<{ label: string; value: string; mono?: boolean }> = ({ label, value, mono }) => (
  <div>
    <div className="text-[10px] uppercase tracking-wider text-zinc-600">{label}</div>
    <div className={`text-xs text-zinc-300 mt-1 truncate ${mono ? 'font-mono' : ''}`}>{value}</div>
  </div>
);
