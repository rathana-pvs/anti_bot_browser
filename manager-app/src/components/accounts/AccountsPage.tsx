import { useEffect, useMemo, useState } from 'react';
import { FolderPlus, Loader2, Plus, Search, Smartphone, Users, X } from 'lucide-react';
import { Profile } from '../../types/profile';
import { ProxyItem } from '../../types/proxy';
import { QueueExecutionItem } from '../../types/automation';
import { ActionMenu } from '../ui/ActionMenu';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { EmptyState } from '../ui/EmptyState';
import { Input } from '../ui/Input';
import { useContextMenu } from '../ui/ContextMenu';
import { cn } from '../ui/cn';
import { AccountHandlers, FolderHandlers, accountMenu, bulkMenu, folderMenu } from './accountActions';
import { ACCOUNT_DRAG_TYPE, ALL_FOLDERS, ATTENTION, FolderList, NO_FOLDER, SystemFolder } from './FolderList';
import { STAGE_FOLDER_PREFIX, STAGE_META, STAGE_ORDER, StageBadge, StageView, stageFolder, stageView, useAccountHealth } from './accountHealth';
import {
  AccountActivity, HealthSignal, STATUS_LABEL, STATUS_TONE, TONE_CLASS, activityFor, browserNotes, liveSignal,
  isAndroid, networkSignal, openItemSignals, phoneLabel,
  ActivityPeriod, PERIOD_OPTIONS, periodStart,
} from './accountModel';
import { formatTime, kindLabel } from '../queue/queueModel';

type StatusFilter = 'all' | 'running' | 'stopped';
type TypeFilter = 'all' | 'browser' | 'android';

export function AccountsPage({ profiles, proxies, groups, executions, handlers, folderHandlers, busyIds, onCreate }: {
  profiles: Profile[]; proxies: ProxyItem[]; groups: string[]; executions: QueueExecutionItem[];
  handlers: AccountHandlers; folderHandlers: FolderHandlers;
  /** Accounts with a start/stop request in flight. */
  busyIds: Set<string>;
  onCreate: () => void;
}) {
  const [status, setStatus] = useState<StatusFilter>('all');
  const [kind, setKind] = useState<TypeFilter>('all');
  const [folder, setFolder] = useState(ALL_FOLDERS);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Always opens on Today; longer ranges are a deliberate, temporary choice.
  const [period, setPeriod] = useState<ActivityPeriod>(1);
  const menu = useContextMenu();

  const activity = useMemo(() => new Map(profiles.map(p => [p.id, activityFor(p.id, executions, period)])), [profiles, executions, period]);
  const { health } = useAccountHealth();
  // Stages use today's open items, whatever period the table shows.
  const stages = useMemo(() => new Map(profiles.map(p => [p.id, stageView(p, health[p.id], activityFor(p.id, executions, 1))])),
    [profiles, health, executions]);
  const systemFolders: SystemFolder[] = STAGE_ORDER.map(stage => {
    const meta = STAGE_META[stage];
    const Icon = meta.icon;
    return { key: stageFolder(stage), label: meta.label, icon: <Icon className={`h-3.5 w-3.5 ${meta.tone}`} />, tone: meta.tone, hint: meta.suggestion,
      count: profiles.filter(p => stages.get(p.id)?.stage === stage).length };
  });
  // Older links to the Needs attention view open its system folder.
  const activeFolder = folder === ATTENTION ? stageFolder('attention') : folder;
  const stageFilter = activeFolder.startsWith(STAGE_FOLDER_PREFIX) ? activeFolder.slice(STAGE_FOLDER_PREFIX.length) : null;
  const periodInfo = PERIOD_OPTIONS.find(option => option.value === period)!;
  const totals = useMemo(() => {
    let posted = 0, warmed = 0;
    activity.forEach(entry => { posted += entry.posted; warmed += entry.warmed; });
    return { posted, warmed };
  }, [activity]);
  const folders = useMemo(() => [...new Set([...groups, ...profiles.map(p => p.group || '').filter(Boolean)])]
    .sort((a, b) => a.localeCompare(b))
    .map(name => ({ name, count: profiles.filter(p => p.group === name).length })), [groups, profiles]);

  // A deleted or renamed folder must not leave the list filtered to nothing.
  useEffect(() => {
    if (![ALL_FOLDERS, ATTENTION, NO_FOLDER].includes(folder) && !folder.startsWith(STAGE_FOLDER_PREFIX)
      && !folders.some(f => f.name === folder)) setFolder(ALL_FOLDERS);
  }, [folder, folders]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return profiles.filter(p => {
      if (stageFilter && stages.get(p.id)?.stage !== stageFilter) return false;
      if (folder === NO_FOLDER && p.group) return false;
      if (!stageFilter && ![ALL_FOLDERS, NO_FOLDER].includes(folder) && (p.group || '') !== folder) return false;
      if (status === 'running' && p.status !== 'running') return false;
      if (status === 'stopped' && p.status === 'running') return false;
      if (kind === 'browser' && isAndroid(p)) return false;
      if (kind === 'android' && !isAndroid(p)) return false;
      return !q || p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q) || (p.group || '').toLowerCase().includes(q);
    }).sort((a, b) => Number(!a.group) - Number(!b.group) || (a.group || '').localeCompare(b.group || '') || a.name.localeCompare(b.name));
  }, [profiles, status, kind, folder, search, stages, stageFilter]);

  const attentionCount = profiles.filter(p => stages.get(p.id)?.stage === 'attention').length;
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
    : { items: accountMenu(profile, handlers, { failureCutoff: periodStart(period) }), title: profile.name };
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
  const heading = folder === ALL_FOLDERS ? 'All accounts' : stageFilter ? STAGE_META[stageFilter as keyof typeof STAGE_META].label : folder === NO_FOLDER ? 'No folder' : folder;

  return <div className="flex min-h-0 flex-1">
    <FolderList value={activeFolder} onChange={setFolder} folders={folders} total={profiles.length} attention={attentionCount} system={systemFolders}
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
        {profiles.some(isAndroid) && <>
          <span className="mx-1 h-4 w-px bg-zinc-800" aria-hidden="true" />
          <button type="button" className={chip(kind === 'all')} onClick={() => setKind('all')}>Any type</button>
          <button type="button" className={chip(kind === 'browser')} onClick={() => setKind('browser')}>Browser</button>
          <button type="button" className={chip(kind === 'android')} onClick={() => setKind('android')}>Phone</button>
        </>}
        <div role="radiogroup" aria-label="Activity period" className="ml-auto flex items-center gap-2">
          <span className="text-[11px] text-zinc-500">{totals.posted} posted{totals.warmed ? ` · ${totals.warmed} warmed` : ''}</span>
          <div className="flex rounded-control border border-zinc-800 bg-zinc-950 p-0.5">
            {PERIOD_OPTIONS.map(option => <button key={option.value} type="button" role="radio" aria-checked={period === option.value}
              onClick={() => setPeriod(option.value)}
              className={cn('rounded-md px-2.5 py-1 text-xs transition-colors', period === option.value ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
              {option.label}</button>)}
          </div>
        </div>
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
          <div className={cn(ROW_GRID, 'border-b border-zinc-800 bg-zinc-950/60 px-3 py-2 text-[11px] text-zinc-500')}>
            <Checkbox checked={allVisibleSelected ? true : selected.size > 0 ? 'indeterminate' : false} onCheckedChange={toggleAll} ariaLabel="Select all shown accounts" />
            <span>Account</span><span>Status</span><span className="hidden md:block">Network</span><span className="hidden md:block">Live</span><span className="hidden md:block">{periodInfo.column}</span><span />
          </div>
          {visible.length === 0 && <p className="px-3 py-10 text-center text-xs text-zinc-500">
            {stageFilter && !search && status === 'all' ? 'No accounts are in this stage right now.'
              : folder !== ALL_FOLDERS && !search && status === 'all' ? 'This folder is empty. Drag accounts here, or use Move to folder.' : 'No accounts match these filters.'}</p>}
          {visible.map(profile => <AccountRow key={profile.id} profile={profile} activity={activity.get(profile.id)!} stage={stages.get(profile.id)!}
            network={networkSignal(profile, proxies)} selected={selected.has(profile.id)} busy={busyIds.has(profile.id)} onToggle={() => toggle(profile.id)}
            onOpen={() => handlers.open(profile.id, 'overview')} onDragStart={startDrag(profile)}
            menuItems={() => accountMenu(profile, handlers, { failureCutoff: periodStart(period) })}
            onOpenQueue={() => handlers.openQueue(profile.id)}
            onContextMenu={(e) => { const m = rowMenu(profile); menu.open(e, m.items, m.title); }}
            onMenuKey={menu.keyboardTrigger(() => rowMenu(profile).items, profile.name)} />)}
        </div>
      </div>
    </div>
    {menu.element}
  </div>;
}

const ROW_GRID = 'grid grid-cols-[1.5rem_minmax(0,1.6fr)_7rem_minmax(0,1fr)_5.5rem_minmax(0,1.4fr)_2.5rem] items-center gap-3';

function Signal({ signal, onOpenQueue }: { signal: HealthSignal; onOpenQueue?: () => void }) {
  return signal.opensQueue && onOpenQueue
    ? <button type="button" title={`${signal.hint} Click to see them in the queue.`} onClick={(e) => { e.stopPropagation(); onOpenQueue(); }}
      className={cn('whitespace-nowrap underline decoration-dotted underline-offset-2 hover:decoration-solid', TONE_CLASS[signal.tone])}>{signal.label}</button>
    : <span title={signal.hint} className={cn('whitespace-nowrap', TONE_CLASS[signal.tone])}>{signal.label}</span>;
}

function AccountRow({ profile, network, activity, stage, selected, busy, onToggle, onOpen, onOpenQueue, onDragStart, menuItems, onContextMenu, onMenuKey }: {
  profile: Profile; network: HealthSignal; activity: AccountActivity; stage: StageView; selected: boolean; busy: boolean;
  onToggle: () => void; onOpen: () => void; onOpenQueue: () => void; onDragStart: (event: React.DragEvent) => void; menuItems: () => ReturnType<typeof accountMenu>;
  onContextMenu: (event: React.MouseEvent) => void; onMenuKey: (event: React.KeyboardEvent) => void;
}) {
  const notes = browserNotes(profile);
  const openItems = openItemSignals(activity);
  const done = [activity.posted && `${activity.posted} posted`, activity.warmed && `${activity.warmed} warmed`].filter(Boolean).join(' · ');
  return <div tabIndex={0} draggable onDragStart={onDragStart} onClick={onOpen} onContextMenu={onContextMenu}
    onKeyDown={(e) => {
      onMenuKey(e);
      if (e.target === e.currentTarget && e.key === 'Enter') onOpen();
      if (e.target === e.currentTarget && e.key === ' ') { e.preventDefault(); onToggle(); }
    }}
    aria-label={`${profile.name}, ${STATUS_LABEL[profile.status]}`}
    className={cn(ROW_GRID, 'cursor-pointer border-b border-zinc-800/70 px-3 py-2.5 text-xs outline-none transition-colors last:border-b-0 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500',
      selected ? 'bg-blue-950/25' : 'hover:bg-zinc-900/60')}>
    <span onClick={(e) => e.stopPropagation()}><Checkbox checked={selected} onCheckedChange={onToggle} ariaLabel={`Select ${profile.name}`} /></span>
    <div className="min-w-0">
      <p className="flex items-center gap-1.5 truncate font-medium text-zinc-100">
        {isAndroid(profile) && <Smartphone className="h-3.5 w-3.5 shrink-0 text-zinc-400" aria-label="Phone account" />}{profile.name}</p>
      <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500"><StageBadge view={stage} quietReady />
        <span className="truncate">{[profile.group || 'No folder', isAndroid(profile) && phoneLabel(profile)].filter(Boolean).join(' · ')}</span></p>
    </div>
    <div className="min-w-0">
      {isAndroid(profile)
        ? <span className="flex items-center gap-1.5 text-zinc-400"><span className="h-1.5 w-1.5 rounded-full bg-current" />On phone</span>
        : <span className={cn('flex items-center gap-1.5', STATUS_TONE[profile.status])}>
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}{STATUS_LABEL[profile.status]}
        </span>}
      {notes.filter(note => note.label !== 'Browser error').map(note => <p key={note.label} className="truncate text-[11px]"><Signal signal={note} /></p>)}
    </div>
    <div className="hidden min-w-0 text-[11px] md:block" title={network.hint}>
      <span className={cn('inline-flex max-w-full rounded-md border border-current/20 bg-current/5 px-1.5 py-0.5', TONE_CLASS[network.tone])}><Signal signal={network} /></span>
      {network.detail && <p className="mt-1 truncate text-[10px] text-zinc-500">{network.detail}</p>}
    </div>
    <div className="hidden text-[11px] md:block"><Signal signal={liveSignal(profile)} /></div>
    <div className="hidden min-w-0 space-y-0.5 text-[11px] md:block">
      <p className="truncate text-zinc-300">{done || 'Nothing posted'}</p>
      {activity.running
        ? <p className="truncate text-blue-300">Now: {kindLabel(activity.running)}</p>
        : <p className="truncate text-zinc-500">{activity.next ? `Next ${formatTime(activity.next.scheduled_at)}` : 'Nothing scheduled'}</p>}
      {openItems.length > 0 && <p className="flex flex-wrap gap-x-2">{openItems.map(signal => <Signal key={signal.label} signal={signal} onOpenQueue={onOpenQueue} />)}</p>}
    </div>
    <span onClick={(e) => e.stopPropagation()} className="flex justify-end">
      <ActionMenu items={menuItems()} ariaLabel={`Actions for ${profile.name}`} className="[&>button]:min-h-7 [&>button]:border-transparent [&>button]:px-1.5" />
    </span>
  </div>;
}
