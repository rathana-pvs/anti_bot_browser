import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Monitor, PanelRightClose, Pin, PinOff, Smartphone } from 'lucide-react';
import { executeProfileAction, fetchProfiles } from './services/api';
import { setVisibleInterval } from './services/visibleInterval';
import type { Profile } from './types/profile';
import { VncViewer } from './components/VncViewer';
import { PhoneLiveView } from './components/devices/PhoneLiveView';
import { useDevices } from './components/devices/useDevices';
import { PhoneDrop, PhoneFilesDialog } from './components/accounts/PhoneFilesDialog';
import { STATUS_LABEL, STATUS_TONE, isAndroid, isOnScreen, phoneStatus } from './components/accounts/accountModel';
import { IconButton } from './components/ui/IconButton';
import { Spinner } from './components/ui/Spinner';
import { cn } from './components/ui/cn';
import { useAppDialog } from './components/ui/AppDialogProvider';

const PROFILE_POLL_MS = 5000;

/**
 * The page of an account's pop-out live window (Tauri label `live-<profile id>`): only that
 * account's screen. Start, stop and the rest stay in the main window.
 */
export function LiveWindowApp({ profileId }: { profileId: string }) {
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);
  const [onTop, setOnTop] = useState(false);
  const [phoneFiles, setPhoneFiles] = useState<{ files?: PhoneDrop[] } | null>(null);
  const { devices } = useDevices(10000);
  const { showAlert } = useAppDialog();
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = () => fetchProfiles()
      .then(list => { if (!cancelled) setProfile(list.find(item => item.id === profileId) ?? null); })
      .catch(() => undefined);
    load();
    const stop = setVisibleInterval(load, PROFILE_POLL_MS);
    return () => { cancelled = true; stop(); };
  }, [profileId, reloadKey]);

  // The screen's own Start/Resume buttons (shown over a stopped or paused browser).
  const runAction = (id: string, action: 'start' | 'stop' | 'pause' | 'unpause') => {
    executeProfileAction(id, action)
      .then(() => setReloadKey(key => key + 1))
      .catch(error => void showAlert(error instanceof Error ? error.message : String(error), { title: 'Couldn’t do that', variant: 'danger' }));
  };

  const toggleOnTop = () => {
    const next = !onTop;
    invoke('set_window_on_top', { onTop: next }).then(() => setOnTop(next)).catch(() => undefined);
  };
  const dock = () => void invoke('dock_live_window').catch(() => undefined);

  if (profile === undefined) return <div className="flex h-screen items-center justify-center bg-background"><Spinner /></div>;
  if (profile === null) return <div className="flex h-screen flex-col items-center justify-center gap-2 bg-background p-6 text-center text-sm text-muted">
    <p className="font-semibold text-foreground">This account no longer exists</p>
    <p>It was deleted or moved. Close this window.</p>
  </div>;

  const android = isAndroid(profile);
  const phone = android ? phoneStatus(profile, devices) : null;
  const onScreen = android && isOnScreen(profile, devices);

  return <div onContextMenu={event => event.preventDefault()} className="flex h-screen flex-col overflow-hidden bg-background font-sans text-zinc-100">
    <header className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-surface pl-3 pr-1 text-xs">
      {android ? <Smartphone aria-hidden="true" className="h-4 w-4 text-muted" /> : <Monitor aria-hidden="true" className="h-4 w-4 text-muted" />}
      <span className="min-w-0 truncate font-semibold">{profile.name}</span>
      <span className={cn('flex shrink-0 items-center gap-1.5', phone ? phone.tone : STATUS_TONE[profile.status])}>
        <span className="h-1.5 w-1.5 rounded-full bg-current" />{phone ? phone.label : STATUS_LABEL[profile.status]}
      </span>
      <div className="flex-1" />
      <IconButton aria-label={onTop ? 'Stop keeping on top' : 'Keep on top'} title={onTop ? 'Stop keeping on top' : 'Keep on top'}
        className="h-8 w-8" onClick={toggleOnTop}>
        {onTop ? <Pin className="h-4 w-4" /> : <PinOff className="h-4 w-4" />}
      </IconButton>
      <IconButton aria-label="Dock back into the app" title="Dock back into the app" className="h-8 w-8" onClick={dock}>
        <PanelRightClose className="h-4 w-4" />
      </IconButton>
    </header>

    <div className="flex min-h-0 flex-1">
      {android && profile.runtime
        // Starts by itself only when the account is already on the phone's screen, so opening
        // this window never switches the phone away from another account.
        ? <PhoneLiveView key={profile.id} runtime={profile.runtime} onScreen={onScreen} autoStart={onScreen}
          onSendFiles={files => setPhoneFiles({ files })} />
        : <VncViewer profile={profile} onAction={runAction} embedded />}
    </div>

    {phoneFiles && <PhoneFilesDialog profile={profile} initialFiles={phoneFiles.files} onClose={() => setPhoneFiles(null)} />}
  </div>;
}
