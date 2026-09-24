import React from 'react';
import { SystemStats } from '../types/profile';
import { Activity, Server, Globe, Cpu, Monitor, HardDrive } from 'lucide-react';

interface TelemetryFooterProps {
  stats: SystemStats;
}

export const TelemetryFooter: React.FC<TelemetryFooterProps> = ({ stats }) => {
  const ramPercent = stats.total_memory_mb
    ? Math.round((stats.used_memory_mb / stats.total_memory_mb) * 100)
    : 0;

  const usedGb = (stats.used_memory_mb / 1024).toFixed(1);
  const totalGb = (stats.total_memory_mb / 1024).toFixed(1);

  const cpuPercent = stats.cpu_percent !== undefined ? stats.cpu_percent : 0;
  const cpuCores = stats.cpu_cores || 1;

  const gpuPercent = stats.gpu_percent !== undefined ? stats.gpu_percent : 0;
  const gpuShortModel = stats.gpu_model
    ? stats.gpu_model.includes('Iris Xe')
      ? 'Iris Xe'
      : stats.gpu_model.replace(/Intel|Graphics|NVIDIA|GeForce/gi, '').trim().slice(0, 10)
    : 'GPU';

  // Unified color states: Normal (green) -> Yellow (60-84%) -> Red (>=85%)
  const getResourceColor = (percent: number) => {
    if (percent >= 85) return 'bg-red-500';
    if (percent >= 60) return 'bg-yellow-400';
    return 'bg-emerald-500';
  };

  const getResourceTextColor = (percent: number) => {
    if (percent >= 85) return 'text-red-400 font-semibold';
    if (percent >= 60) return 'text-yellow-400 font-medium';
    return 'text-zinc-300 font-medium';
  };

  return (
    <footer className="h-8 bg-zinc-950 border-t border-border px-4 flex items-center justify-between text-[11px] text-zinc-400 select-none shrink-0 font-mono">
      {/* Left System Info */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <Server className="w-3 h-3 text-zinc-500" />
          <span className="hidden sm:inline">Docker:</span>
          <span className={`font-semibold ${stats.docker_running ? 'text-green-400' : 'text-red-400'}`}>
            {stats.docker_running ? 'Connected' : 'Offline'}
          </span>
        </div>

        <span className="text-zinc-700">|</span>

        <div className="flex items-center gap-1.5">
          <Activity className="w-3 h-3 text-zinc-500" />
          <span className="hidden sm:inline">Profiles:</span>
          <span className="text-zinc-100 font-semibold">
            {stats.active_profiles} / {stats.total_profiles}
          </span>
        </div>
      </div>

      {/* Right Telemetry */}
      <div className="flex items-center gap-3.5">
        {/* Host CPU */}
        <div
          className="flex items-center gap-1.5"
          title={`CPU: ${stats.cpu_model || 'Host CPU'} (${cpuCores} Cores)`}
        >
          <Cpu className="w-3 h-3 text-zinc-500" />
          <span>CPU:</span>
          <div className="w-16 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${getResourceColor(cpuPercent)}`}
              style={{ width: `${cpuPercent}%` }}
            />
          </div>
          <span className={getResourceTextColor(cpuPercent)}>
            {cpuPercent}% <span className="text-zinc-500 hidden xl:inline">({cpuCores}C)</span>
          </span>
        </div>

        <span className="text-zinc-700">|</span>

        {/* Host GPU */}
        <div
          className="flex items-center gap-1.5"
          title={`GPU: ${stats.gpu_model || 'Host Graphics'}`}
        >
          <Monitor className="w-3 h-3 text-zinc-500" />
          <span>GPU:</span>
          <div className="w-16 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${getResourceColor(gpuPercent)}`}
              style={{ width: `${gpuPercent}%` }}
            />
          </div>
          <span className={getResourceTextColor(gpuPercent)}>
            {gpuPercent}% <span className="text-zinc-500 hidden xl:inline">({gpuShortModel})</span>
          </span>
        </div>

        <span className="text-zinc-700">|</span>

        {/* Host RAM */}
        <div
          className="flex items-center gap-1.5"
          title={`RAM: ${usedGb} GB used of ${totalGb} GB total`}
        >
          <HardDrive className="w-3 h-3 text-zinc-500" />
          <span>RAM:</span>
          <div className="w-16 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${getResourceColor(ramPercent)}`}
              style={{ width: `${ramPercent}%` }}
            />
          </div>
          <span className={getResourceTextColor(ramPercent)}>
            {usedGb}/{totalGb} GB <span className="text-zinc-400">({ramPercent}%)</span>
          </span>
        </div>

        <span className="text-zinc-700">|</span>

        {/* Proxies */}
        <div className="flex items-center gap-1.5">
          <Globe className="w-3 h-3 text-zinc-500" />
          <span className="hidden sm:inline">Proxies:</span>
          <span className="text-zinc-200 font-medium">
            {stats.available_proxies}
          </span>
        </div>
      </div>
    </footer>
  );
};
