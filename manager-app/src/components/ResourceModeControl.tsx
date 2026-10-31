import React, { useEffect, useState } from 'react';
import { Cpu, ChevronDown, Loader2 } from 'lucide-react';
import { Select } from './ui/Select';
import { fetchResourceMode, updateResourceMode, updateOcrEngine, updateAdaptiveLimits } from '../services/api';
import { AdaptiveLimitsUpdate, ResourceMode, ResourceModeSettings, OcrEngine } from '../types/automation';

export const ResourceModeControl: React.FC = () => {
  const [settings, setSettings] = useState<ResourceModeSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setSettings(await fetchResourceMode());
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to load resource mode');
    }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  const selectMode = async (mode: ResourceMode) => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await updateResourceMode(mode));
    } catch (err: any) {
      setError(err.message || 'Failed to update resource mode');
    } finally {
      setSaving(false);
    }
  };

  const updateLimits = async (limits: AdaptiveLimitsUpdate) => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await updateAdaptiveLimits(limits));
    } catch (err: any) {
      setError(err.message || 'Failed to update automatic limits');
    } finally {
      setSaving(false);
    }
  };

  const selectOcr = async (engine: OcrEngine) => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await updateOcrEngine(engine));
    } catch (err: any) {
      setError(err.message || 'Failed to update OCR engine');
    } finally {
      setSaving(false);
    }
  };

  const adaptive = settings?.adaptive;

  // Older backends can still return a launch lock; newer payloads omit it.
  const ocrLocked = !!settings?.ocr_engine && 'locked' in settings.ocr_engine
    && settings.ocr_engine.locked === true;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-[10px]" aria-label="Global resource controls">
      {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-400" /> : <Cpu className="h-3.5 w-3.5 text-violet-400" />}
      <Select ariaLabel="Global resource mode" value={settings?.selected_mode || ''} placeholder="Resources…"
        disabled={!settings || saving} onValueChange={value => selectMode(value as ResourceMode)}
        className="min-h-7 w-32 border-zinc-700 px-2 py-1 text-[10px] focus:border-violet-500 focus:ring-violet-500/20"
        options={(['auto', 'low', 'medium', 'high'] as ResourceMode[]).map(mode => ({
          value: mode,
          label: mode === 'auto' ? 'Automatic' : mode[0].toUpperCase() + mode.slice(1),
          disabled: !settings || (mode !== 'auto' && !settings.supported_modes[mode as 'low' | 'medium' | 'high']),
        }))} />
      <Select ariaLabel="OCR engine" value={settings?.ocr_engine?.selected || ''}
        placeholder="OCR…" disabled={!settings?.ocr_engine || ocrLocked || saving}
        onValueChange={value => selectOcr(value as OcrEngine)}
        className="min-h-7 w-52 border-zinc-700 px-2 py-1 text-[10px] focus:border-violet-500 focus:ring-violet-500/20"
        options={settings?.ocr_engine?.options || []} />
      {settings?.ocr_engine?.restart_required && (
        <span role="status" className="text-amber-400">Saved · Restart app/service to apply OCR</span>
      )}
      {ocrLocked && <span className="text-zinc-500">OCR set by launch configuration</span>}
      {settings && <details className="relative">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded px-2 py-1.5 text-zinc-400 outline-none hover:bg-zinc-800 focus-visible:ring-2 focus-visible:ring-violet-500/40 [&::-webkit-details-marker]:hidden" aria-label="Resource details">
          <span><strong className="font-medium text-zinc-200">{settings.runtime.active_profile_containers}/{settings.limits.max_active_profile_containers}</strong> containers</span>
          <span className="hidden xl:inline">RAM {settings.runtime.memory_used_percent}% · CPU {settings.runtime.sustained_cpu_percent}%</span>
          <ChevronDown className="h-3 w-3" />
        </summary>
        <div className="absolute right-0 top-full z-50 mt-2 w-64 space-y-3 rounded-xl border border-zinc-700 bg-zinc-950 p-4 text-xs shadow-xl">
          <div><p className="font-medium text-zinc-200">Global resources</p><p className="mt-1 text-zinc-500">{settings.hardware.total_memory_gb} GB · {settings.hardware.cpu_threads} threads</p></div>
          {adaptive?.enabled && <div className="space-y-2 border-b border-zinc-800 pb-3">
            <p className="text-zinc-300">
              <span className={adaptive.decision.state === 'calm' ? 'text-emerald-400' : adaptive.decision.state === 'busy' ? 'text-amber-400' : 'text-red-400'}>
                {adaptive.decision.state === 'calm' ? 'Calm' : adaptive.decision.state === 'busy' ? 'Busy' : 'Under pressure'}
              </span>{' · '}{adaptive.decision.limit} of up to {adaptive.decision.ceiling} jobs
            </p>
            <p className="text-zinc-500">{adaptive.decision.hold ? 'Pausing running jobs at safe steps: ' : ''}{adaptive.decision.reason}</p>
            <label className="flex items-center justify-between gap-2 text-zinc-400">Max parallel jobs
              <Select ariaLabel="Maximum parallel jobs" value={adaptive.max_parallel ? String(adaptive.max_parallel) : 'none'} disabled={saving}
                onValueChange={value => updateLimits({ max_parallel: value === 'none' ? null : Number(value) })}
                className="min-h-7 w-24 border-zinc-700 px-2 py-1 text-[10px]"
                options={[{ value: 'none', label: 'No limit' }, ...Array.from({ length: 16 }, (_, index) => ({ value: String(index + 1), label: String(index + 1) }))]} />
            </label>
            <label className="flex items-center justify-between gap-2 text-zinc-400">Keep free for me
              <Select ariaLabel="Resources kept free" value={String(adaptive.reserve_percent)} disabled={saving}
                onValueChange={value => updateLimits({ reserve_percent: Number(value) })}
                className="min-h-7 w-24 border-zinc-700 px-2 py-1 text-[10px]"
                options={[0, 10, 20, 30, 40].map(value => ({ value: String(value), label: `${value}%` }))} />
            </label>
          </div>}
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-zinc-400">
            <dt>Publish slots</dt><dd className="text-right text-zinc-200">{settings.limits.max_publishers}</dd>
            <dt>Prepare slots</dt><dd className="text-right text-zinc-200">{settings.limits.max_preparers}</dd>
            <dt>Containers</dt><dd className="text-right text-zinc-200">{settings.runtime.active_profile_containers}/{settings.limits.max_active_profile_containers}</dd>
            <dt>RAM usage</dt><dd className="text-right text-zinc-200">{settings.runtime.memory_used_percent}%</dd>
            <dt>CPU usage</dt><dd className="text-right text-zinc-200">{settings.runtime.sustained_cpu_percent}%</dd>
            <dt>OCR threads</dt><dd className="text-right text-zinc-200">{settings.limits.ocr_threads_per_worker}/worker</dd>
          </dl>
          <p className="border-t border-zinc-800 pt-3 text-zinc-300" title={settings.runtime.ocr_worker?.error || settings.hardware.ocr.gpu_name || settings.hardware.ocr.fallback_reason || undefined}>
            OCR: {settings.hardware.ocr.label}
            {settings.runtime.ocr_worker?.status === 'ready' ? ' · Shared' : ''}
            {settings.runtime.ocr_worker && ['starting', 'initializing'].includes(settings.runtime.ocr_worker.status) ? ' · Warming' : ''}
            {settings.runtime.ocr_worker && ['error', 'stopped'].includes(settings.runtime.ocr_worker.status) ? ' · Local fallback' : ''}
            {settings.hardware.ocr.device === 'cpu' && settings.hardware.ocr.nvidia_detected ? ' · CUDA unavailable' : ''}
          </p>
        </div>
      </details>}
      {error && <span role="alert" className="max-w-48 truncate text-red-400" title={error}>{error}</span>}
    </div>
  );
};
