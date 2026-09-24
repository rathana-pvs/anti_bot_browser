import React, { useState } from 'react';
import { Profile } from '../types/profile';
import { ProfileCard } from './ProfileCard';
import { Plus, Search, Layers, Globe, PanelLeftClose, Sparkles } from 'lucide-react';

interface SidebarProps {
  profiles: Profile[];
  selectedProfileId: string | null;
  activeTab: 'profiles' | 'proxies' | 'campaigns';
  onTabChange: (tab: 'profiles' | 'proxies' | 'campaigns') => void;
  proxyCount?: number;
  onSelectProfile: (profile: Profile) => void;
  onOpenCreateModal: () => void;
  onAction: (profileId: string, action: 'start' | 'stop' | 'pause' | 'unpause', e: React.MouseEvent) => void;
  isOpen?: boolean;
  onToggle?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  profiles,
  selectedProfileId,
  activeTab,
  onTabChange,
  proxyCount = 0,
  onSelectProfile,
  onOpenCreateModal,
  onAction,
  isOpen = true,
  onToggle,
}) => {
  const [filter, setFilter] = useState<'all' | 'running' | 'paused' | 'stopped'>('all');
  const [search, setSearch] = useState('');

  const filteredProfiles = profiles.filter((p) => {
    const matchesFilter = filter === 'all' || p.status === filter;
    const matchesSearch =
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.id.toLowerCase().includes(search.toLowerCase()) ||
      p.network.proxy_host.includes(search);
    return matchesFilter && matchesSearch;
  });

  const runningCount = profiles.filter((p) => p.status === 'running').length;
  const pausedCount = profiles.filter((p) => p.status === 'paused').length;
  const stoppedCount = profiles.filter((p) => p.status === 'stopped').length;

  return (
    <aside
      className={`h-full flex flex-col bg-surface border-r border-border shrink-0 select-none transition-all duration-200 ease-in-out overflow-hidden ${
        isOpen ? 'w-80 opacity-100' : 'w-0 border-r-0 opacity-0 pointer-events-none'
      }`}
    >
      <div className="w-80 h-full flex flex-col">
        {/* Top Header */}
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-zinc-400" />
            <h1 className="font-semibold text-sm tracking-tight text-zinc-100">Isolated Browser</h1>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={onOpenCreateModal}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-white text-zinc-950 font-semibold text-xs hover:bg-zinc-200 transition-colors shadow-sm"
            >
              <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
              <span>New Profile</span>
            </button>
            {onToggle && (
              <button
                onClick={onToggle}
                className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
                title="Collapse Sidebar"
              >
                <PanelLeftClose className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

      {/* Main Mode Navigation (Profiles vs Proxy Pool vs Campaigns) */}
      <div className="p-2 border-b border-border/80 bg-zinc-950/60 grid grid-cols-3 gap-1 text-[11px]">
        <button
          onClick={() => onTabChange('profiles')}
          className={`flex items-center justify-center gap-1 py-1.5 rounded-md font-medium transition-colors ${
            activeTab === 'profiles'
              ? 'bg-zinc-800 text-zinc-100 border border-zinc-700 shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
          title="Profiles"
        >
          <Layers className="w-3 h-3" />
          <span>Profiles</span>
        </button>
        <button
          onClick={() => onTabChange('proxies')}
          className={`flex items-center justify-center gap-1 py-1.5 rounded-md font-medium transition-colors ${
            activeTab === 'proxies'
              ? 'bg-zinc-800 text-zinc-100 border border-zinc-700 shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
          title="Proxy Pool"
        >
          <Globe className="w-3 h-3" />
          <span>Proxies ({proxyCount})</span>
        </button>
        <button
          onClick={() => onTabChange('campaigns')}
          className={`flex items-center justify-center gap-1 py-1.5 rounded-md font-medium transition-colors ${
            activeTab === 'campaigns'
              ? 'bg-zinc-800 text-emerald-400 border border-emerald-500/30 shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
          title="Campaigns"
        >
          <Sparkles className="w-3 h-3 text-emerald-400" />
          <span>Campaign</span>
        </button>
      </div>

      {/* Search Input */}
      <div className="p-3 border-b border-border/60">
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search profiles or proxy..."
            className="w-full bg-zinc-950 border border-zinc-800 rounded-md pl-8 pr-3 py-1.5 text-xs text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-zinc-700"
          />
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1 mt-2.5">
          <button
            onClick={() => setFilter('all')}
            className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              filter === 'all'
                ? 'bg-zinc-800 text-zinc-100 border border-zinc-700'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            All ({profiles.length})
          </button>
          <button
            onClick={() => setFilter('running')}
            className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              filter === 'running'
                ? 'bg-zinc-800 text-green-400 border border-zinc-700'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Running ({runningCount})
          </button>
          <button
            onClick={() => setFilter('paused')}
            className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              filter === 'paused'
                ? 'bg-zinc-800 text-amber-400 border border-zinc-700'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Paused ({pausedCount})
          </button>
          <button
            onClick={() => setFilter('stopped')}
            className={`px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              filter === 'stopped'
                ? 'bg-zinc-800 text-zinc-400 border border-zinc-700'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Stopped ({stoppedCount})
          </button>
        </div>
      </div>

      {/* Profiles Scroll Area */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {filteredProfiles.length === 0 ? (
          <div className="text-center py-10 px-4">
            <p className="text-xs text-zinc-500">No profiles found</p>
          </div>
        ) : (
          filteredProfiles.map((profile) => (
            <ProfileCard
              key={profile.id}
              profile={profile}
              isSelected={profile.id === selectedProfileId && activeTab === 'profiles'}
              onSelect={(p) => {
                onTabChange('profiles');
                onSelectProfile(p);
              }}
              onAction={onAction}
            />
          ))
        )}
      </div>
      </div>
    </aside>
  );
};
