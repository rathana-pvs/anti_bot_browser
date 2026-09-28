import React from 'react';
import { SystemStats } from '../types/profile';
import { Activity, Server, Globe, Cpu, Monitor, HardDrive } from 'lucide-react';

interface TelemetryFooterProps {
  stats: SystemStats;
}

const percent = (value: number | null | undefined) => Math.min(100, Math.max(0, value || 0));
const gb = (value: number | undefined) => ((value || 0) / 1024).toFixed(1);

export const TelemetryFooter: React.FC<TelemetryFooterProps> = ({ stats }) => {
  const runtime = stats.runtime || stats.wsl_runtime || {
    cpu_percent: stats.cpu_percent || 0,
    cpu_threads: stats.cpu_cores || 1,
    cpu_model: stats.cpu_model || 'WSL CPU',
    used_memory_mb: stats.used_memory_mb,
    total_memory_mb: stats.total_memory_mb,
  };
  const host = stats.windows_host;
  const hasWindowsHost = Boolean(host?.available && host.total_memory_mb);
  const displayHost = hasWindowsHost ? host : runtime;
  const hostPlatform = hasWindowsHost ? 'Windows' : 'Linux';

  const hostCpu = percent(displayHost?.cpu_percent);
  const hostGpu = percent(displayHost?.gpu_percent);
  const hostRam = Math.round(((displayHost?.used_memory_mb || 0) / (displayHost?.total_memory_mb || 1)) * 100);
  const runtimeCpu = percent(runtime.cpu_percent);
  const runtimeRam = Math.round((runtime.used_memory_mb / runtime.total_memory_mb) * 100);

  const color = (value: number) => value >= 85 ? 'bg-red-500' : value >= 60 ? 'bg-yellow-400' : 'bg-emerald-500';
  const textColor = (value: number) => value >= 85
    ? 'text-red-400 font-semibold'
    : value >= 60 ? 'text-yellow-400 font-medium' : 'text-zinc-300 font-medium';

  const meter = (value: number) => (
    <div className="w-12 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
      <div className={`h-full transition-all duration-300 ${color(value)}`} style={{ width: `${value}%` }} />
    </div>
  );

  return (
    <footer className="h-8 bg-zinc-950 border-t border-border px-4 flex items-center gap-3 text-[11px] text-zinc-400 select-none shrink-0 font-mono overflow-x-auto whitespace-nowrap">
      <div className="flex items-center gap-3 shrink-0">
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
          <span className="text-zinc-100 font-semibold">{stats.active_profiles} / {stats.total_profiles}</span>
        </div>
      </div>

      <span className="text-zinc-700">|</span>
      <span className="text-sky-400 font-semibold">HOST</span>
      <>
          <div className="flex items-center gap-1.5" title={`${hostPlatform} CPU: ${displayHost?.cpu_model || 'Host CPU'} (${displayHost?.cpu_threads || 1} logical threads)`}>
            <Cpu className="w-3 h-3 text-zinc-500" /><span>CPU</span>{meter(hostCpu)}
            <span className={textColor(hostCpu)}>{hostCpu.toFixed(1)}% <span className="text-zinc-500">({displayHost?.cpu_threads}T)</span></span>
          </div>
          <div className="flex items-center gap-1.5" title={`${hostPlatform} GPU: ${displayHost?.gpu_model || 'Host graphics'}`}>
            <Monitor className="w-3 h-3 text-zinc-500" /><span>GPU</span>{meter(hostGpu)}
            <span className={textColor(hostGpu)}>{hostGpu.toFixed(1)}%</span>
          </div>
          <div className="flex items-center gap-1.5" title={`${hostPlatform} RAM: ${gb(displayHost?.used_memory_mb)} GB used of ${gb(displayHost?.total_memory_mb)} GB`}>
            <HardDrive className="w-3 h-3 text-zinc-500" /><span>RAM</span>{meter(hostRam)}
            <span className={textColor(hostRam)}>{gb(displayHost?.used_memory_mb)}/{gb(displayHost?.total_memory_mb)} GB ({hostRam}%)</span>
          </div>
      </>

      {stats.runtime_environment === 'wsl' && hasWindowsHost && <>
        <span className="text-zinc-700">|</span>
        <span className="text-violet-400 font-semibold">WSL</span>
        <div className="flex items-center gap-1.5" title={`WSL/Docker runtime CPU (${runtime.cpu_threads} logical threads)`}>
          <Cpu className="w-3 h-3 text-zinc-500" /><span>CPU</span>
          <span className={textColor(runtimeCpu)}>{runtimeCpu.toFixed(1)}% ({runtime.cpu_threads}T)</span>
        </div>
        <div className="flex items-center gap-1.5" title="Memory visible to the WSL/Docker runtime">
          <HardDrive className="w-3 h-3 text-zinc-500" /><span>RAM</span>
          <span className={textColor(runtimeRam)}>{gb(runtime.used_memory_mb)}/{gb(runtime.total_memory_mb)} GB ({runtimeRam}%)</span>
        </div>
      </>}

      <span className="text-zinc-700">|</span>
      <div className="flex items-center gap-1.5 shrink-0">
        <Globe className="w-3 h-3 text-zinc-500" />
        <span className="hidden sm:inline">Proxies:</span>
        <span className="text-zinc-200 font-medium">{stats.available_proxies}</span>
      </div>
    </footer>
  );
};
