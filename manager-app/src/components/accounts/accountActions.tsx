import { Copy, Eye, Eraser, FolderInput, FolderMinus, FolderPlus, ListChecks, Pause, PencilLine, Play, Settings2, Square, Trash2, ExternalLink } from 'lucide-react';
import { Profile } from '../../types/profile';
import type { ActionMenuItem } from '../ui/ActionMenu';
import { isAndroid, type AccountTab } from './accountModel';

export type ProfileAction = 'start' | 'stop' | 'pause' | 'unpause';

export interface AccountHandlers {
  open: (profileId: string, tab?: AccountTab) => void;
  run: (profiles: Profile[], action: ProfileAction) => void;
  moveToGroup: (profiles: Profile[]) => void;
  remove: (profiles: Profile[]) => void;
  copyId: (profile: Profile) => void;
  /** Opens the Queue showing only this account's items. */
  openQueue: (profileId: string) => void;
  /** Removes failed-before-posting items older than `before` (all of them when omitted). */
  clearFailures: (profile: Profile, before?: number) => void;
}

export interface FolderHandlers {
  create: () => void;
  rename: (folder: string) => void;
  remove: (folder: string) => void;
  /** Direct move, used by drag and drop. `folder` is '' for no folder. */
  moveTo: (profiles: Profile[], folder: string) => void;
}

const icon = 'h-3.5 w-3.5';

/** The same list backs the ⋯ button and the right-click menu, so they never drift apart. */
export function accountMenu(profile: Profile, handlers: AccountHandlers, { includeOpen = true, failureCutoff }: { includeOpen?: boolean; failureCutoff?: number } = {}): ActionMenuItem[] {
  const items: ActionMenuItem[] = [];
  if (isAndroid(profile)) {
    if (includeOpen) items.push({ label: 'Open', icon: <ExternalLink className={icon} />, onSelect: () => handlers.open(profile.id, 'overview') });
    items.push(
      { label: 'Show in queue', icon: <ListChecks className={icon} />, onSelect: () => handlers.openQueue(profile.id), divider: includeOpen },
      { label: 'Move to folder…', icon: <FolderInput className={icon} />, onSelect: () => handlers.moveToGroup([profile]) },
      { label: 'Copy account ID', icon: <Copy className={icon} />, onSelect: () => handlers.copyId(profile) },
      { label: 'Delete', icon: <Trash2 className={icon} />, danger: true, divider: true, onSelect: () => handlers.remove([profile]) },
    );
    return items;
  }
  if (includeOpen) {
    items.push(
      { label: 'Open', icon: <ExternalLink className={icon} />, onSelect: () => handlers.open(profile.id, 'overview') },
      { label: 'Open browser', icon: <Eye className={icon} />, onSelect: () => handlers.open(profile.id, 'browser') },
    );
  }
  if (profile.status === 'stopped' || profile.status === 'error') {
    items.push({ label: 'Start', icon: <Play className={icon} />, onSelect: () => handlers.run([profile], 'start'), divider: includeOpen });
  } else {
    items.push(profile.status === 'paused'
      ? { label: 'Resume', icon: <Play className={icon} />, onSelect: () => handlers.run([profile], 'unpause'), divider: includeOpen }
      : { label: 'Pause', icon: <Pause className={icon} />, onSelect: () => handlers.run([profile], 'pause'), divider: includeOpen });
    items.push({ label: 'Stop', icon: <Square className={icon} />, onSelect: () => handlers.run([profile], 'stop') });
  }
  items.push(
    { label: 'Show in queue', icon: <ListChecks className={icon} />, onSelect: () => handlers.openQueue(profile.id), divider: true },
    { label: 'Clear old failures…', icon: <Eraser className={icon} />, hint: 'Removes failed-before-posting items. Unconfirmed posts are never removed.', onSelect: () => handlers.clearFailures(profile, failureCutoff) },
    { label: 'Edit settings', icon: <Settings2 className={icon} />, onSelect: () => handlers.open(profile.id, 'settings'), divider: true },
    { label: 'Move to folder…', icon: <FolderInput className={icon} />, onSelect: () => handlers.moveToGroup([profile]) },
    { label: 'Copy account ID', icon: <Copy className={icon} />, onSelect: () => handlers.copyId(profile) },
    { label: 'Delete', icon: <Trash2 className={icon} />, danger: true, divider: true, onSelect: () => handlers.remove([profile]) },
  );
  return items;
}

const countLabel = (verb: string, count: number) => count ? `${verb} ${count} account${count === 1 ? '' : 's'}` : `${verb} accounts`;

export function bulkMenu(profiles: Profile[], handlers: AccountHandlers): ActionMenuItem[] {
  const count = profiles.length;
  const startable = profiles.filter(p => !isAndroid(p) && (p.status === 'stopped' || p.status === 'error'));
  const stoppable = profiles.filter(p => !isAndroid(p) && (p.status === 'running' || p.status === 'paused'));
  return [
    { label: countLabel('Start', startable.length), icon: <Play className={icon} />, disabled: !startable.length, onSelect: () => handlers.run(startable, 'start') },
    { label: countLabel('Stop', stoppable.length), icon: <Square className={icon} />, disabled: !stoppable.length, onSelect: () => handlers.run(stoppable, 'stop') },
    { label: `Move ${count} to folder…`, icon: <FolderInput className={icon} />, divider: true, onSelect: () => handlers.moveToGroup(profiles) },
    { label: countLabel('Delete', count), icon: <Trash2 className={icon} />, danger: true, divider: true, onSelect: () => handlers.remove(profiles) },
  ];
}

/** Right-click menu for a folder in the folder column. */
export function folderMenu(folder: string, members: Profile[], handlers: AccountHandlers, folders: FolderHandlers): ActionMenuItem[] {
  const startable = members.filter(p => !isAndroid(p) && (p.status === 'stopped' || p.status === 'error'));
  const stoppable = members.filter(p => !isAndroid(p) && (p.status === 'running' || p.status === 'paused'));
  return [
    { label: countLabel('Start', startable.length), icon: <Play className={icon} />, disabled: !startable.length, onSelect: () => handlers.run(startable, 'start') },
    { label: countLabel('Stop', stoppable.length), icon: <Square className={icon} />, disabled: !stoppable.length, onSelect: () => handlers.run(stoppable, 'stop') },
    { label: 'Rename folder…', icon: <PencilLine className={icon} />, divider: true, onSelect: () => folders.rename(folder) },
    { label: 'New folder…', icon: <FolderPlus className={icon} />, onSelect: folders.create },
    { label: 'Delete folder…', icon: <FolderMinus className={icon} />, danger: true, divider: true, hint: 'Accounts are kept and moved to No folder.', onSelect: () => folders.remove(folder) },
  ];
}
