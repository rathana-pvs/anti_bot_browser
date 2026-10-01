import React, { useState, useEffect } from 'react';
import { Profile } from '../types/profile';
import { Shield, Trash2, HardDrive, Network, PanelRightClose, Activity, Loader2, Sparkles, AlertTriangle } from 'lucide-react';
import { cleanProfileEvidence } from '../services/api';

interface ProfileDetailsProps {
  profile: Profile | null;
  onDelete: (profileId: string) => void;
  onEdit?: () => void;
  isOpen?: boolean;
  onToggle?: () => void;
  onRefresh?: () => void;
}

export const ProfileDetails: React.FC<ProfileDetailsProps> = ({
  profile,
  onDelete,
  onEdit,
  isOpen = true,
  onToggle,
  onRefresh,
}) => {
  const [isCleaning, setIsCleaning] = useState(false);
  const [cleanupMessage, setCleanupMessage] = useState<string | null>(null);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [localDiskUsage, setLocalDiskUsage] = useState<string | null>(null);

  useEffect(() => {
    setCleanupMessage(null);
    setCleanupError(null);
    setLocalDiskUsage(null);
  }, [profile?.id]);

  const handleCleanEvidence = async () => {
    if (!profile) return;
    setIsCleaning(true);
    setCleanupMessage(null);
    setCleanupError(null);
    try {
      const res = await cleanProfileEvidence(profile.id, 3);
      if (res.new_disk_usage) {
        setLocalDiskUsage(res.new_disk_usage);
      }
      if (res.deleted_runs > 0) {
        setCleanupMessage(`Freed ${res.freed_formatted} (${res.deleted_runs} deleted, ${res.kept_runs} kept)`);
      } else {
        setCleanupMessage(`All ${res.kept_runs} runs are within last 3 days`);
      }
      if (onRefresh) {
        onRefresh();
      }
    } catch (err: any) {
      setCleanupError(err.message || 'Failed to clean evidence');
    } finally {
      setIsCleaning(false);
    }
  };

  if (!profile) return null;

  const requested = profile.requested_environment || {
    screen_resolution: profile.fingerprint.screen_resolution,
    timezone: profile.fingerprint.timezone,
    language: profile.fingerprint.language,
    timezone_policy: profile.network.proxy_host ? 'proxy' as const : 'host' as const,
    user_agent_policy: 'browser_default' as const,
    user_agent: null,
    rendering_mode: 'host_gpu' as const,
  };
  const effective = profile.effective_environment;
  const observed = profile.observed_environment;
  const resources = profile.resources || { cpu_limit: 4, memory_mb: 4096 };
  const effectiveResources = profile.effective_resources;

  return (
    <aside
      className={`h-full bg-surface border-l border-border shrink-0 select-none overflow-hidden transition-all duration-200 ease-in-out ${
        isOpen ? 'w-72 opacity-100' : 'w-0 border-l-0 opacity-0 pointer-events-none'
      }`}
    >
      <div className="w-72 h-full p-4 flex flex-col justify-between overflow-y-auto">
        <div className="space-y-5">
          {profile.restart_required && (
            <div className="flex gap-2 rounded-lg border border-amber-800/50 bg-amber-950/20 p-2.5 text-[11px] text-amber-300">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>Requested runtime settings are not active. {profile.status === 'running' ? 'Stop and start' : 'Start'} this profile to apply them.</span>
            </div>
          )}
          {/* Header */}
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                Profile Inspector
              </h2>
              <p className="text-sm font-medium text-zinc-100 mt-1 truncate">{profile.name}</p>
              <p className="text-xs text-zinc-500 font-mono">{profile.id}</p>
            </div>
            <div className="flex items-center gap-1.5">
              {onEdit && (
                <button
                  onClick={onEdit}
                  className="px-2.5 py-1 rounded bg-zinc-800 border border-zinc-700 text-zinc-300 hover:text-white hover:bg-zinc-700 text-xs font-medium transition-colors"
                >
                  Edit
                </button>
              )}
              {onToggle && (
                <button
                  onClick={onToggle}
                  className="p-1 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
                  title="Collapse Inspector"
                >
                  <PanelRightClose className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          {/* Live Container Telemetry (When Running) */}
          {profile.status === 'running' && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5 text-emerald-400" />
                Container Resources
              </h3>
              <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800 space-y-1.5 text-xs">
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Configured Limit</span>
                  <span className="text-zinc-300 font-mono text-[11px] font-medium">
                    {resources.cpu_limit} vCPU · {resources.memory_mb / 1024} GiB
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Effective Limit</span>
                  <span className="text-blue-300 font-mono text-[11px] font-medium">
                    {effectiveResources
                      ? `${effectiveResources.cpu_limit} vCPU · ${effectiveResources.memory_mb / 1024} GiB`
                      : 'Not recorded'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Current CPU</span>
                  <span className="text-emerald-400 font-mono text-[11px] font-medium">
                    {profile.cpu_usage || '0.0%'}
                  </span>
                </div>
                <div className="flex justify-between items-center" title="Real-time Docker container memory usage / cgroup sandbox limit">
                  <span className="text-zinc-500">Current RAM / Limit</span>
                  <span className="text-blue-400 font-mono text-[11px] font-medium">
                    {profile.ram_usage || 'Active'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Container Disk</span>
                  <span className="text-amber-400 font-mono text-[11px] font-medium">
                    {localDiskUsage || profile.disk_usage || 'Active'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Display Ports</span>
                  <span className="text-zinc-400 font-mono text-[11px]">
                    VNC :{profile.container.vnc_port} · WS :{profile.container.ws_port || profile.container.vnc_port + 180}
                  </span>
                </div>
              </div>
            </div>
          )}

        {/* Network & Proxy */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
            <Network className="w-3.5 h-3.5" />
            Network Isolation
          </h3>
          <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800 space-y-1.5 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-zinc-500">Mode</span>
              <span className="text-zinc-300 font-mono text-[11px]">
                {profile.network.mode || (profile.network.proxy_host ? 'custom' : 'direct')}
              </span>
            </div>
            {profile.network.proxy_host && (
              <div className="flex justify-between gap-2">
                <span className="text-zinc-500">Endpoint</span>
                <span className="truncate text-zinc-300 font-mono text-[11px]">
                  {profile.network.proxy_host}:{profile.network.proxy_port}
                </span>
              </div>
            )}
            <div className="flex justify-between items-center">
              <span className="text-zinc-500">Killswitch</span>
              <span className={`text-[11px] font-medium ${profile.network.proxy_host ? 'text-emerald-400' : 'text-zinc-500'}`}>
                {profile.network.proxy_host ? 'Fail-Closed (iptables)' : 'Disabled'}
              </span>
            </div>
          </div>
        </div>

        {/* Requested, effective and observed environment */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5" />
            Browser Environment
          </h3>
          <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2 text-xs">
            <div className="grid grid-cols-[62px_1fr] gap-x-2 gap-y-1 text-[10px]">
              <span className="text-zinc-600">Requested</span>
              <span className="truncate text-zinc-300 font-mono">{requested.screen_resolution} · {requested.timezone}</span>
              <span className="text-zinc-600">Effective</span>
              <span className="truncate text-blue-300 font-mono">
                {effective ? `${effective.screen_resolution} · ${effective.timezone}` : 'Not applied yet'}
              </span>
              <span className="text-zinc-600">Observed</span>
              <span className="truncate text-emerald-300 font-mono">
                {observed ? `${observed.screen_resolution || 'unknown'} · ${observed.timezone || 'unknown'}` : 'Not measured'}
              </span>
            </div>
            <div className="border-t border-zinc-800/70 pt-2">
              <span className="block text-[11px] text-zinc-500">Language / rendering</span>
              <span className="text-[11px] text-zinc-300 font-mono">
                {requested.language} · {requested.rendering_mode === 'host_gpu' ? 'Host GPU' : 'Software'}
              </span>
            </div>
            <div>
              <span className="block text-[11px] text-zinc-500">Observed renderer</span>
              <span className="block truncate text-[11px] text-zinc-300 font-mono" title={observed?.webgl_renderer || undefined}>
                {observed?.webgl_renderer || 'Not measured'}
              </span>
              {observed?.rendering && (
                <span className={`mt-1 block text-[10px] ${observed.rendering.accelerated ? 'text-emerald-400' : 'text-amber-400'}`}>
                  {observed.rendering.accelerated
                    ? `Hardware accelerated · ${observed.rendering.device_backend}`
                    : `Software fallback${observed.rendering.fallback_reason ? ` · ${observed.rendering.fallback_reason}` : ''}`}
                </span>
              )}
            </div>
            {observed?.browser_version && <div className="truncate text-[10px] text-zinc-500">{observed.browser_version}</div>}
            {effective?.network_preflight && (
              <div className={`truncate text-[10px] ${effective.network_preflight.startsWith('passed:') ? 'text-emerald-400' : 'text-red-400'}`}>
                Network: {effective.network_preflight}
              </div>
            )}
            {observed?.observed_at && <div className="text-[10px] text-zinc-600">Observed {new Date(observed.observed_at).toLocaleString()}</div>}
          </div>
        </div>

        {/* Persistent Storage & Evidence Cleanup */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-zinc-500 flex items-center gap-1">
              <HardDrive className="w-3 h-3" />
              Storage Mount
            </span>
            {(localDiskUsage || profile.disk_usage) && (
              <span className="text-[11px] text-amber-400 font-mono font-medium">
                {localDiskUsage || profile.disk_usage}
              </span>
            )}
          </div>
          <p className="text-[11px] text-zinc-400 font-mono bg-zinc-950 p-2 rounded border border-zinc-800 truncate">
            {profile.container.volume_path}
          </p>

          {/* Clean Evidence Action */}
          <div className="pt-0.5">
            <button
              onClick={handleCleanEvidence}
              disabled={isCleaning}
              className="w-full flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded bg-zinc-900 border border-zinc-700/80 hover:border-amber-500/50 hover:bg-zinc-800/80 text-zinc-300 hover:text-amber-300 text-xs font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              title="Delete evidence older than 3 days while keeping recent evidence"
            >
              {isCleaning ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-400" />
                  <span>Cleaning Evidence...</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                  <span>Clean Evidence (&gt;3 days)</span>
                </>
              )}
            </button>

            {cleanupMessage && (
              <div className="mt-1.5 p-2 rounded bg-zinc-950 border border-emerald-900/40 text-[11px] text-emerald-400 flex items-center justify-between">
                <span>{cleanupMessage}</span>
                <button
                  onClick={() => setCleanupMessage(null)}
                  className="text-zinc-500 hover:text-zinc-300 text-xs ml-1"
                >
                  ✕
                </button>
              </div>
            )}

            {cleanupError && (
              <div className="mt-1.5 p-2 rounded bg-zinc-950 border border-red-900/40 text-[11px] text-red-400 flex items-center justify-between">
                <span>{cleanupError}</span>
                <button
                  onClick={() => setCleanupError(null)}
                  className="text-zinc-500 hover:text-zinc-300 text-xs ml-1"
                >
                  ✕
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Delete Action */}
      <div className="pt-4 border-t border-border mt-4">
        <button
          onClick={() => {
            if (confirm(`Are you sure you want to delete profile "${profile.name}"?`)) {
              onDelete(profile.id);
            }
          }}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-1.5 rounded bg-zinc-900 border border-red-900/40 text-red-400 hover:bg-red-950/30 hover:border-red-800 text-xs font-medium transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Delete Profile</span>
        </button>
      </div>
      </div>
    </aside>
  );
};
