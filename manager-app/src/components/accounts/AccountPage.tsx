import { useState } from 'react';
import { AlertTriangle, ArrowLeft, Pause, Play, Square } from 'lucide-react';
import { Profile, ProfileUpdateRequest } from '../../types/profile';
import { ProxyItem } from '../../types/proxy';
import { QueueExecutionItem } from '../../types/automation';
import { VncViewer } from '../VncViewer';
import { MediaLightbox, LightboxMedia } from '../MediaLightbox';
import { ActionMenu } from '../ui/ActionMenu';
import { Button } from '../ui/Button';
import { useAppDialog } from '../ui/AppDialogProvider';
import { cn } from '../ui/cn';
import { AccountHandlers, accountMenu } from './accountActions';
import { AccountTab, STATUS_LABEL, STATUS_TONE, activityFor, isAndroid } from './accountModel';
import { AccountOverview } from './AccountOverview';
import { AccountActivityTab } from './AccountActivityTab';
import { ProfileSettings } from './ProfileSettings';

const TABS: { value: AccountTab; label: string }[] = [
  { value: 'browser', label: 'Browser' },
  { value: 'overview', label: 'Overview' },
  { value: 'settings', label: 'Settings' },
  { value: 'activity', label: 'Activity' },
];

export function AccountPage({ profile, tab: requestedTab, onTabChange, onBack, handlers, busy, executions, proxies, groups, onSave, onOpenQueue }: {
  profile: Profile; tab: AccountTab; onTabChange: (tab: AccountTab) => void; onBack: () => void;
  handlers: AccountHandlers; busy: boolean; executions: QueueExecutionItem[];
  proxies: ProxyItem[]; groups: string[];
  onSave: (profileId: string, updates: ProfileUpdateRequest) => Promise<void>;
  onOpenQueue: () => void;
}) {
  const { showToast } = useAppDialog();
  const [lightbox, setLightbox] = useState<LightboxMedia | null>(null);
  const activity = activityFor(profile.id, executions);
  const run = (action: 'start' | 'stop' | 'pause' | 'unpause') => handlers.run([profile], action);
  const android = isAndroid(profile);
  // Phone accounts have no browser or editable runtime settings yet.
  const tabs = android ? TABS.filter(option => option.value === 'overview' || option.value === 'activity') : TABS;
  const tab = tabs.some(option => option.value === requestedTab) ? requestedTab : 'overview';

  return <div className="flex min-h-0 flex-1 flex-col">
    <header className="shrink-0 border-b border-border bg-surface/50 px-6 pt-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" onClick={onBack} className="-ml-2"><ArrowLeft className="h-3.5 w-3.5" />Accounts</Button>
        <h1 className="min-w-0 truncate text-sm font-semibold text-zinc-100">{profile.name}</h1>
        <span className={cn('flex items-center gap-1.5 text-xs', android ? 'text-zinc-400' : STATUS_TONE[profile.status])}><span className="h-1.5 w-1.5 rounded-full bg-current" />{android ? 'On phone' : STATUS_LABEL[profile.status]}</span>
        {profile.group && <span className="text-xs text-zinc-500">{profile.group}</span>}
        <div className="ml-auto flex items-center gap-2">
          {android ? null : profile.status === 'stopped' || profile.status === 'error'
            ? <Button size="sm" loading={busy} onClick={() => run('start')}><Play className="h-3 w-3" />Start</Button>
            : <>
              {profile.status === 'paused'
                ? <Button size="sm" variant="outline" loading={busy} onClick={() => run('unpause')}><Play className="h-3 w-3" />Resume</Button>
                : <Button size="sm" variant="outline" loading={busy} onClick={() => run('pause')} title="Pause keeps memory but uses no CPU"><Pause className="h-3 w-3" />Pause</Button>}
              <Button size="sm" variant="outline" disabled={busy} onClick={() => run('stop')}><Square className="h-3 w-3" />Stop</Button>
            </>}
          <ActionMenu ariaLabel={`More actions for ${profile.name}`}
            items={accountMenu(profile, handlers, { includeOpen: false }).filter(item => !['Start', 'Stop', 'Pause', 'Resume', 'Edit settings'].includes(item.label))} />
        </div>
      </div>
      <nav role="tablist" aria-label="Account sections" className="mt-2 flex gap-1">
        {tabs.map(option => <button key={option.value} type="button" role="tab" aria-selected={tab === option.value} onClick={() => onTabChange(option.value)}
          className={cn('border-b-2 px-3 pb-2.5 pt-1 text-xs font-semibold transition-colors',
            tab === option.value ? 'border-blue-500 text-white' : 'border-transparent text-zinc-400 hover:text-zinc-200')}>
          {option.label}{option.value === 'overview' && activity.attention.length > 0 && <span className="ml-1.5 rounded-full bg-amber-500/20 px-1.5 text-[10px] text-amber-300">{activity.attention.length}</span>}
        </button>)}
      </nav>
    </header>

    {profile.restart_required && <div className="mx-6 mt-3 flex shrink-0 items-center gap-2 rounded-xl border border-amber-800/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span className="flex-1">Saved settings aren’t active yet. {profile.status === 'running' ? 'Stop and start' : 'Start'} this account to apply them.</span>
    </div>}

    {tab === 'browser'
      ? <div className="flex min-h-0 flex-1"><VncViewer profile={profile} onAction={(_id, action) => run(action)} embedded /></div>
      : <div className="min-h-0 flex-1 overflow-y-auto p-6">
        {tab === 'overview' && <AccountOverview profile={profile} proxies={proxies} activity={activity} onOpenQueue={onOpenQueue} />}
        {tab === 'settings' && <ProfileSettings profile={profile} onSave={onSave} proxies={proxies} groups={groups} />}
        {tab === 'activity' && <AccountActivityTab profile={profile} recent={activity.recent}
          onOpenImage={(url, label) => setLightbox({ url, type: 'photo', name: label })} onToast={showToast} />}
      </div>}
    <MediaLightbox media={lightbox} onClose={() => setLightbox(null)} />
  </div>;
}
