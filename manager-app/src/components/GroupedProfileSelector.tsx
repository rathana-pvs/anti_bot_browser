import { useState } from 'react';
import { Folder, Search } from 'lucide-react';
import { Profile } from '../types/profile';
import { groupProfiles } from '../utils/profileGroups';
import { Checkbox } from './ui/Checkbox';
import { Select } from './ui/Select';

export function GroupedProfileSelector({ profiles, selectedIds, onChange }: {
  profiles: Profile[]; selectedIds: string[]; onChange: (ids: string[]) => void;
}) {
  const [search, setSearch] = useState('');
  const [groupFilter, setGroupFilter] = useState('all');
  const groups = groupProfiles(profiles);
  const query = search.trim().toLowerCase();
  const visible = groups.filter((group) => groupFilter === 'all' || `group:${group.name}` === groupFilter)
    .map((group) => ({ ...group, visible: group.members.filter((p) =>
      [p.name, p.id, p.network?.proxy_host, group.label].some((value) => value?.toLowerCase().includes(query))) }))
    .filter((group) => group.visible.length);
  const toggle = (ids: string[]) => {
    const allSelected = ids.every((id) => selectedIds.includes(id));
    onChange(allSelected ? selectedIds.filter((id) => !ids.includes(id)) : [...new Set([...selectedIds, ...ids])]);
  };
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      <div className="relative min-w-[160px] flex-1">
        <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-zinc-500" />
        <input aria-label="Search target profiles" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search profiles or groups"
          className="w-full rounded-lg border border-zinc-800 bg-zinc-950 py-2 pl-9 pr-3 text-xs text-white" />
      </div>
      <div className="w-44 max-w-full">
        <Select
          ariaLabel="Filter target group"
          value={groupFilter}
          onValueChange={setGroupFilter}
          options={[
            { value: 'all', label: 'All groups' },
            ...groups.map((group) => ({
              value: `group:${group.name}`,
              label: `${group.label} (${group.members.length})`,
            })),
          ]}
        />
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-3 text-xs">
      <button type="button" className="text-blue-400" onClick={() => onChange([...new Set([...selectedIds, ...visible.flatMap((g) => g.visible.map((p) => p.id))])])}>Select visible</button>
      <button type="button" className="text-zinc-400" onClick={() => onChange(profiles.filter((p) => p.status === 'running').map((p) => p.id))}>Running only</button>
      <button type="button" className="text-zinc-400" onClick={() => onChange([])}>Clear all</button>
      <span className="ml-auto text-zinc-500">{profiles.filter((p) => selectedIds.includes(p.id)).length} selected</span>
    </div>
    <div className="max-h-[45vh] space-y-3 overflow-y-auto">
      {visible.map((group) => {
        const ids = group.members.map((p) => p.id);
        const count = ids.filter((id) => selectedIds.includes(id)).length;
        return <section key={group.name} className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/30">
          <div className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900/70 px-3 py-2.5">
            <Checkbox checked={count === ids.length ? true : count > 0 ? 'indeterminate' : false} onCheckedChange={() => toggle(ids)} ariaLabel={`Select all profiles in ${group.label}`} />
            <Folder className="h-4 w-4 text-blue-400" />
            <span className="min-w-0 flex-1 truncate text-xs font-semibold text-zinc-200">{group.label}</span>
            <span className="text-[11px] text-zinc-500">{count}/{ids.length} selected</span>
          </div>
          {group.visible.map((p) => <div key={p.id} className="flex items-center gap-3 px-3 py-2.5 hover:bg-zinc-800/40">
            <Checkbox checked={selectedIds.includes(p.id)} onCheckedChange={() => toggle([p.id])} ariaLabel={`Select ${p.name}`} />
            <div className="min-w-0 flex-1"><div className="truncate text-xs font-medium text-zinc-200">{p.name}</div>
              <div className="truncate text-[10px] text-zinc-500">{p.id} · {p.network?.proxy_host || 'Direct network'}</div></div>
            <span className={`text-[10px] ${p.status === 'running' ? 'text-emerald-400' : 'text-zinc-500'}`}>{p.status}</span>
          </div>)}
        </section>;
      })}
      {!visible.length && <p className="py-8 text-center text-xs text-zinc-500">{profiles.length ? 'No profiles match these filters.' : 'Create a profile to choose batch targets.'}</p>}
    </div>
    <p className="text-[11px] text-zinc-500">Group checkboxes select the entire group. Uncheck individual profiles to customize your batch.</p>
  </div>;
}
