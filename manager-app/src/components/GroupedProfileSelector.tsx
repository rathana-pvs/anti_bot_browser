import { useId, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, Loader2 } from 'lucide-react';
import { Profile } from '../types/profile';
import { groupProfiles } from '../utils/profileGroups';
import { Checkbox } from './ui/Checkbox';

export function GroupedProfileSelector({ profiles, selectedIds, onChange, busyProfileIds = [], disabledProfileIds = [], maxHeightClass = 'max-h-[45vh]' }: {
  profiles: Profile[]; selectedIds: string[]; onChange: (ids: string[]) => void;
  disabledProfileIds?: string[]; busyProfileIds?: string[]; maxHeightClass?: string;
}) {
  const selectorId = useId();
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const eligible = profiles.filter(p => !disabledProfileIds.includes(p.id));
  const groups = groupProfiles(profiles);
  const toggle = (ids: string[]) => {
    ids = ids.filter(id => !disabledProfileIds.includes(id));
    if (!ids.length) return;
    const allSelected = ids.every((id) => selectedIds.includes(id));
    onChange(allSelected ? selectedIds.filter((id) => !ids.includes(id)) : [...new Set([...selectedIds, ...ids])]);
  };
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-3 text-xs">
      <button type="button" className="text-blue-400" onClick={() => onChange([...new Set([...selectedIds, ...eligible.map((p) => p.id)])])}>Select all</button>
      <button type="button" className="text-zinc-400" onClick={() => onChange(eligible.filter((p) => p.status === 'running').map((p) => p.id))}>Running only</button>
      <button type="button" className="text-zinc-400" onClick={() => onChange([])}>Clear all</button>
      <span className="ml-auto text-zinc-500">{profiles.filter((p) => selectedIds.includes(p.id)).length} selected</span>
    </div>
    <div className={`${maxHeightClass} space-y-1 overflow-y-auto pr-1`}>
      {groups.map((group) => {
        const ids = group.members.filter(p => !disabledProfileIds.includes(p.id)).map((p) => p.id);
        const count = ids.filter((id) => selectedIds.includes(id)).length;
        const collapsed = !expandedGroups.has(group.name);
        const groupId = `${selectorId}-group-${encodeURIComponent(group.name)}`;
        return <section key={group.name} className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/30">
          <div className="flex items-center gap-2 bg-zinc-900/70 px-3 py-2">
            <Checkbox disabled={!ids.length} checked={ids.length > 0 && count === ids.length ? true : count > 0 ? 'indeterminate' : false} onCheckedChange={() => toggle(ids)} ariaLabel={`Select all profiles in ${group.label}`} />
            <button type="button" aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${group.label}`} aria-expanded={!collapsed} aria-controls={groupId}
              onClick={() => setExpandedGroups((previous) => {
                const next = new Set(previous);
                if (next.has(group.name)) next.delete(group.name); else next.add(group.name);
                return next;
              })}
              className="flex min-w-0 flex-1 items-center gap-2 rounded-md py-1 text-left outline-none hover:text-white focus-visible:ring-2 focus-visible:ring-blue-500">
              {collapsed ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-zinc-500" />}
              <Folder className="h-4 w-4 shrink-0 text-blue-400" />
              <span className="min-w-0 flex-1 truncate text-xs font-semibold text-zinc-200">{group.label}</span>
              <span className="shrink-0 text-[10px] text-zinc-500">{count}/{ids.length} selected</span>
            </button>
          </div>
          <div id={groupId} hidden={collapsed} className="border-t border-zinc-800">
            {group.members.map((p) => <label htmlFor={`${selectorId}-profile-${p.id}`} key={p.id}
              className={`flex cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors ${selectedIds.includes(p.id) ? 'bg-blue-500/10' : 'hover:bg-zinc-800/40'}`}>
              <Checkbox id={`${selectorId}-profile-${p.id}`} disabled={disabledProfileIds.includes(p.id)} checked={selectedIds.includes(p.id)} onCheckedChange={() => toggle([p.id])} ariaLabel={`Select ${p.name}`} />
              <div className="min-w-0 flex-1"><div className="truncate text-xs font-medium text-zinc-200">{p.name}</div>
                <div className="truncate text-[10px] text-zinc-500">{p.id} · {disabledProfileIds.includes(p.id) ? 'Live setup required in Edit profile' : p.network?.proxy_host || 'Direct network'}</div></div>
              {busyProfileIds.includes(p.id) && <span className="flex shrink-0 items-center gap-1 text-[10px] text-amber-400"><Loader2 className="h-3 w-3 animate-spin" />Busy</span>}
              <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase ${p.status === 'running' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-zinc-900 text-zinc-500'}`}>{p.status}</span>
            </label>)}
          </div>
        </section>;
      })}
      {!groups.length && <p className="py-8 text-center text-xs text-zinc-500">Create a profile to choose batch targets.</p>}
    </div>
    <p className="text-[11px] text-zinc-500">Group checkboxes select the entire group. Uncheck individual profiles to customize your batch.</p>
  </div>;
}
