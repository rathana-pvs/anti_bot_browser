import React, { useEffect, useState } from 'react';
import { Cpu, Loader2 } from 'lucide-react';
import { fetchResourceMode, updateResourceMode } from '../services/api';
import { ResourceMode, ResourceModeSettings } from '../types/automation';

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

  return (
    <div className="mx-6 mt-3 p-3 rounded-xl bg-zinc-900/70 border border-zinc-800/80 flex flex-wrap items-center gap-3 shrink-0">
      <div className="flex items-center gap-2 min-w-[190px]">
        <Cpu className="w-4 h-4 text-violet-400" />
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500">Global Resource Mode</div>
          <div className="text-xs text-zinc-300">
            {settings ? `${settings.hardware.total_memory_gb} GB · ${settings.hardware.cpu_threads} threads` : 'Detecting hardware…'}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1.5">
        {(['auto', 'low', 'medium', 'high'] as ResourceMode[]).map((mode) => {
          const supported = Boolean(settings) && (
            mode === 'auto' || settings!.supported_modes[mode as 'low' | 'medium' | 'high']
          );
          const selected = settings?.selected_mode === mode;
          return (
            <button
              key={mode}
              type="button"
              disabled={!supported || saving}
              onClick={() => selectMode(mode)}
              className={`px-2.5 py-1.5 rounded-lg border text-[10px] font-semibold uppercase transition-all ${
                selected
                  ? 'bg-violet-600/20 border-violet-500 text-violet-200'
                  : supported
                    ? 'bg-zinc-950 border-zinc-800 text-zinc-400 hover:text-zinc-200'
                    : 'bg-zinc-950/40 border-zinc-900 text-zinc-700 cursor-not-allowed'
              }`}
            >
              {saving && selected ? <Loader2 className="w-3 h-3 animate-spin" /> : mode}
              {mode === 'auto' && settings ? ` (${settings.effective_mode})` : ''}
            </button>
          );
        })}
      </div>

      {settings && (
        <div className="ml-auto flex flex-wrap items-center gap-3 text-[10px] font-mono text-zinc-500">
          <span><strong className="text-zinc-200">{settings.limits.max_publishers}</strong> publish</span>
          <span><strong className="text-zinc-200">{settings.limits.max_preparers}</strong> prepare</span>
          <span
            className={`px-2 py-1 rounded-md border ${
              settings.hardware.ocr.device === 'cuda'
                ? 'border-emerald-700/70 bg-emerald-950/40 text-emerald-300'
                : settings.hardware.ocr.nvidia_detected
                  ? 'border-amber-700/70 bg-amber-950/40 text-amber-300'
                  : 'border-zinc-700 bg-zinc-950 text-zinc-300'
            }`}
            title={settings.hardware.ocr.gpu_name || settings.hardware.ocr.fallback_reason || undefined}
          >
            OCR: <strong>{settings.hardware.ocr.label}</strong>
            {settings.hardware.ocr.device === 'cpu' && settings.hardware.ocr.nvidia_detected ? ' (CUDA unavailable)' : ''}
          </span>
          <span><strong className="text-zinc-200">{settings.limits.ocr_threads_per_worker}</strong> OCR threads/worker</span>
          <span><strong className="text-zinc-200">{settings.runtime.active_profile_containers}/{settings.limits.max_active_profile_containers}</strong> containers</span>
          <span>RAM <strong className="text-zinc-200">{settings.runtime.memory_used_percent}%</strong></span>
          <span>CPU <strong className="text-zinc-200">{settings.runtime.sustained_cpu_percent}%</strong></span>
        </div>
      )}
      {error && <div className="w-full text-[10px] text-red-400">{error}</div>}
    </div>
  );
};
