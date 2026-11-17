import { ReactNode, useState } from 'react';
import { AlertTriangle, Folder, FolderPlus, Inbox, Lock, Users } from 'lucide-react';
import { cn } from '../ui/cn';
import { SidebarSection } from './SidebarSection';

export const ALL_FOLDERS = '__all__';
export const ATTENTION = '__attention__';
export const NO_FOLDER = '__none__';
/** Drag payload type for moving accounts between folders. */
export const ACCOUNT_DRAG_TYPE = 'application/x-account-ids';

export interface FolderEntry { name: string; count: number }
/** A folder the app fills by itself; it has no menu and accepts no drops. */
export interface SystemFolder { key: string; label: string; icon: ReactNode; count: number; tone?: string; hint?: string }

/**
 * Left column of the Accounts page. Folders filter the list, accept dropped accounts,
 * and open a right-click menu. Smart views (All, Needs attention) only filter.
 */
export function FolderList({ value, onChange, folders, total, attention, unfiled, onCreate, onDropAccounts, onFolderMenu, onBlankMenu, menuKey, allLabel = 'All accounts', emptyHint = 'Create a folder, then drag accounts onto it.', dragType = ACCOUNT_DRAG_TYPE, automatic = false, system, children }: {
  value: string; onChange: (value: string) => void; folders: FolderEntry[];
  total: number; attention: number; unfiled: number;
  onCreate: () => void;
  /** `folder` is '' for “No folder”. */
  onDropAccounts: (ids: string[], folder: string) => void;
  onFolderMenu: (event: React.MouseEvent, folder: string) => void;
  onBlankMenu: (event: React.MouseEvent) => void;
  menuKey: (folder: string) => (event: React.KeyboardEvent) => void;
  allLabel?: string; emptyHint?: string; dragType?: string;
  automatic?: boolean;
  /** System folders shown above the user's folders, in place of Needs attention. */
  system?: SystemFolder[];
  /** More filters shown below the folders, e.g. Devices. */
  children?: ReactNode;
}) {
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const dropProps = (key: string, folder: string) => ({
    onDragOver: (event: React.DragEvent) => {
      if (!event.dataTransfer.types.includes(dragType)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      setDropTarget(key);
    },
    onDragLeave: () => setDropTarget(current => current === key ? null : current),
    onDrop: (event: React.DragEvent) => {
      setDropTarget(null);
      const raw = event.dataTransfer.getData(dragType);
      if (!raw) return;
      event.preventDefault();
      try { onDropAccounts(JSON.parse(raw), folder); } catch (_) { /* ignore foreign drags */ }
    },
  });

  const item = (key: string, label: string, icon: ReactNode, count: number, extra: Record<string, unknown> = {}, tone?: string) =>
    <button key={key} type="button" onClick={() => onChange(key)} aria-current={value === key ? 'true' : undefined} {...extra}
      className={cn('flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500',
        value === key ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200',
        dropTarget === key && 'bg-blue-950/60 text-blue-100 ring-1 ring-blue-600', tone)}>
      {icon}<span className="min-w-0 flex-1 truncate">{label}</span><span className="text-[11px] text-zinc-500">{count}</span>
    </button>;

  const systemItem = (folder: SystemFolder) => item(folder.key, folder.label, folder.icon, folder.count,
    { title: folder.hint }, value === folder.key || !folder.count ? '' : folder.tone);
  const folderItem = (folder: FolderEntry) => item(folder.name, folder.name, <Folder className="h-3.5 w-3.5" />, folder.count, {
    ...(automatic ? {} : dropProps(folder.name, folder.name)),
    onContextMenu: automatic ? undefined : (event: React.MouseEvent) => onFolderMenu(event, folder.name),
    onKeyDown: automatic ? undefined : menuKey(folder.name),
  });
  const noFolderItem = item(NO_FOLDER, 'No folder', <Inbox className="h-3.5 w-3.5" />, unfiled, { ...dropProps(NO_FOLDER, '') });
  const folderItems = <>
    {folders.map(folderItem)}
    {!automatic && noFolderItem}
    {automatic && <p className="px-2.5 pt-3 text-[11px] leading-relaxed text-zinc-500">Folders update automatically from profile assignments and speed checks.</p>}
    {!folders.length && <p className="px-2.5 pt-2 text-[11px] leading-relaxed text-zinc-500">{emptyHint}</p>}
  </>;

  return <aside aria-label="Folders" onContextMenu={automatic ? undefined : onBlankMenu} className="flex w-56 shrink-0 flex-col border-r border-border bg-surface/30">
    <div className="flex items-center justify-between px-4 pb-2 pt-3">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">Folders</span>
      {!automatic && <button type="button" onClick={onCreate} aria-label="New folder" title="New folder"
        className="rounded-md p-1 text-zinc-400 hover:bg-zinc-800 hover:text-white"><FolderPlus className="h-4 w-4" /></button>}
    </div>
    <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
      {item(ALL_FOLDERS, allLabel, <Users className="h-3.5 w-3.5" />, total)}
      {!system && attention > 0 && item(ATTENTION, 'Needs attention', <AlertTriangle className="h-3.5 w-3.5" />, attention, {}, value === ATTENTION ? '' : 'text-amber-300')}
      {system && <SidebarSection id="health" title="Account health" icon={<Lock className="h-3 w-3" aria-hidden="true" />}
        hint="Filled automatically from each account's history. These folders can't be renamed or deleted."
        activeChild={system.filter(folder => folder.key === value).map(systemItem)}>
        {system.map(systemItem)}
      </SidebarSection>}
      {!system && <div className="my-2 border-t border-zinc-800" />}
      {system
        ? <SidebarSection id="folders" title="My folders"
          activeChild={[...folders.filter(folder => folder.name === value).map(folderItem), ...(value === NO_FOLDER ? [noFolderItem] : [])]}>
          {folderItems}
        </SidebarSection>
        : folderItems}
      {children}
    </nav>
  </aside>;
}
