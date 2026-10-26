import { useEffect, useMemo, useState } from 'react';
import { FolderPlus, Loader2, Plus, Search, Users, X } from 'lucide-react';
import { Profile } from '../../types/profile';
import { QueueExecutionItem } from '../../types/automation';
import { ActionMenu } from '../ui/ActionMenu';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { EmptyState } from '../ui/EmptyState';
import { Input } from '../ui/Input';
import { useContextMenu } from '../ui/ContextMenu';
import { cn } from '../ui/cn';
import { AccountHandlers, FolderHandlers, accountMenu, bulkMenu, folderMenu } from './accountActions';
import { ACCOUNT_DRAG_TYPE, ALL_FOLDERS, ATTENTION, FolderList, NO_FOLDER } from './FolderList';
import {
  AccountActivity, STATUS_LABEL, STATUS_TONE, TONE_CLASS, activityFor, healthFor, needsAttention, warmupLabel,
} from './accountModel';
import { formatTime, kindLabel } from '../queue/queueModel';

type StatusFilter = 'all' | 'running' | 'stopped';

export function AccountsPage({ profiles, groups, executions, handlers, folderHandlers, busyIds, onCreate }: {
  profiles: Profile[]; groups: string[]; executions: QueueExecutionItem[];
  handlers: AccountHandlers; folderHandlers: FolderHandlers;
  /** Accounts with a start/stop request in flight. */
  busyIds: Set<string>;
  onCreate: () => void;
}) {
  const [status, setStatus] = useState<StatusFilter>('all');
  const [folder, setFolder] = useState(ALL_FOLDERS);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const menu = useContextMenu();

  const activity = useMemo(() => new Map(profiles.map(p => [p.id, activityFor(p.id, executions)])), [profiles, executions]);
  const folders = useMemo(() => [...new Set([...groups, ...profiles.map(p => p.group || '').filter(Boolean)])]
    .sort((a, b) => a.localeCompare(b))
    .map(name => ({ name, count: profiles.filter(p => p.group === name).length })), [groups, profiles]);

  // A deleted or renamed folder must not leave the list filtered to nothing.
  useEffect(() => {
    if (![ALL_FOLDERS, ATTENTION, NO_FOLDER].includes(folder) && !folders.some(f => f.name === folder)) setFolder(ALL_FOLDERS);
  }, [folder, folders]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return profiles.filter(p => {
      if (folder === ATTENTION && !needsAttention(p, activity.get(p.id))) return false;
      if (folder === NO_FOLDER && p.group) return false;
      if (![ALL_FOLDERS, ATTENTION, NO_FOLDER].includes(folder) && (p.group || '') !== folder) return false;
      if (status === 'running' && p.status !== 'running') return false;
      if (status === 'stopped' && p.status === 'running') return false;
      return !q || p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q) || (p.group || '').toLowerCase().includes(q);
    }).sort((a, b) => Number(!a.group) - Number(!b.group) || (a.group || '').localeCompare(b.group || '') || a.name.localeCompare(b.name));
  }, [profiles, status, folder, search, activity]);

  const attentionCount = profiles.filter(p => needsAttention(p, activity.get(p.id))).length;
  const runningCount = profiles.filter(p => p.status === 'running').length;
  const selectedProfiles = profiles.filter(p => selected.has(p.id));
  const allVisibleSelected = visible.length > 0 && visible.every(p => selected.has(p.id));

  const toggle = (id: string) => setSelected(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected(allVisibleSelected ? new Set() : new Set(visible.map(p => p.id)));

  const rowMenu = (profile: Profile) => selected.has(profile.id) && selected.size > 1
    ? { items: bulkMenu(selectedProfiles, handlers), title: `${selected.size} accounts` }
    : { items: accountMenu(profile, handlers), title: profile.name };
  const menuForFolder = (name: string) => folderMenu(name, profiles.filter(p => p.group === name), handlers, folderHandlers);
  const blankMenu = [{ label: 'New folder…', icon: <FolderPlus className="h-3.5 w-3.5" />, onSelect: folderHandlers.create }];

  // Dragging a selected row carries the whole selection.
  const startDrag = (profile: Profile) => (event: React.DragEvent) => {
    const ids = selected.has(profile.id) ? [...selected] : [profile.id];
    event.dataTransfer.setData(ACCOUNT_DRAG_TYPE, JSON.stringify(ids));
    event.dataTransfer.effectAllowed = 'move';
  };
  const dropAccounts = (ids: string[], target: string) => {
    const moving = profiles.filter(p => ids.includes(p.id) && (p.group || '') !== target);
    if (moving.length) folderHandlers.moveTo(moving, target);
  };

  if (!profiles.length) {
    return <div className="flex flex-1 items-center justify-center p-6">
      <EmptyState icon={<Users className="h-6 w-6" />} title="Create your first account"
        description="Each account gets its own isolated browser, cookies, and network."
        action={<Button onClick={onCreate}><Plus className="h-3.5 w-3.5" />New account</Button>} />
    </div>;
  }

  const chip = (active: boolean) => cn('rounded-full border px-3 py-1 text-xs transition-colors',
    active ? 'border-blue-600 bg-blue-950/40 text-blue-200' : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200');
  const heading = folder === ALL_FOLDERS ? 'All accounts' : folder === ATTENTION ? 'Needs attention' : folder === NO_FOLDER ? 'No folder' : folder;

  return <div className="flex min-h-0 flex-1">
    <FolderList value={folder} onChange={setFolder} folders={folders} total={profiles.length} attention={attentionCount}
      unfiled={profiles.filter(p => !p.group).length} onCreate={folderHandlers.create} onDropAccounts={dropAccounts}
      onFolderMenu={(event, name) => menu.open(event, menuForFolder(name), name)}
      onBlankMenu={(event) => menu.open(event, blankMenu)}
      menuKey={(name) => menu.keyboardTrigger(() => menuForFolder(name), name)} />

    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border bg-surface/50 px-6 py-3">
        <h1 className="truncate text-sm font-semibold text-zinc-100">{heading}</h1>
        <span className="text-xs text-zinc-500">{profiles.length} accounts · {runningCount} running</span>
        <div className="ml-auto flex items-center gap-2">
          <div className="relative w-56">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name, folder, or ID" aria-label="Search accounts" className="min-h-8 py-1 pl-8" />
          </div>
          <Button size="sm" onClick={onCreate}><Plus className="h-3.5 w-3.5" />New account</Button>
        </div>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-2 px-6 pt-3">
        <button type="button" className={chip(status === 'all')} onClick={() => setStatus('all')}>Any status</button>
        <button type="button" className={chip(status === 'running')} onClick={() => setStatus('running')}>Running</button>
        <button type="button" className={chip(status === 'stopped')} onClick={() => setStatus('stopped')}>Not running</button>
        <span className="ml-auto text-[11px] text-zinc-600">Drag accounts onto a folder to move them</span>
      </div>

      {selected.size > 0 && <div className="mx-6 mt-3 flex shrink-0 flex-wrap items-center gap-2 rounded-xl border border-blue-900 bg-blue-950/30 px-3 py-2 text-xs text-blue-100">
        <span>{selected.size} selected</span>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {bulkMenu(selectedProfiles, handlers).map(item => <Button key={item.label} size="sm" variant="ghost" disabled={item.disabled}
            className={item.danger ? 'text-red-300 hover:text-red-200' : 'text-blue-100'} onClick={item.onSelect}>{item.icon}{item.label}</Button>)}
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} aria-label="Clear selection"><X className="h-3.5 w-3.5" /></Button>
        </div>
      </div>}

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-3">
        <div className="overflow-hidden rounded-xl border border-zinc-800">
          <div className="grid grid-cols-[1.5rem_minmax(0,1.5fr)_6rem_minmax(0,1.6fr)_minmax(0,1fr)_2.5rem] items-center gap-3 border-b border-zinc-800 bg-zinc-950/60 px-3 py-2 text-[11px] text-zinc-500">
            <Checkbox checked={allVisibleSelected ? true : selected.size > 0 ? 'indeterminate' : false} onCheckedChange={toggleAll} ariaLabel="Select all shown accounts" />
            <span>Account</span><span>Status</span><span className="hidden md:block">Health</span><span className="hidden md:block">Today</span><span />
          </div>
          {visible.length === 0 && <p className="px-3 py-10 text-center text-xs text-zinc-500">
            {folder !== ALL_FOLDERS && folder !== ATTENTION && !search && status === 'all' ? 'This folder is empty. Drag accounts here, or use Move to folder.' : 'No accounts match these filters.'}</p>}
          {visible.map(profile => <AccountRow key={profile.id} profile={profile} activity={activity.get(profile.id)!}
            selected={selected.has(profile.id)} busy={busyIds.has(profile.id)} onToggle={() => toggle(profile.id)}
            onOpen={() => handlers.open(profile.id, 'overview')} onDragStart={startDrag(profile)}
            menuItems={() => accountMenu(profile, handlers)}
            onContextMenu={(e) => { const m = rowMenu(profile); menu.open(e, m.items, m.title); }}
            onMenuKey={menu.keyboardTrigger(() => rowMenu(profile).items, profile.name)} />)}
        </div>
      </div>
    </div>
    {menu.element}
  </div>;
}

function AccountRow({ profile, activity, selected, busy, onToggle, onOpen, onDragStart, menuItems, onContextMenu, onMenuKey }: {
  profile: Profile; activity: AccountActivity; selected: boolean; busy: boolean;
  onToggle: () => void; onOpen: () => void; onDragStart: (event: React.DragEvent) => void; menuItems: () => ReturnType<typeof accountMenu>;
  onContextMenu: (event: React.MouseEvent) => void; onMenuKey: (event: React.KeyboardEvent) => void;
}) {
  const health = healthFor(profile, activity);
  return <div tabIndex={0} draggable onDragStart={onDragStart} onClick={onOpen} onContextMenu={onContextMenu}
    onKeyDown={(e) => {
      onMenuKey(e);
      if (e.target === e.currentTarget && e.key === 'Enter') onOpen();
      if (e.target === e.currentTarget && e.key === ' ') { e.preventDefault(); onToggle(); }
    }}
    aria-label={`${profile.name}, ${STATUS_LABEL[profile.status]}`}
    className={cn('grid cursor-pointer grid-cols-[1.5rem_minmax(0,1.5fr)_6rem_minmax(0,1.6fr)_minmax(0,1fr)_2.5rem] items-center gap-3 border-b border-zinc-800/70 px-3 py-2.5 text-xs outline-none transition-colors last:border-b-0 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500',
      selected ? 'bg-blue-950/25' : 'hover:bg-zinc-900/60')}>
    <span onClick={(e) => e.stopPropagation()}><Checkbox checked={selected} onCheckedChange={onToggle} ariaLabel={`Select ${profile.name}`} /></span>
    <div className="min-w-0">
      <p className="truncate font-medium text-zinc-100">{profile.name}</p>
      <p className="truncate text-[11px] text-zinc-500">{[profile.group || 'No folder', warmupLabel(profile)].join(' · ')}</p>
    </div>
    <span className={cn('flex items-center gap-1.5', STATUS_TONE[profile.status])}>
      {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}{STATUS_LABEL[profile.status]}
    </span>
    <div className="hidden min-w-0 flex-wrap gap-x-2 gap-y-0.5 md:flex">
      {health.map(signal => <span key={signal.label} title={signal.hint} className={cn('whitespace-nowrap text-[11px]', TONE_CLASS[signal.tone])}>{signal.label}</span>)}
    </div>
    <div className="hidden min-w-0 text-[11px] md:block">
      {activity.running
        ? <p className="truncate text-blue-300">Now: {kindLabel(activity.running)}</p>
        : <p className="truncate text-zinc-300">{activity.postedToday ? `${activity.postedToday} posted` : 'Nothing posted'}</p>}
      <p className="truncate text-zinc-500">{activity.next ? `Next ${formatTime(activity.next.scheduled_at)}` : 'Nothing scheduled'}</p>
    </div>
    <span onClick={(e) => e.stopPropagation()} className="flex justify-end">
      <ActionMenu items={menuItems()} ariaLabel={`Actions for ${profile.name}`} className="[&>button]:min-h-7 [&>button]:border-transparent [&>button]:px-1.5" />
    </span>
  </div>;
}
