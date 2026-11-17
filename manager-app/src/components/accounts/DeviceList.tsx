import { Monitor, Smartphone } from 'lucide-react';
import { cn } from '../ui/cn';
import { DeviceEntry, deviceSummary } from './accountDevices';
import { SidebarSection } from './SidebarSection';

/** The Devices part of the Accounts sidebar: each device is a folder holding its accounts.
 * Accounts can't be dropped here: an account's device is fixed when it is created. */
export function DeviceList({ entries, value, onChange, onMenu, menuKey }: {
  entries: DeviceEntry[]; value: string | null; onChange: (key: string) => void;
  onMenu: (event: React.MouseEvent, entry: DeviceEntry) => void;
  menuKey: (entry: DeviceEntry) => (event: React.KeyboardEvent) => void;
}) {
  const item = (entry: DeviceEntry) => {
    const Icon = entry.kind === 'pc' ? Monitor : Smartphone;
    const active = value === entry.key;
    return <button key={entry.key} type="button" aria-current={active ? 'true' : undefined} title={`${entry.label}: ${deviceSummary(entry)}`}
      onClick={() => onChange(entry.key)}
      onContextMenu={(event) => { event.stopPropagation(); onMenu(event, entry); }} onKeyDown={menuKey(entry)}
      className={cn('flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500',
        active ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200')}>
      <span className="relative shrink-0">
        <Icon className={cn('h-3.5 w-3.5', entry.kind === 'pc' ? 'text-violet-300' : 'text-teal-300')} aria-hidden="true" />
        {entry.kind === 'phone' && <span aria-hidden="true" className={cn('absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full ring-1 ring-zinc-950',
          entry.online ? 'bg-emerald-400' : 'bg-zinc-600')} />}
      </span>
      <span className="min-w-0 flex-1 truncate">{entry.label}</span>
      <span className="text-[11px] text-zinc-500">{entry.count}</span>
    </button>;
  };
  return <SidebarSection id="devices" title="Devices" activeChild={entries.filter(entry => entry.key === value).map(item)}>
    {entries.map(item)}
  </SidebarSection>;
}
