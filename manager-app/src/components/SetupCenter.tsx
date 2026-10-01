import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  Clipboard,
  Loader2,
  RefreshCw,
  Settings2,
  TerminalSquare,
} from 'lucide-react';
import {
  fetchSetupStatus,
  fetchSetupLog,
  listenToSetupLogs,
  recoverServices,
  SetupLogEvent,
  SetupSnapshot,
  startSetup,
} from '../services/setup';

interface SetupCenterProps {
  initialStatus: SetupSnapshot;
  onComplete: () => void;
}

interface DisplayLog extends SetupLogEvent {
  time: string;
}

export const SetupCenter: React.FC<SetupCenterProps> = ({ initialStatus, onComplete }) => {
  const [snapshot, setSnapshot] = useState(initialStatus);
  const [running, setRunning] = useState(false);
  const [checking, setChecking] = useState(false);
  const [showDetails, setShowDetails] = useState(true);
  const [logs, setLogs] = useState<DisplayLog[]>([]);
  const [persistedLogs, setPersistedLogs] = useState<string[]>([]);
  const [resultMessage, setResultMessage] = useState<string | null>(null);
  const [restartRequired, setRestartRequired] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  const completedCount = useMemo(
    () => snapshot.steps.filter((step) => step.status === 'ready').length,
    [snapshot.steps],
  );
  const progress = Math.round((completedCount / snapshot.steps.length) * 100);
  const activeStepIndex = snapshot.steps.findIndex((step) => step.status === 'action_required');
  const runtimeReady = snapshot.steps.find((step) => step.id === 'runtime')?.status === 'ready';
  const configurationReady = snapshot.steps.find((step) => step.id === 'configuration')?.status === 'ready';
  const gpuReady = snapshot.steps.find((step) => step.id === 'gpu')?.status === 'ready';
  const platformStep = snapshot.steps.find((step) => step.id === 'platform');
  const platformRecoverable = platformStep?.status === 'ready' || platformStep?.detail.includes('installed');
  const useServiceRecovery = runtimeReady && configurationReady && platformRecoverable && gpuReady;
  const visibleLogText = useMemo(() => {
    const liveMessages = logs.map((entry) => entry.message.trim()).filter(Boolean);
    const liveLines = logs.map((entry) => `[${entry.time}] ${entry.message}`);
    const persistedOnly = persistedLogs.filter((line) => {
      const normalized = line.trim();
      return !liveMessages.some((message) => normalized === message || normalized.endsWith(message));
    });
    return [...liveLines, ...persistedOnly].slice(-500);
  }, [logs, persistedLogs]);

  useEffect(() => {
    let disposed = false;
    let stopListening: (() => void) | undefined;
    listenToSetupLogs((entry) => {
      if (disposed) return;
      setLogs((current) => [...current.slice(-499), {
        ...entry,
        time: new Date().toLocaleTimeString([], { hour12: false }),
      }]);
    }).then((unlisten) => {
      if (disposed) unlisten();
      else stopListening = unlisten;
    });
    return () => {
      disposed = true;
      stopListening?.();
    };
  }, []);

  useEffect(() => {
    if (!running) return;
    let disposed = false;
    let pollingStatus = false;
    let pollingLog = false;
    const pollStatus = async () => {
      if (pollingStatus) return;
      pollingStatus = true;
      try {
        const latest = await fetchSetupStatus();
        if (!disposed) setSnapshot(latest);
      } catch {
        // Setup output is more useful than a transient readiness-probe error.
      } finally {
        pollingStatus = false;
      }
    };
    const pollLog = async () => {
      if (pollingLog) return;
      pollingLog = true;
      try {
        const latest = await fetchSetupLog();
        if (!disposed) setPersistedLogs(latest);
      } catch {
        // Live Tauri events remain available if a single file read fails.
      } finally {
        pollingLog = false;
      }
    };
    void pollStatus();
    void pollLog();
    const statusInterval = window.setInterval(() => void pollStatus(), 5000);
    const logInterval = window.setInterval(() => void pollLog(), 1000);
    return () => {
      disposed = true;
      window.clearInterval(statusInterval);
      window.clearInterval(logInterval);
    };
  }, [running]);

  useEffect(() => {
    const container = logContainerRef.current;
    if (!container || !showDetails) return;
    container.scrollTop = container.scrollHeight;
  }, [visibleLogText, running, showDetails]);

  const refresh = async () => {
    setChecking(true);
    try {
      const latest = await fetchSetupStatus();
      setSnapshot(latest);
      return latest;
    } finally {
      setChecking(false);
    }
  };

  const run = async () => {
    setRunning(true);
    setPersistedLogs([]);
    setResultMessage(null);
    setRestartRequired(false);
    setLogs((current) => [...current, {
      stream: 'system',
      message: `Starting ${completedCount > 0 ? 'setup and repair' : 'initial setup'}…`,
      time: new Date().toLocaleTimeString([], { hour12: false }),
    }]);
    try {
      const result = useServiceRecovery
        ? await recoverServices()
        : await startSetup(completedCount > 0);
      setResultMessage(result.message);
      setRestartRequired(result.restartRequired);
      const latest = await refresh();
      if (latest.ready) setResultMessage('Your computer is ready.');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setResultMessage(message);
      setLogs((current) => [...current, {
        stream: 'stderr',
        message,
        time: new Date().toLocaleTimeString([], { hour12: false }),
      }]);
    } finally {
      setRunning(false);
    }
  };

  const copyLogs = async () => {
    const content = visibleLogText.join('\n');
    await navigator.clipboard.writeText(content);
  };

  return (
    <main className="h-screen overflow-hidden bg-[#08090c] text-zinc-100">
      <div className="mx-auto flex h-full max-w-6xl flex-col px-6 py-8 lg:px-10">
        <header className="mb-7 flex shrink-0 items-start justify-between gap-6">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-300">
              <Settings2 size={13} /> First-time setup · {snapshot.platform === 'windows' ? 'Windows' : 'Linux'}
            </div>
            <h1 className="text-3xl font-semibold tracking-tight">Prepare Automat FB</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
              The setup assistant installs the required platform, prepares the private runtime,
              applies safe defaults, and verifies the browser environment.
            </p>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={running || checking}
            className="mt-1 inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-300 transition hover:bg-zinc-800 disabled:opacity-50"
          >
            <RefreshCw size={15} className={checking ? 'animate-spin' : ''} /> Check again
          </button>
        </header>

        <section className="grid min-h-0 flex-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.78fr)]">
          <div className="min-h-0 overflow-y-auto rounded-2xl border border-zinc-800 bg-zinc-950/70 p-5 shadow-2xl shadow-black/20">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <h2 className="font-medium">Computer readiness</h2>
                <p className="mt-1 text-xs text-zinc-500">{completedCount} of {snapshot.steps.length} checks completed</p>
              </div>
              <span className="text-sm font-semibold text-blue-300">{progress}%</span>
            </div>
            <div className="mb-5 h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div className="h-full rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${progress}%` }} />
            </div>

            <div className="space-y-2">
              {snapshot.steps.map((step, index) => {
                const ready = step.status === 'ready';
                const waiting = step.status === 'waiting';
                return (
                  <div key={step.id} className={`flex gap-3 rounded-xl border p-3.5 ${ready ? 'border-emerald-500/15 bg-emerald-500/[0.04]' : 'border-zinc-800 bg-zinc-900/55'}`}>
                    <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${ready ? 'bg-emerald-500/15 text-emerald-400' : waiting ? 'bg-zinc-800 text-zinc-600' : 'bg-amber-500/15 text-amber-400'}`}>
                      {ready ? <Check size={15} strokeWidth={2.5} /> : waiting ? <Circle size={12} /> : running && index === activeStepIndex ? <Loader2 size={14} className="animate-spin" /> : <span className="text-xs font-semibold">{index + 1}</span>}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className={`text-sm font-medium ${waiting ? 'text-zinc-500' : 'text-zinc-200'}`}>{step.title}</h3>
                        <span className={`text-[11px] font-medium uppercase tracking-wide ${ready ? 'text-emerald-400' : waiting ? 'text-zinc-600' : 'text-amber-400'}`}>
                          {ready ? 'Ready' : waiting ? 'Waiting' : 'Required'}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-zinc-500">{step.description}</p>
                      <p className={`mt-1.5 text-xs ${ready ? 'text-emerald-400/70' : 'text-zinc-400'}`}>{step.detail}</p>
                      {step.id === 'gpu' && runtimeReady && (
                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
                          <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                            <div className="text-[10px] uppercase tracking-wide text-zinc-600">Host GPU</div>
                            <div className="mt-0.5 truncate text-xs text-zinc-300" title={snapshot.gpu.hostGpu || undefined}>
                              {snapshot.gpu.hostGpu || 'Not reported by the host'}
                            </div>
                          </div>
                          <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                            <div className="text-[10px] uppercase tracking-wide text-zinc-600">{snapshot.platform === 'windows' ? 'WSL GPU visibility' : 'Runtime GPU visibility'}</div>
                            <div className={`mt-0.5 text-xs ${snapshot.gpu.wslGpuVisible ? 'text-emerald-300' : 'text-zinc-400'}`}>
                              {snapshot.gpu.wslGpuVisible ? 'NVIDIA runtime visible' : 'No NVIDIA runtime detected'}
                            </div>
                          </div>
                          <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                            <div className="text-[10px] uppercase tracking-wide text-zinc-600">Browser rendering</div>
                            <div className={`mt-0.5 text-xs ${snapshot.gpu.browserAccelerationAvailable ? 'text-emerald-300' : 'text-amber-300'}`}>
                              {snapshot.gpu.browserAccelerationAvailable ? 'Hardware device available' : 'Software fallback'}
                            </div>
                          </div>
                          <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                            <div className="text-[10px] uppercase tracking-wide text-zinc-600">OCR acceleration</div>
                            <div className={`mt-0.5 text-xs ${snapshot.gpu.cudaRuntimeAvailable ? 'text-emerald-300' : 'text-amber-300'}`}>
                              {snapshot.gpu.runtimeInstallRequired
                                ? 'Package installation required'
                                : snapshot.gpu.cudaRuntimeAvailable
                                ? `CUDA · ${snapshot.gpu.cudaDevice || 'NVIDIA GPU'}`
                                : 'CPU fallback'}
                            </div>
                          </div>
                          {snapshot.gpu.fallbackReason && (
                            <p className="sm:col-span-2 text-[11px] leading-5 text-amber-300/80">{snapshot.gpu.fallbackReason}</p>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {resultMessage && (
              <div className={`mt-4 flex gap-2 rounded-lg border px-3 py-2.5 text-sm ${snapshot.ready ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-200'}`}>
                {snapshot.ready ? <Check size={17} className="mt-0.5 shrink-0" /> : <AlertCircle size={17} className="mt-0.5 shrink-0" />}
                <div>{resultMessage}{restartRequired && <div className="mt-1 text-xs opacity-75">Restart or sign out if requested, then reopen Automat FB. Setup will continue from this step.</div>}</div>
              </div>
            )}

            <div className="mt-5 flex gap-3">
              {snapshot.ready ? (
                <button type="button" onClick={onComplete} className="flex-1 rounded-xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-blue-500">
                  Open Automat FB
                </button>
              ) : (
                <button type="button" onClick={run} disabled={running} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-wait disabled:opacity-60">
                  {running && <Loader2 size={16} className="animate-spin" />}
                  {running ? (useServiceRecovery ? 'Starting required services…' : 'Setting up your computer…') : useServiceRecovery ? 'Start required services' : completedCount > 0 ? 'Continue setup and repair' : 'Set up my computer'}
                </button>
              )}
            </div>
            <p className="mt-3 text-center text-[11px] leading-5 text-zinc-600">
              Administrator approval or a restart may be requested for system components. Existing configuration is preserved.
            </p>
          </div>

          <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-[#0b0d10] shadow-2xl shadow-black/20">
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-medium text-zinc-300">
                <TerminalSquare size={16} className="text-zinc-500" /> Setup activity
              </div>
              <div className="flex items-center gap-1">
                <button type="button" onClick={copyLogs} disabled={visibleLogText.length === 0} title="Copy logs" className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300 disabled:opacity-30">
                  <Clipboard size={14} />
                </button>
                <button type="button" onClick={() => setShowDetails((value) => !value)} className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300">
                  {showDetails ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
                </button>
              </div>
            </div>
            {showDetails && (
              <div ref={logContainerRef} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4 font-mono text-[11px] leading-5">
                {visibleLogText.length === 0 ? (
                  <div className="text-zinc-600">Setup activity will appear here after you begin.</div>
                ) : visibleLogText.map((line, index) => (
                  <div key={`${index}-${line}`} className="whitespace-pre-wrap break-words text-zinc-400">
                    {line}
                  </div>
                ))}
                {running && (
                  <div className="mt-1 flex items-center gap-2 text-blue-300">
                    <Loader2 size={11} className="animate-spin" />
                    {visibleLogText.length > 1 ? 'Installer running · live output' : 'Starting installer · waiting for output'}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
};
