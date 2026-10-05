import React, { useState, useEffect, useRef } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Profile } from '../types/profile';
import { Shield, Trash2, HardDrive, Network, PanelRightClose, Activity, Loader2, Sparkles, AlertTriangle, Info, X, RefreshCw } from 'lucide-react';
import { cleanProfileEvidence, checkProfileFacebookResponse, FacebookResponseResult, checkProfileProxySpeed, ProxySpeedResult } from '../services/api';
import { useAppDialog } from './ui/AppDialogProvider';

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
  const { showConfirm } = useAppDialog();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [isCleaning, setIsCleaning] = useState(false);
  const [cleanupMessage, setCleanupMessage] = useState<string | null>(null);
  const [cleanupError, setCleanupError] = useState<string | null>(null);
  const [localDiskUsage, setLocalDiskUsage] = useState<string | null>(null);
  const [facebookResponse, setFacebookResponse] = useState<FacebookResponseResult | null>(null);
  const [isCheckingFacebook, setIsCheckingFacebook] = useState(false);
  const [facebookError, setFacebookError] = useState<string | null>(null);
  const facebookRequest = useRef(0);

  const [download, setDownload] = useState<ProxySpeedResult | null>(null);
  const [upload, setUpload] = useState<ProxySpeedResult | null>(null);
  const [speedPhase, setSpeedPhase] = useState<'download' | 'upload' | null>(null);
  const [speedError, setSpeedError] = useState<string | null>(null);
  const speedRequest = useRef(0);

  useEffect(() => {
    speedRequest.current += 1;
    setDownload(null);
    setUpload(null);
    setSpeedPhase(null);
    setSpeedError(null);
    return () => { speedRequest.current += 1; };
  }, [profile?.id, profile?.network.proxy_host, profile?.network.proxy_port,
    profile?.network.proxy_type, profile?.network.proxy_user, profile?.network.proxy_pass]);

  const handleCheckSpeed = async () => {
    if (!profile || speedPhase) return;
    const request = ++speedRequest.current;
    setSpeedPhase('download');
    setSpeedError(null);
    setDownload(null);
    setUpload(null);
    try {
      const down = await checkProfileProxySpeed(profile.id, 'download');
      if (request !== speedRequest.current) return;
      setDownload(down);
      setSpeedPhase('upload');
      const up = await checkProfileProxySpeed(profile.id, 'upload');
      if (request !== speedRequest.current) return;
      setUpload(up);
    } catch (error) {
      if (request === speedRequest.current) setSpeedError(error instanceof Error ? error.message : 'Speed test failed');
    } finally {
      if (request === speedRequest.current) setSpeedPhase(null);
    }
  };

  useEffect(() => {
    facebookRequest.current += 1;
    setFacebookResponse(null);
    setFacebookError(null);
    setIsCheckingFacebook(false);
    return () => { facebookRequest.current += 1; };
  }, [profile?.id, profile?.network.proxy_host, profile?.network.proxy_port,
    profile?.network.proxy_type, profile?.network.proxy_user, profile?.network.proxy_pass]);

  const handleCheckFacebook = async () => {
    if (!profile || isCheckingFacebook) return;
    const request = ++facebookRequest.current;
    setIsCheckingFacebook(true);
    setFacebookError(null);
    try {
      const result = await checkProfileFacebookResponse(profile.id);
      if (request === facebookRequest.current) setFacebookResponse(result);
    } catch (error) {
      if (request === facebookRequest.current) setFacebookError(error instanceof Error ? error.message : 'Failed to check Facebook response');
    } finally {
      if (request === facebookRequest.current) setIsCheckingFacebook(false);
    }
  };

  useEffect(() => {
    setDetailsOpen(false);
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

  const fullDetails = (
    <>
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
              <div className="flex justify-between gap-2 min-w-0">
                <span className="text-zinc-500">Endpoint</span>
                <span className="break-all text-zinc-300 font-mono text-[11px] text-right">
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
            {profile.network.proxy_host && (
              <div className="border-t border-zinc-800/70 pt-2 space-y-1.5">
                <div className="flex items-center justify-between gap-2 pt-1">
                  <span className="text-zinc-500" title="Time to the first response byte from Facebook through this proxy">Facebook response time</span>
                  <div className="flex items-center gap-2">
                    <span aria-live="polite" className={`font-mono text-[11px] ${facebookResponse && !facebookResponse.success ? 'text-red-400' : 'text-zinc-300'}`}>
                      {isCheckingFacebook ? 'Checking…' : facebookResponse?.response_ms != null ? `${facebookResponse.response_ms} ms` : facebookResponse ? 'Failed' : 'Not measured'}
                    </span>
                    <button type="button" onClick={handleCheckFacebook} disabled={isCheckingFacebook || Boolean(speedPhase)}
                      aria-label="Check Facebook response time" title="Check Facebook response through this proxy"
                      className="rounded border border-zinc-700 p-1 text-zinc-400 hover:bg-zinc-800 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
                      {isCheckingFacebook ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                    </button>
                  </div>
                </div>
                {facebookResponse?.last_checked && <p className="text-[10px] text-zinc-600">Last checked {new Date(facebookResponse.last_checked).toLocaleString()}</p>}
                {(facebookError || facebookResponse?.error) && <p role="alert" className="break-words text-[11px] text-red-400">{facebookError || facebookResponse?.error}</p>}
                <div className="border-t border-zinc-800/70 pt-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-zinc-500">Proxy speed · Cloudflare</span>
                    <button type="button" onClick={handleCheckSpeed} disabled={Boolean(speedPhase) || isCheckingFacebook}
                      title="Estimated transfer speed through this proxy. Uses up to 7 MB of test data."
                      className="flex items-center gap-1 rounded border border-zinc-700 px-2 py-1 text-[11px] text-zinc-400 hover:bg-zinc-800 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
                      {speedPhase && <Loader2 className="h-3 w-3 animate-spin" />}
                      {speedPhase ? `Testing ${speedPhase}…` : 'Test speed'}
                    </button>
                  </div>
                  <div aria-live="polite" className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                    <div><span className="text-zinc-500">↓ Download</span><p className="mt-0.5 font-mono text-zinc-300">{speedPhase === 'download' ? 'Checking…' : download?.mbps != null ? `${download.mbps} Mbps` : download ? 'Failed' : 'Not measured'}</p></div>
                    <div><span className="text-zinc-500">↑ Upload</span><p className="mt-0.5 font-mono text-zinc-300">{speedPhase === 'upload' ? 'Checking…' : upload?.mbps != null ? `${upload.mbps} Mbps` : upload ? 'Failed' : 'Not measured'}</p></div>
                  </div>
                  <p className="mt-1 text-[10px] text-zinc-600">Estimate · up to 7 MB per test</p>
                  {(upload?.last_checked || download?.last_checked) && <p className="text-[10px] text-zinc-600">Last tested {new Date(upload?.last_checked || download!.last_checked).toLocaleString()}</p>}
                  {download?.error && <p role="alert" className="mt-1 break-words text-[11px] text-red-400">Download: {download.error}</p>}
                  {upload?.error && <p role="alert" className="mt-1 break-words text-[11px] text-red-400">Upload: {upload.error}</p>}
                  {speedError && <p role="alert" className="mt-1 break-words text-[11px] text-red-400">{speedError}</p>}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Requested, effective and observed environment */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5" />
            Browser Environment
          </h3>
          <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2 text-xs">
            <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-2 gap-y-1 text-[10px]">
              <span className="text-zinc-600">Requested</span>
              <span className="break-words text-zinc-300 font-mono">{requested.screen_resolution} · {requested.timezone}</span>
              <span className="text-zinc-600">Effective</span>
              <span className="break-words text-blue-300 font-mono">
                {effective ? `${effective.screen_resolution} · ${effective.timezone}` : 'Not applied yet'}
              </span>
              <span className="text-zinc-600">Observed</span>
              <span className="break-words text-emerald-300 font-mono">
                {observed ? `${observed.screen_resolution || 'unknown'} · ${observed.timezone || 'unknown'}` : 'Not measured'}
              </span>
            </div>
            <div className="border-t border-zinc-800/70 pt-2">
              <span className="block text-[11px] text-zinc-500">Language / rendering</span>
              <span className="text-[11px] text-zinc-300 font-mono">
                {requested.language} · {requested.rendering_mode === 'host_gpu' ? 'Host GPU' : 'Software'}
              </span>
              {observed && (
                <span className={`mt-1 block text-[10px] ${observed.language === requested.language ? 'text-emerald-400' : 'text-amber-400'}`}>
                  Browser {observed.language || 'not measured'} · system {observed.system_locale || 'not measured'}
                </span>
              )}
            </div>
            <div>
              <span className="block text-[11px] text-zinc-500">Observed renderer</span>
              <span className="block break-words text-[11px] text-zinc-300 font-mono" title={observed?.webgl_renderer || undefined}>
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
            {observed?.browser_version && <div className="break-words text-[10px] text-zinc-500">{observed.browser_version}</div>}
            {observed?.user_agent && (
              <div className="break-words text-[10px] text-zinc-500" title={observed.user_agent}>
                {observed.platform || 'unknown platform'} · {observed.hardware_concurrency || '?'} CPU · {observed.device_memory || '?'} GiB
              </div>
            )}
            {effective?.network_preflight && (
              <div className={`break-words text-[10px] ${effective.network_preflight.startsWith('passed:') ? 'text-emerald-400' : 'text-red-400'}`}>
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
          <p className="text-[11px] text-zinc-400 font-mono bg-zinc-950 p-2 rounded border border-zinc-800 break-words">
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
    </>
  );

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

          <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-zinc-500">Status</span>
              <span className={`capitalize font-medium ${profile.status === 'running' ? 'text-emerald-400' : 'text-zinc-300'}`}>{profile.status}</span>
            </div>
            {profile.status === 'running' && (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">CPU</span>
                  <span className="font-mono text-zinc-300">{profile.cpu_usage || '0.0%'}</span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-zinc-500">Memory</span>
                  <span className="font-mono text-zinc-300 text-right">{profile.ram_usage || 'Not measured'}</span>
                </div>
              </>
            )}
            <div className="border-t border-zinc-800 pt-3 flex items-center justify-between gap-2">
              <span className="text-zinc-500">Connection</span>
              <span className="text-zinc-300">{profile.network.proxy_host ? 'Proxy' : 'Direct'}</span>
            </div>
            {effective?.network_preflight && (
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Network check</span>
                <span className={effective.network_preflight.startsWith('passed:') ? 'text-emerald-400' : 'text-red-400'}>
                  {effective.network_preflight.startsWith('passed:') ? 'Passed' : 'Needs attention'}
                </span>
              </div>
            )}
            <div className="flex items-center justify-between gap-2">
              <span className="text-zinc-500">Timezone</span>
              <span className="text-zinc-300 text-right break-words min-w-0">{effective?.timezone || requested.timezone}</span>
            </div>
          </div>

          <Dialog.Root open={detailsOpen} onOpenChange={setDetailsOpen}>
            <Dialog.Trigger asChild>
              <button className="w-full flex items-center justify-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-white transition-colors">
                <Info className="h-3.5 w-3.5" />
                View details
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 z-[200] bg-black/70 backdrop-blur-sm" />
              <Dialog.Content className="fixed left-1/2 top-1/2 z-[201] flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-zinc-700 bg-surface shadow-2xl focus:outline-none">
                <div className="flex items-start justify-between gap-3 border-b border-border p-5">
                  <div className="min-w-0">
                    <Dialog.Title className="text-sm font-semibold text-zinc-100">{profile.name} · Details</Dialog.Title>
                    <Dialog.Description className="mt-1 text-xs text-zinc-500">Resources, connection, browser environment, and storage.</Dialog.Description>
                  </div>
                  <Dialog.Close asChild>
                    <button aria-label="Close profile details" className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-white">
                      <X className="h-4 w-4" />
                    </button>
                  </Dialog.Close>
                </div>
                <div className="overflow-y-auto p-5 space-y-5">
                  {fullDetails}
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
      </div>

      {/* Delete Action */}
      <div className="pt-4 border-t border-border mt-4">
        <button
          onClick={async () => {
            if (await showConfirm(`Delete profile "${profile.name}"?`, {
              title: 'Delete profile', confirmLabel: 'Delete profile', variant: 'danger',
            })) {
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
