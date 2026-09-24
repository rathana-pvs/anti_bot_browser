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
        return 'bg-green-500';
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

  return (
    <div
      onClick={() => onSelect(profile)}
      className={`group relative p-3 rounded-lg border transition-all cursor-pointer ${
        isSelected
          ? 'bg-zinc-800/80 border-zinc-600 shadow-sm'
          : 'bg-zinc-900 border-zinc-800 hover:bg-zinc-800/40 hover:border-zinc-700'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full shrink-0 ${getStatusDot()}`} />
          <h3 className="font-medium text-sm text-zinc-100 truncate">{profile.name}</h3>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[11px] font-medium px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700/60 text-zinc-300">
            Wk {profile.account.warming_week}
          </span>
        </div>
      </div>

      <div className="mt-2 text-xs text-zinc-400 flex items-center justify-between">
        <span className="truncate flex items-center gap-1">
          <Globe className="w-3 h-3 text-zinc-500 shrink-0" />
          {profile.network.proxy_host ? `${profile.network.proxy_host}:${profile.network.proxy_port}` : 'Direct Network'}
        </span>
        <span className="text-zinc-500 shrink-0 ml-2 font-mono text-[11px]">
          :{profile.container.vnc_port}
        </span>
      </div>

      <div className="mt-2.5 pt-2 border-t border-zinc-800/80 flex items-center justify-between text-xs text-zinc-500">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="capitalize">{getStatusText()}</span>
          {profile.status === 'running' && (profile.cpu_usage || profile.ram_usage) && (
            <span className="text-[10px] text-zinc-400 font-mono truncate">
              · CPU {profile.cpu_usage || '0%'} | RAM {profile.ram_usage ? profile.ram_usage.split('/')[0].trim() : ''}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
          {profile.status === 'stopped' && (
            <button
              onClick={(e) => onAction(profile.id, 'start', e)}
              className="p-1 rounded hover:bg-zinc-700 text-zinc-300 hover:text-white transition-colors"
              title="Launch profile"
            >
              <Play className="w-3.5 h-3.5" />
            </button>
          )}

          {profile.status === 'running' && (
            <>
              <button
                onClick={(e) => onAction(profile.id, 'pause', e)}
                className="p-1 rounded hover:bg-zinc-700 text-zinc-300 hover:text-white transition-colors"
                title="Pause profile (0% CPU)"
              >
                <Pause className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={(e) => onAction(profile.id, 'stop', e)}
                className="p-1 rounded hover:bg-zinc-700 text-zinc-300 hover:text-white transition-colors"
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
                className="p-1 rounded hover:bg-zinc-700 text-amber-400 hover:text-white transition-colors"
                title="Resume profile"
              >
                <Play className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={(e) => onAction(profile.id, 'stop', e)}
                className="p-1 rounded hover:bg-zinc-700 text-zinc-300 hover:text-white transition-colors"
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
