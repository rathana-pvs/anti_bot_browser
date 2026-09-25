import React from 'react';
import { Profile } from '../types/profile';
import { Shield, Trash2, HardDrive, Network, PanelRightClose, Activity } from 'lucide-react';

interface ProfileDetailsProps {
  profile: Profile | null;
  onDelete: (profileId: string) => void;
  onEdit?: () => void;
  isOpen?: boolean;
  onToggle?: () => void;
}

export const ProfileDetails: React.FC<ProfileDetailsProps> = ({
  profile,
  onDelete,
  onEdit,
  isOpen = true,
  onToggle,
}) => {
  if (!profile) return null;

  return (
    <aside
      className={`h-full bg-surface border-l border-border shrink-0 select-none overflow-hidden transition-all duration-200 ease-in-out ${
        isOpen ? 'w-72 opacity-100' : 'w-0 border-l-0 opacity-0 pointer-events-none'
      }`}
    >
      <div className="w-72 h-full p-4 flex flex-col justify-between overflow-y-auto">
        <div className="space-y-5">
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
                  <span className="text-zinc-500">Container CPU (4 Cores)</span>
                  <span className="text-emerald-400 font-mono text-[11px] font-medium">
                    {profile.cpu_usage || '< 0.5%'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Container RAM</span>
                  <span className="text-blue-400 font-mono text-[11px] font-medium">
                    {profile.ram_usage || 'Active'}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-zinc-500">Container Disk</span>
                  <span className="text-amber-400 font-mono text-[11px] font-medium">
                    {profile.disk_usage || 'Active'}
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
            <div className="flex justify-between">
              <span className="text-zinc-500">Proxy Host</span>
              <span className="text-zinc-300 font-mono text-[11px]">
                {profile.network.proxy_host || 'None (Direct)'}
              </span>
            </div>
            {profile.network.proxy_port > 0 && (
              <div className="flex justify-between">
                <span className="text-zinc-500">Proxy Port</span>
                <span className="text-zinc-300 font-mono text-[11px]">
                  {profile.network.proxy_port}
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

        {/* Fingerprint Identity */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5" />
            Fingerprint
          </h3>
          <div className="p-2.5 rounded-lg bg-zinc-950 border border-zinc-800 space-y-2 text-xs">
            <div>
              <span className="text-zinc-500 block text-[11px]">GPU Renderer</span>
              <span className="text-zinc-300 font-mono text-[11px] leading-tight block truncate">
                {profile.fingerprint.webgl_renderer}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">Resolution</span>
              <span className="text-zinc-300 font-mono text-[11px]">
                {profile.fingerprint.screen_resolution}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">Timezone</span>
              <span className="text-zinc-300 font-mono text-[11px]">
                {profile.fingerprint.timezone}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">Cores / RAM</span>
              <span className="text-zinc-300 font-mono text-[11px]">
                {profile.fingerprint.hardware_concurrency} Cores · {profile.fingerprint.device_memory} GB
              </span>
            </div>
          </div>
        </div>

        {/* Persistent Storage */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-zinc-500 flex items-center gap-1">
              <HardDrive className="w-3 h-3" />
              Storage Mount
            </span>
            {profile.disk_usage && (
              <span className="text-[11px] text-zinc-400 font-mono">
                {profile.disk_usage}
              </span>
            )}
          </div>
          <p className="text-[11px] text-zinc-400 font-mono bg-zinc-950 p-2 rounded border border-zinc-800 truncate">
            {profile.container.volume_path}
          </p>
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
