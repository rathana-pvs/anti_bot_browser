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
        options={(['auto', 'extreme', 'custom'] as ResourceMode[]).map(mode => ({
          value: mode,
          label: mode === 'auto' ? 'Auto' : mode[0].toUpperCase() + mode.slice(1),
          disabled: !settings,
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
      {settings && <details className="w-full min-w-0">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded px-2 py-1.5 text-zinc-400 outline-none hover:bg-zinc-800 focus-visible:ring-2 focus-visible:ring-violet-500/40 [&::-webkit-details-marker]:hidden" aria-label="Resource details">
          <span><strong className="font-medium text-zinc-200">{settings.runtime.active_profile_containers}/{settings.limits.max_active_profile_containers}</strong> containers</span>
          <span className="hidden xl:inline">RAM {settings.runtime.memory_used_percent}% · CPU {settings.runtime.sustained_cpu_percent}%</span>
          <ChevronDown className="h-3 w-3" />
        </summary>
        <div className="mt-2 w-full min-w-0 space-y-3 rounded-xl border border-zinc-700 bg-zinc-950 p-4 text-xs">
          <div><p className="font-medium text-zinc-200">Global resources</p><p className="mt-1 text-zinc-500">{settings.hardware.total_memory_gb} GB · {settings.hardware.cpu_threads} threads</p></div>
          {adaptive?.enabled && <div className="space-y-2 border-b border-zinc-800 pb-3">
            <p className="text-zinc-300">
              <span className={adaptive.decision.state === 'calm' ? 'text-emerald-400' : adaptive.decision.state === 'busy' ? 'text-amber-400' : 'text-red-400'}>
                {adaptive.decision.state === 'calm' ? 'Calm' : adaptive.decision.state === 'busy' ? 'Busy' : 'Under pressure'}
              </span>{' · '}{adaptive.decision.limit} of up to {adaptive.decision.ceiling} jobs
            </p>
            <p className="text-zinc-500">{adaptive.decision.hold ? 'Pausing running jobs at safe steps: ' : ''}{adaptive.decision.reason}</p>
          </div>}
          <p className="text-zinc-400">{settings.selected_mode === 'auto'
            ? 'Auto balances parallel accounts with desktop headroom and grows gradually when resources stay comfortable.'
            : settings.selected_mode === 'extreme'
              ? 'Extreme grows faster and uses more resources, up to the resource forecast and a maximum of 16 accounts.'
              : 'Custom uses your limits. Resource pressure can delay new work.'}</p>
          {settings.selected_mode === 'custom' && adaptive?.custom && <div className="space-y-3 border-b border-zinc-800 pb-3">
            {([
              ['max_parallel', 'Maximum active accounts', 'Total accounts sharing posts, Live and preparation slots.'],
              ['max_posts', 'Parallel posts (Reels, images, etc.)', 'Maximum accounts posting at the same time.'],
              ['max_live', 'Parallel Live broadcasts', 'Maximum accounts streaming at the same time.'],
            ] as const).map(([key, label, hint]) => <div key={key}>
              <label className="flex items-center justify-between gap-2 text-zinc-300">{label}
                <Select ariaLabel={label} value={String(adaptive.custom[key])} disabled={saving}
                  onValueChange={value => updateLimits({ [key]: Number(value) })}
                  className="min-h-7 w-16 shrink-0 border-zinc-700 px-2 py-1 text-[10px]"
                  options={Array.from({ length: key === 'max_parallel' ? 16 : adaptive.custom.max_parallel }, (_, index) => ({ value: String(index + 1), label: String(index + 1) }))} />
              </label>
              <p className="mt-1 text-[11px] text-zinc-500">{hint}</p>
            </div>)}
            <label className="flex items-center justify-between gap-2 text-zinc-300">Resource reserve
              <Select ariaLabel="Resource reserve" value={String(adaptive.custom.reserve_percent)} disabled={saving}
                onValueChange={value => updateLimits({ reserve_percent: Number(value) })}
                className="min-h-7 w-20 shrink-0 border-zinc-700 px-2 py-1 text-[10px]"
                options={[0, 10, 20, 30, 40].map(value => ({ value: String(value), label: `${value}%` }))} />
            </label>
            <p className="text-[11px] text-zinc-500">Posts and Live share the total account limit. The reserve leaves additional CPU and memory headroom.</p>
          </div>}
          <p className="text-[11px] text-zinc-500">Profile starts are at least 10 seconds apart in every mode. Lowering limits lets active jobs finish; new jobs wait.</p>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-zinc-400">
            <dt>Post slots</dt><dd className="text-right text-zinc-200">{settings.limits.max_post_publishers}</dd>
            <dt>Live slots</dt><dd className="text-right text-zinc-200">{settings.limits.max_live_publishers}</dd>
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
