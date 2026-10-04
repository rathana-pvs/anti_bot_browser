import React from 'react';
import { Profile } from '../types/profile';
import { Play, Pause, Square, Globe } from 'lucide-react';

interface ProfileCardProps {
  profile: Profile;
  isSelected: boolean;
  onSelect: (profile: Profile) => void;
  onAction: (profileId: string, action: 'start' | 'stop' | 'pause' | 'unpause', e: React.MouseEvent) => void;
}

export const ProfileCard: React.FC<ProfileCardProps> = ({
  profile,
  isSelected,
  onSelect,
  onAction,
}) => {
  const getStatusDot = () => {
    switch (profile.status) {
      case 'running':
        return 'bg-emerald-500';
      case 'paused':
        return 'bg-amber-500';
      case 'error':
        return 'bg-red-500';
      case 'stopped':
      default:
        return 'bg-zinc-500';
    }
  };

  const getStatusText = () => {
    switch (profile.status) {
      case 'running':
        return 'Running';
      case 'paused':
        return 'Paused';
      case 'error':
        return 'Error';
      case 'stopped':
      default:
        return 'Stopped';
    }
  };

  const proxyLabel = profile.network.proxy_host || 'Direct';
  const fullProxy = profile.network.proxy_host
    ? `${profile.network.proxy_host}:${profile.network.proxy_port}`
    : 'Direct Network';

  return (
    <div
      onClick={() => onSelect(profile)}
      className={`group relative min-h-12 py-2 px-2.5 rounded-md border transition-all cursor-pointer flex items-center justify-between gap-2 ${
        isSelected
          ? 'bg-zinc-800/90 border-zinc-500 shadow-sm'
          : 'bg-zinc-900/90 border-zinc-800/80 hover:bg-zinc-800/50 hover:border-zinc-700'
      }`}
      title={`${profile.name} (${getStatusText()}) - ${fullProxy}`}
    >
      {/* Left: Status Dot & Profile Name */}
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${getStatusDot()} ${
            profile.status === 'running' ? 'ring-2 ring-emerald-500/30 animate-pulse' : ''
          }`}
          title={`Status: ${getStatusText()}`}
        />
        <div className="min-w-0"><div className="truncate text-xs font-medium text-zinc-100">{profile.name}</div><div className="text-[10px] text-zinc-500">{profile.id} · {getStatusText()}</div></div>
      </div>

      {/* Right: Proxy & Action Buttons */}
      <div className="flex items-center gap-1.5 shrink-0">
        <span
          className="text-[11px] text-zinc-400 font-mono truncate max-w-[105px] flex items-center gap-1"
          title={`Proxy: ${fullProxy}`}
        >
          <Globe className="w-3 h-3 text-zinc-500 shrink-0" />
          <span className="truncate">{proxyLabel}</span>
        </span>

        {/* Action Buttons */}
        <div className="flex items-center gap-0.5 ml-0.5" onClick={(e) => e.stopPropagation()}>
          {profile.status === 'stopped' && (
            <button
              onClick={(e) => onAction(profile.id, 'start', e)}
              className="p-1 rounded text-zinc-400 hover:text-emerald-400 hover:bg-zinc-700/60 transition-colors"
              title="Launch profile"
            >
              <Play className="w-3.5 h-3.5" />
            </button>
          )}

          {profile.status === 'running' && (
            <>
              <button
                onClick={(e) => onAction(profile.id, 'pause', e)}
                className="p-1 rounded text-zinc-400 hover:text-amber-400 hover:bg-zinc-700/60 transition-colors"
                title="Pause profile (0% CPU)"
              >
                <Pause className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={(e) => onAction(profile.id, 'stop', e)}
                className="p-1 rounded text-zinc-400 hover:text-red-400 hover:bg-zinc-700/60 transition-colors"
                title="Stop container"
              >
                <Square className="w-3.5 h-3.5" />
              </button>
            </>
          )}

          {profile.status === 'paused' && (
            <>
              <button
                onClick={(e) => onAction(profile.id, 'unpause', e)}
                className="p-1 rounded text-amber-400 hover:text-amber-300 hover:bg-zinc-700/60 transition-colors"
                title="Resume profile"
              >
                <Play className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={(e) => onAction(profile.id, 'stop', e)}
                className="p-1 rounded text-zinc-400 hover:text-red-400 hover:bg-zinc-700/60 transition-colors"
                title="Stop container"
              >
                <Square className="w-3.5 h-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
