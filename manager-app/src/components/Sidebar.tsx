import { CreateGroupModal } from './CreateGroupModal';
import { groupProfiles } from '../utils/profileGroups';
import React, { useState } from 'react';
import { Profile } from '../types/profile';
import { ProfileCard } from './ProfileCard';
import { Plus, Layers, Globe, PanelLeftClose, Sparkles, Folder, ChevronDown, ChevronRight, FolderPlus, Settings } from 'lucide-react';

interface SidebarProps {
  profiles: Profile[];
  groups: string[];
  onCreateGroup: (name: string) => Promise<void>;
  selectedProfileId: string | null;
  activeTab: 'profiles' | 'proxies' | 'campaigns';
  onTabChange: (tab: 'profiles' | 'proxies' | 'campaigns') => void;
  proxyCount?: number;
  onSelectProfile: (profile: Profile) => void;
  onOpenCreateModal: () => void;
  onOpenSettings: () => void;
  onAction: (profileId: string, action: 'start' | 'stop' | 'pause' | 'unpause', e: React.MouseEvent) => void;
  isOpen?: boolean;
  onToggle?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  profiles, groups, onCreateGroup,
  selectedProfileId,
  activeTab,
  onTabChange,
  proxyCount = 0,
  onSelectProfile,
  onOpenCreateModal,
  onOpenSettings,
  onAction,
  isOpen = true,
  onToggle,
}) => {
  const [showGroupModal, setShowGroupModal] = useState(false);
  const [filter, setFilter] = useState<'all' | 'running' | 'paused' | 'stopped'>('all');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const filteredProfiles = profiles.filter((p) => filter === 'all' || p.status === filter);

  const visibleGroups = groupProfiles(filteredProfiles, filter === 'all' ? groups : []);

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
          <button
            type="button"
            onClick={onOpenSettings}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs font-medium text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200 transition-colors"
          >
            <Settings className="h-4 w-4" />
            <span>Settings</span>
          </button>
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

      {/* Main Mode Navigation (Profiles, Campaigns, Proxy Pool) */}
      <div className="p-2 border-b border-border/80 bg-zinc-950/60 grid grid-cols-3 gap-1 text-[10px]">
        <button
          onClick={() => onTabChange('profiles')}
          className={`flex items-center justify-center gap-1 py-1.5 rounded-md font-medium transition-colors ${
            activeTab === 'profiles'
              ? 'bg-zinc-800 text-zinc-100 border border-zinc-700 shadow-sm'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
          title="Screen"
        >
          <Layers className="w-3 h-3" />
          <span>Screen</span>
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
      </div>

      {/* Filter Pills */}
      <div className="p-3 border-b border-border/60">
        <div className="flex items-center gap-1">
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

      {/* Profiles Subheader: Count */}
      <div className="px-3 py-1.5 border-b border-border/40 flex items-center justify-between text-[11px] text-zinc-400 bg-zinc-950/40">
        <span className="font-medium text-zinc-400">
          {filteredProfiles.length} profiles · {groups.length} groups
        </span>
        <button type="button" onClick={() => setShowGroupModal(true)} className="flex items-center gap-1 py-1 text-blue-400 hover:text-blue-300">
          <FolderPlus className="h-3.5 w-3.5" />New group
        </button>
      </div>

      {/* Profiles Scroll Area */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {visibleGroups.length === 0 ? (
          <div className="text-center py-10 px-4">
            <p className="text-xs text-zinc-500">No profiles found</p>
          </div>
        ) : (
          visibleGroups.map((group) => {
            const collapsed = !expandedGroups.has(group.name);
            return <section key={group.name} className="overflow-hidden rounded-xl border border-zinc-800/70 bg-zinc-950/30">
              <button type="button" aria-expanded={!collapsed} onClick={() => setExpandedGroups((prev) => {
                const next = new Set(prev);
                if (next.has(group.name)) next.delete(group.name); else next.add(group.name);
                return next;
              })} className="flex w-full items-center gap-2 bg-zinc-900/60 px-3 py-3 text-xs text-zinc-300 hover:bg-zinc-800/50">
                {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                <Folder className="h-3.5 w-3.5 text-blue-400" />
                <span className="min-w-0 flex-1 truncate text-left font-semibold">{group.label}</span>
                <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{group.members.length}</span>
              </button>
              {!collapsed && group.members.length === 0 && <p className="px-3 py-4 text-[11px] text-zinc-500">No profiles yet. Choose this group when creating or editing a profile.</p>}
              {!collapsed && <div className="space-y-1 p-1.5">{group.members.map((profile) => (
                <ProfileCard key={profile.id} profile={profile}
                  isSelected={profile.id === selectedProfileId && activeTab === 'profiles'}
                  onSelect={(p) => { onTabChange('profiles'); onSelectProfile(p); }} onAction={onAction} />
              ))}</div>}
            </section>;
          })
        )}
      </div>

      </div>
      {showGroupModal && <CreateGroupModal onCreate={onCreateGroup} onClose={() => setShowGroupModal(false)} />}
    </aside>
  );
};
