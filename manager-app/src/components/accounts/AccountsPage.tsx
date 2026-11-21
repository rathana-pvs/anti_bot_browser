import { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, ExternalLink, FileUp, FolderOpen, FolderPlus, ListChecks, Loader2, Monitor, Plus, Rows3, Search, Smartphone, Users, X } from 'lucide-react';
import { Profile } from '../../types/profile';
import { ProxyItem, proxyHolders } from '../../types/proxy';
import { QueueExecutionItem } from '../../types/automation';
import { AndroidDevice } from '../../types/device';
import { ActionMenu } from '../ui/ActionMenu';
import { Button } from '../ui/Button';
import { Checkbox } from '../ui/Checkbox';
import { EmptyState } from '../ui/EmptyState';
import { Input } from '../ui/Input';
import { useContextMenu } from '../ui/ContextMenu';
import { cn } from '../ui/cn';
import { SortButton, sortRows, useSort } from '../ui/sort';
import { AccountHandlers, FolderHandlers, accountMenu, bulkMenu, folderMenu } from './accountActions';
import { ACCOUNT_DRAG_TYPE, ALL_FOLDERS, ATTENTION, FolderList, NO_FOLDER, SystemFolder } from './FolderList';
import { STAGE_FOLDER_PREFIX, STAGE_META, STAGE_ORDER, StageBadge, StageView, stageFolder, stageView, useAccountHealth } from './accountHealth';
import {
  AccountActivity, HealthSignal, STATUS_LABEL, STATUS_TONE, TONE_CLASS, activityFor, browserNotes,
  deviceLabel, isAndroid, isOnScreen, networkSignal, openItemSignals, phoneStatus,
  ActivityPeriod, PERIOD_OPTIONS, periodStart,
} from './accountModel';
import { formatTime, kindLabel } from '../queue/queueModel';
import { DeviceChip } from './DeviceChip';
import { ProxyShareBadge } from './ProxyShareBadge';
import { pageCells } from './facebookPage';
import { DeviceEntry, PC_DEVICE, deviceEntries, deviceKeyOf, deviceSummary } from './accountDevices';
import { DeviceList } from './DeviceList';
import type { ActionMenuItem } from '../ui/ActionMenu';

/** Folder value of a device's folder: the prefix, then This PC's key or a phone's serial. */
const DEVICE_FOLDER_PREFIX = 'device:';
const GROUP_BY_DEVICE_KEY = 'accounts.groupByDevice';
const readGroupByDevice = () => { try { return localStorage.getItem(GROUP_BY_DEVICE_KEY) === '1'; } catch { return false; } };

type StatusFilter = 'all' | 'running' | 'stopped';
type AccountSortKey = 'name' | 'page' | 'followers' | 'device' | 'status' | 'network' | 'period';

function accountSortValue(profile: Profile, key: AccountSortKey,
  ctx: { devices: AndroidDevice[]; proxies: ProxyItem[]; activity: Map<string, AccountActivity> }): string | number | null {
  switch (key) {
    case 'name': return profile.name;
    case 'page': return pageCells(profile.facebook_page, isAndroid(profile)).sortName;
    case 'followers': return pageCells(profile.facebook_page, isAndroid(profile)).sortFollowers;
    case 'device': return deviceLabel(profile, ctx.devices);
    case 'status': return isAndroid(profile) ? phoneStatus(profile, ctx.devices)?.label ?? null : STATUS_LABEL[profile.status];
    case 'network': return networkSignal(profile, ctx.proxies).label;
    case 'period': return ctx.activity.get(profile.id)?.posted ?? 0;
  }
}

export function AccountsPage({ profiles, proxies, devices, groups, executions, handlers, folderHandlers, busyIds, onCreate, onImport, onOpenDevice, deviceFocus, onDeviceFocused }: {
  profiles: Profile[]; proxies: ProxyItem[]; devices: AndroidDevice[]; groups: string[]; executions: QueueExecutionItem[];
  handlers: AccountHandlers; folderHandlers: FolderHandlers;
  /** Accounts with a start/stop request in flight. */
  busyIds: Set<string>;
  onCreate: () => void;
  /** Opens the import dialog for an account exported on another PC. */
  onImport: () => void;
  /** Opens the Devices page on this phone serial, or on This PC when null. */
  onOpenDevice: (serial: string | null) => void;
  /** Set by the Devices page's View accounts: shows only that device's accounts. */
  deviceFocus?: { key: string } | null;
  /** Called once deviceFocus is applied, so coming back to the list later doesn't apply it again. */
  onDeviceFocused?: () => void;
}) {
  const [status, setStatus] = useState<StatusFilter>('all');
  const [groupByDevice, setGroupByDevice] = useState(readGroupByDevice);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [folder, setFolder] = useState(ALL_FOLDERS);
  const [search, setSearch] = useState('');
  // Set by a row's "accounts on this proxy" badge.
  const [proxyFilter, setProxyFilter] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Always opens on Today; longer ranges are a deliberate, temporary choice.
  const [period, setPeriod] = useState<ActivityPeriod>(1);
  const { sort, toggle: toggleSort } = useSort<AccountSortKey>();
  const menu = useContextMenu();

  const activity = useMemo(() => new Map(profiles.map(p => [p.id, activityFor(p.id, executions, period)])), [profiles, executions, period]);
  const { health } = useAccountHealth();
  // Account health is independent of job failures and the table's activity period.
  const stages = useMemo(() => new Map(profiles.map(p => [p.id, stageView(p, health[p.id])])),
    [profiles, health]);
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
      && !folder.startsWith(DEVICE_FOLDER_PREFIX) && !folders.some(f => f.name === folder)) setFolder(ALL_FOLDERS);
  }, [folder, folders]);

  // A removed proxy must not leave the list filtered to nothing.
  const filterProxy = proxyFilter ? proxies.find(proxy => proxy.id === proxyFilter) ?? null : null;
  useEffect(() => { if (proxyFilter && !filterProxy) setProxyFilter(null); }, [proxyFilter, filterProxy]);
  const toggleProxyFilter = (proxyId: string) => setProxyFilter(current => current === proxyId ? null : proxyId);

  // Each device is a folder of its own: This PC (PC_DEVICE) or a phone's serial.
  const deviceList = useMemo(() => deviceEntries(profiles, devices), [profiles, devices]);
  const deviceFilter = activeFolder.startsWith(DEVICE_FOLDER_PREFIX) ? activeFolder.slice(DEVICE_FOLDER_PREFIX.length) : null;
  const filterDevice = deviceFilter ? deviceList.find(entry => entry.key === deviceFilter) ?? null : null;
  // A removed phone must not leave the list showing nothing.
  useEffect(() => { if (deviceFilter && !filterDevice) setFolder(ALL_FOLDERS); }, [deviceFilter, filterDevice]);
  const openDeviceFolder = (key: string) => setFolder(DEVICE_FOLDER_PREFIX + key);
  // The Devices page's View accounts opens that device's folder.
  useEffect(() => {
    if (!deviceFocus) return;
    openDeviceFolder(deviceFocus.key);
    setProxyFilter(null);
    onDeviceFocused?.();
  }, [deviceFocus]); // eslint-disable-line react-hooks/exhaustive-deps
  const setGrouping = (on: boolean) => {
    setGroupByDevice(on);
    try { localStorage.setItem(GROUP_BY_DEVICE_KEY, on ? '1' : '0'); } catch { /* remembered for this visit only */ }
  };

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const onProxy = filterProxy ? new Set(proxyHolders(filterProxy)) : null;
    const filtered = profiles.filter(p => {
      if (onProxy && !onProxy.has(p.id)) return false;
      if (stageFilter && stages.get(p.id)?.stage !== stageFilter) return false;
      if (folder === NO_FOLDER && p.group) return false;
      if (!stageFilter && !deviceFilter && ![ALL_FOLDERS, NO_FOLDER].includes(folder) && (p.group || '') !== folder) return false;
      if (status === 'running' && p.status !== 'running') return false;
      if (status === 'stopped' && p.status === 'running') return false;
      if (deviceFilter && deviceKeyOf(p, devices) !== deviceFilter) return false;
      return !q || [p.name, p.id, p.group || '', deviceLabel(p, devices)].some(value => value.toLowerCase().includes(q));
    });
    // A clicked column sorts the whole list; otherwise group accounts by folder, then name.
    if (sort) return sortRows(filtered, sort, (p, key) => accountSortValue(p, key, { devices, proxies, activity }));
    return filtered.sort((a, b) => Number(!a.group) - Number(!b.group) || (a.group || '').localeCompare(b.group || '') || a.name.localeCompare(b.name));
  }, [profiles, devices, proxies, status, deviceFilter, folder, search, stages, stageFilter, sort, activity, filterProxy]);

  // Grouped, each device's accounts keep the list's order under that device's header.
  const sections = useMemo(() => {
    if (!groupByDevice) return null;
    const byDevice = new Map<string, Profile[]>();
    visible.forEach(p => {
      const key = deviceKeyOf(p, devices);
      byDevice.set(key, [...(byDevice.get(key) ?? []), p]);
    });
    return deviceList.filter(entry => byDevice.has(entry.key)).map(entry => ({ entry, rows: byDevice.get(entry.key)! }));
  }, [groupByDevice, visible, devices, deviceList]);

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

  const clearSelection = () => setSelected(new Set());
  // Deleted accounts must not linger in the selection count.
  useEffect(() => {
    setSelected(current => {
      const kept = new Set([...current].filter(id => profiles.some(p => p.id === id)));
      return kept.size === current.size ? current : kept;
    });
  }, [profiles]);

  const entryOf = (profile: Profile) => deviceList.find(entry => entry.key === deviceKeyOf(profile, devices));
  const deviceMenu = (entry: DeviceEntry, { selectAccounts = true } = {}): ActionMenuItem[] => [
    ...(deviceFilter === entry.key ? [] : [{ label: `Open ${entry.label} folder`, icon: <FolderOpen className="h-3.5 w-3.5" />, onSelect: () => openDeviceFolder(entry.key) }]),
    ...(selectAccounts ? [{ label: `Select its shown accounts`, icon: <ListChecks className="h-3.5 w-3.5" />,
      disabled: !visible.some(p => deviceKeyOf(p, devices) === entry.key),
      onSelect: () => setSelected(new Set(visible.filter(p => deviceKeyOf(p, devices) === entry.key).map(p => p.id))) }] : []),
    { label: `Go to ${entry.label} on Devices`, icon: <ExternalLink className="h-3.5 w-3.5" />,
      onSelect: () => onOpenDevice(entry.key === PC_DEVICE ? null : entry.key) },
  ];
  const singleMenu = (profile: Profile) => {
    const entry = entryOf(profile);
    const items = accountMenu(profile, handlers, { failureCutoff: periodStart(period), onScreen: isOnScreen(profile, devices) });
    if (entry) items.push(...deviceMenu(entry, { selectAccounts: false }).map((item, index) => index === 0 ? { ...item, divider: true } : item));
    return items;
  };
  const rowMenu = (profile: Profile) => selected.has(profile.id) && selected.size > 1
    ? { items: bulkMenu(selectedProfiles, handlers, clearSelection), title: `${selected.size} accounts` }
    : { items: singleMenu(profile), title: profile.name };
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
    if (!moving.length) return;
    // Dropping the selection somewhere is the end of that selection.
    const draggedSelection = ids.some(id => selected.has(id));
    void folderHandlers.moveTo(moving, target).then(done => { if (done && draggedSelection) clearSelection(); });
  };

  const renderRow = (profile: Profile) => <AccountRow key={profile.id} profile={profile} devices={devices}
    onOpenDevice={() => { const key = deviceKeyOf(profile, devices); onOpenDevice(key === PC_DEVICE ? null : key); }} activity={activity.get(profile.id)!} stage={stages.get(profile.id)!}
            network={networkSignal(profile, proxies)} proxyShare={<ProxyShareBadge profile={profile} proxies={proxies} profiles={profiles}
              active={!!filterProxy && proxyHolders(filterProxy).includes(profile.id)} onFilter={toggleProxyFilter} />} selected={selected.has(profile.id)} busy={busyIds.has(profile.id)} onToggle={() => toggle(profile.id)}
            onOpen={() => handlers.open(profile.id, 'browser')} onDragStart={startDrag(profile)}
            menuItems={() => singleMenu(profile)}
            onOpenQueue={() => handlers.openQueue(profile.id)}
            onContextMenu={(e) => { const m = rowMenu(profile); menu.open(e, m.items, m.title); }}
            onMenuKey={menu.keyboardTrigger(() => rowMenu(profile).items, profile.name)} />;

  if (!profiles.length) {
    return <div className="flex flex-1 items-center justify-center p-6">
      <EmptyState icon={<Users className="h-6 w-6" />} title="Create your first account"
        description="Each account gets its own isolated browser, cookies, and network."
        action={<div className="flex gap-2">
          <Button variant="outline" onClick={onImport}><FileUp className="h-3.5 w-3.5" />Import</Button>
          <Button onClick={onCreate}><Plus className="h-3.5 w-3.5" />New account</Button>
        </div>} />
    </div>;
  }

  const chip = (active: boolean) => cn('rounded-full border px-3 py-1 text-xs transition-colors',
    active ? 'border-blue-600 bg-blue-950/40 text-blue-200' : 'border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200');
  const heading = filterDevice ? filterDevice.label : folder === ALL_FOLDERS ? 'All accounts' : stageFilter ? STAGE_META[stageFilter as keyof typeof STAGE_META].label : folder === NO_FOLDER ? 'No folder' : folder;

  return <div className="flex min-h-0 flex-1">
    <FolderList value={activeFolder} onChange={setFolder} folders={folders} total={profiles.length} attention={attentionCount} system={systemFolders}
      unfiled={profiles.filter(p => !p.group).length} onCreate={folderHandlers.create} onDropAccounts={dropAccounts}
      onFolderMenu={(event, name) => menu.open(event, menuForFolder(name), name)}
      onBlankMenu={(event) => menu.open(event, blankMenu)}
      menuKey={(name) => menu.keyboardTrigger(() => menuForFolder(name), name)}>
      {deviceList.length > 1 && <DeviceList entries={deviceList} value={deviceFilter} onChange={openDeviceFolder}
        onMenu={(event, entry) => menu.open(event, deviceMenu(entry), entry.label)}
        menuKey={(entry) => menu.keyboardTrigger(() => deviceMenu(entry), entry.label)} />}
    </FolderList>

    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border bg-surface/50 px-6 py-3">
        <h1 className="truncate text-sm font-semibold text-zinc-100">{heading}</h1>
        <span className="text-xs text-zinc-500">{profiles.length} accounts · {runningCount} running</span>
        <div className="ml-auto flex items-center gap-2">
          <div className="relative w-56">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name, folder, device, or ID" aria-label="Search accounts" className="min-h-8 py-1 pl-8" />
          </div>
          <Button size="sm" variant="outline" onClick={onImport} title="Import an account exported on another PC"><FileUp className="h-3.5 w-3.5" />Import</Button>
          <Button size="sm" onClick={onCreate}><Plus className="h-3.5 w-3.5" />New account</Button>
        </div>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-2 px-6 pt-3">
        <button type="button" className={chip(status === 'all')} onClick={() => setStatus('all')}>Any status</button>
        <button type="button" className={chip(status === 'running')} onClick={() => setStatus('running')}>Running</button>
        <button type="button" className={chip(status === 'stopped')} onClick={() => setStatus('stopped')}>Not running</button>
        {filterProxy && <>
          <span className="mx-1 h-4 w-px bg-zinc-800" aria-hidden="true" />
          <button type="button" className={cn(chip(true), 'inline-flex items-center gap-1.5')} onClick={() => setProxyFilter(null)}
            title="Show all accounts again" aria-label={`Showing accounts on proxy ${filterProxy.host}:${filterProxy.port}; clear this filter`}>
            <Users className="h-3 w-3" aria-hidden="true" />
            <span className="font-mono">{filterProxy.host}:{filterProxy.port}</span>
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        </>}
        {deviceList.length > 1 && <button type="button" aria-pressed={groupByDevice} onClick={() => setGrouping(!groupByDevice)} title="Show each device's accounts under its own header"
          className={cn(chip(groupByDevice), 'ml-auto inline-flex items-center gap-1.5')}><Rows3 className="h-3 w-3" aria-hidden="true" />Group by device</button>}
        <div role="radiogroup" aria-label="Activity period" className={cn('flex items-center gap-2', deviceList.length > 1 ? 'ml-3' : 'ml-auto')}>
          <span className="text-[11px] text-zinc-500">{totals.posted} posted{totals.warmed ? ` · ${totals.warmed} warmed` : ''}</span>
          <div className="flex rounded-control border border-zinc-800 bg-zinc-950 p-0.5">
            {PERIOD_OPTIONS.map(option => <button key={option.value} type="button" role="radio" aria-checked={period === option.value}
              onClick={() => setPeriod(option.value)}
              className={cn('rounded-md px-2.5 py-1 text-xs transition-colors', period === option.value ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-zinc-200')}>
              {option.label}</button>)}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-3">
        <div className="overflow-hidden rounded-xl border border-zinc-800">
          <div className={cn(ROW_GRID, 'min-h-11 border-b border-zinc-800 px-3 text-[11px]', selected.size > 0 ? 'bg-blue-950/30 text-blue-100' : 'bg-zinc-950/60 text-zinc-500')}>
            <span onClick={toggleAll} className="-ml-3 flex cursor-pointer items-center self-stretch pl-3 pr-2">
              <Checkbox checked={allVisibleSelected ? true : selected.size > 0 ? 'indeterminate' : false} onCheckedChange={toggleAll} ariaLabel="Select all shown accounts" className="pointer-events-none" />
            </span>
            {selected.size > 0
              ? <div className="col-[2/-1] flex flex-wrap items-center gap-1">
                  <span className="mr-1 text-xs font-medium">{selected.size} selected</span>
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-1">
                    {bulkMenu(selectedProfiles, handlers, clearSelection).map(item => <Button key={item.label} size="sm" variant="ghost" disabled={item.disabled}
                      className={item.danger ? 'text-red-300 hover:text-red-200' : 'text-blue-100'} onClick={item.onSelect}>{item.icon}{item.label}</Button>)}
                    <Button size="sm" variant="ghost" onClick={clearSelection} aria-label="Clear selection"><X className="h-3.5 w-3.5" /></Button>
                  </div>
                </div>
              : <>
                  <SortButton label="Account" column="name" sort={sort} onToggle={toggleSort} />
                  <SortButton label="Page" column="page" sort={sort} onToggle={toggleSort} />
                  <SortButton label="Followers" column="followers" sort={sort} onToggle={toggleSort} />
                  <SortButton label="Device" column="device" sort={sort} onToggle={toggleSort} />
                  <SortButton label="Status" column="status" sort={sort} onToggle={toggleSort} />
                  <SortButton label="Network" column="network" sort={sort} onToggle={toggleSort} className="hidden md:inline-flex" />
                  <SortButton label={periodInfo.column} column="period" sort={sort} onToggle={toggleSort} className="hidden md:inline-flex" />
                  <span />
                </>}
          </div>
          {visible.length === 0 && <p className="px-3 py-10 text-center text-xs text-zinc-500">
            {stageFilter && !search && status === 'all' ? 'No accounts are in this stage right now.'
              : filterDevice && !search && status === 'all' ? 'No accounts on this device yet.'
              : folder !== ALL_FOLDERS && !search && status === 'all' ? 'This folder is empty. Drag accounts here, or use Move to folder.' : 'No accounts match these filters.'}</p>}
          {sections
            ? sections.map(({ entry, rows }) => {
              const closed = collapsed.has(entry.key);
              const Icon = entry.kind === 'pc' ? Monitor : Smartphone;
              return <section key={entry.key} aria-label={`${entry.label} accounts`}>
                <div onContextMenu={(event) => menu.open(event, deviceMenu(entry), entry.label)}
                  className="flex items-center gap-2 border-b border-zinc-800 bg-zinc-900/60 px-3 py-2 text-xs">
                  <button type="button" aria-expanded={!closed} onClick={() => setCollapsed(current => {
                    const next = new Set(current);
                    if (next.has(entry.key)) next.delete(entry.key); else next.add(entry.key);
                    return next;
                  })} className="flex min-w-0 flex-1 items-center gap-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                    {closed ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-500" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-zinc-500" />}
                    <Icon className={cn('h-3.5 w-3.5 shrink-0', entry.kind === 'pc' ? 'text-violet-300' : 'text-teal-300')} aria-hidden="true" />
                    <span className="truncate font-semibold text-zinc-100">{entry.label}</span>
                    {entry.kind === 'phone' && <span className={cn('rounded-full px-1.5 py-px text-[10px]',
                      entry.online ? 'bg-emerald-500/15 text-emerald-300' : 'bg-zinc-800 text-zinc-400')}>{entry.online ? 'Online' : entry.online === false ? 'Offline' : 'Not connected'}</span>}
                    <span className="truncate text-[11px] text-zinc-500">
                      {entry.kind === 'pc' ? deviceSummary(entry) : deviceSummary(entry).split(' · ').slice(0, 1).join('')}
                      {' · '}{rows.length === entry.count ? `${rows.length} account${rows.length === 1 ? '' : 's'}` : `${rows.length} of ${entry.count} shown`}</span>
                  </button>
                  <ActionMenu items={deviceMenu(entry)} ariaLabel={`Actions for ${entry.label}`} className="[&>button]:min-h-7 [&>button]:border-transparent [&>button]:px-1.5" />
                </div>
                {!closed && rows.map(renderRow)}
              </section>;
            })
            : visible.map(renderRow)}
        </div>
      </div>
    </div>
    {menu.element}
  </div>;
}

const ROW_GRID = 'grid grid-cols-[1.5rem_minmax(0,1.4fr)_minmax(0,1.2fr)_5rem_minmax(0,1.2fr)_7rem_minmax(0,1fr)_minmax(0,1.4fr)_2.5rem] items-center gap-3';

function Signal({ signal, onOpenQueue }: { signal: HealthSignal; onOpenQueue?: () => void }) {
  return signal.opensQueue && onOpenQueue
    ? <button type="button" title={`${signal.hint} Click to see them in the queue.`} onClick={(e) => { e.stopPropagation(); onOpenQueue(); }}
      className={cn('whitespace-nowrap underline decoration-dotted underline-offset-2 hover:decoration-solid', TONE_CLASS[signal.tone])}>{signal.label}</button>
    : <span title={signal.hint} className={cn('whitespace-nowrap', TONE_CLASS[signal.tone])}>{signal.label}</span>;
}

function AccountRow({ profile, devices, onOpenDevice, network, proxyShare, activity, stage, selected, busy, onToggle, onOpen, onOpenQueue, onDragStart, menuItems, onContextMenu, onMenuKey }: {
  profile: Profile; devices: AndroidDevice[];
  /** Opens the account's device on the Devices page. */
  onOpenDevice: () => void; network: HealthSignal; proxyShare: React.ReactNode; activity: AccountActivity; stage: StageView; selected: boolean; busy: boolean;
  onToggle: () => void; onOpen: () => void; onOpenQueue: () => void; onDragStart: (event: React.DragEvent) => void; menuItems: () => ReturnType<typeof accountMenu>;
  onContextMenu: (event: React.MouseEvent) => void; onMenuKey: (event: React.KeyboardEvent) => void;
}) {
  const notes = browserNotes(profile);
  const openItems = openItemSignals(activity);
  const phone = isAndroid(profile) ? phoneStatus(profile, devices) : null;
  const page = pageCells(profile.facebook_page, isAndroid(profile));
  const done = [activity.posted && `${activity.posted} posted`, activity.warmed && `${activity.warmed} warmed`].filter(Boolean).join(' · ');
  return <div tabIndex={0} draggable onDragStart={onDragStart} onClick={onOpen} onContextMenu={onContextMenu}
    onKeyDown={(e) => {
      onMenuKey(e);
      if (e.target === e.currentTarget && e.key === 'Enter') onOpen();
      if (e.target === e.currentTarget && e.key === ' ') { e.preventDefault(); onToggle(); }
    }}
    aria-label={`${profile.name}, ${deviceLabel(profile, devices)}, ${phone?.label ?? STATUS_LABEL[profile.status]}`}
    className={cn(ROW_GRID, 'cursor-pointer border-b border-zinc-800/70 px-3 py-2.5 text-xs outline-none transition-colors last:border-b-0 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500',
      selected ? 'bg-blue-950/25' : 'hover:bg-zinc-900/60')}>
    <span onClick={(e) => { e.stopPropagation(); onToggle(); }} className="-my-2.5 -ml-3 flex cursor-pointer items-center self-stretch py-2.5 pl-3 pr-2">
      <Checkbox checked={selected} onCheckedChange={onToggle} ariaLabel={`Select ${profile.name}`} className="pointer-events-none" />
    </span>
    <div className="min-w-0">
      <p className="flex items-center gap-1.5 truncate font-medium text-zinc-100">
        {isAndroid(profile) && <Smartphone className="h-3.5 w-3.5 shrink-0 text-zinc-400" aria-label="Phone account" />}{profile.name}</p>
      <p className="flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-500"><StageBadge view={stage} compact quietReady />
        <span className="truncate">{profile.group || 'No folder'}</span></p>
    </div>
    <div className="min-w-0 truncate text-[11px] text-zinc-300" title={page.pageHint}>{page.page}</div>
    <div className="text-[11px] tabular-nums text-zinc-300" title={page.followersHint}>{page.followers}</div>
    <div className="min-w-0"><DeviceChip profile={profile} devices={devices} onClick={onOpenDevice} /></div>
    <div className="min-w-0">
      {phone
        ? <span className={cn('flex items-center gap-1.5', phone.tone)}><span className="h-1.5 w-1.5 rounded-full bg-current" />{phone.label}</span>
        : <span className={cn('flex items-center gap-1.5', STATUS_TONE[profile.status])}>
          {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}{STATUS_LABEL[profile.status]}
        </span>}
      {notes.filter(note => note.label !== 'Browser error').map(note => <p key={note.label} className="truncate text-[11px]"><Signal signal={note} /></p>)}
    </div>
    <div className="hidden min-w-0 text-[11px] md:block" title={network.hint}>
      <span className="flex min-w-0 items-center gap-1">
        <span className={cn('inline-flex min-w-0 max-w-full rounded-md border border-current/20 bg-current/5 px-1.5 py-0.5', TONE_CLASS[network.tone])}><Signal signal={network} /></span>
        {proxyShare}
      </span>
      {network.detail && <p className="mt-1 truncate text-[10px] text-zinc-500">{network.detail}</p>}
    </div>
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
