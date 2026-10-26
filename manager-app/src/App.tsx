import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Profile, ProfileCreateRequest, ProfileUpdateRequest, SystemStats } from './types/profile';
import { ProxyItem } from './types/proxy';
import {
  fetchProfiles,
  probeBackendConnection,
  fetchProfileGroups,
  createProfileGroup,
  renameProfileGroup,
  deleteProfileGroup,
  createProfile,
  updateProfile,
  executeProfileAction,
  deleteProfile,
  deleteExecution,
  fetchSystemStats,
  fetchProxies,
} from './services/api';
import { writeHostClipboardText } from './services/clipboard';
import { waitForBackend } from './services/backendStartup';
import { BackendConnectionScreen } from './components/BackendConnectionScreen';
import { ProxyPanel } from './components/ProxyPanel';
import { CampaignsPanel } from './components/CampaignsPanel';
import { CreateProfileModal } from './components/CreateProfileModal';
import { TelemetryFooter } from './components/TelemetryFooter';
import { ProfileActionErrorModal } from './components/ProfileActionErrorModal';
import { SetupCenter } from './components/SetupCenter';
import { SettingsModal } from './components/SettingsModal';
import { NavRail, AppPage } from './components/NavRail';
import { AccountsPage } from './components/accounts/AccountsPage';
import { AccountPage } from './components/accounts/AccountPage';
import { MoveToGroupDialog } from './components/accounts/MoveToGroupDialog';
import { AccountHandlers, FolderHandlers, ProfileAction } from './components/accounts/accountActions';
import { FolderNameDialog, FolderDialogState } from './components/accounts/FolderNameDialog';
import { AccountTab, clearableFailures, useQueueExecutions } from './components/accounts/accountModel';
import { fetchSetupStatus, isDesktopApp, SetupSnapshot } from './services/setup';
import { useAppDialog } from './components/ui/AppDialogProvider';

interface ProfileActionError {
  profileId: string;
  profileName: string;
  action: ProfileAction;
  reason: string;
}

const ACTION_VERB: Record<ProfileAction, string> = { start: 'Started', stop: 'Stopped', pause: 'Paused', unpause: 'Resumed' };

/** Text fields keep the normal copy/paste menu; everything else uses the app's own menus or none. */
const allowsNativeMenu = (target: EventTarget | null) =>
  target instanceof HTMLElement && Boolean(target.closest('input, textarea, [contenteditable="true"]'));

export const App: React.FC = () => {
  const { showAlert, showConfirm, showToast } = useAppDialog();
  const [setupStatus, setSetupStatus] = useState<SetupSnapshot | null | undefined>(
    () => isDesktopApp() ? undefined : null,
  );
  const [backendReady, setBackendReady] = useState(false);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [statsLoaded, setStatsLoaded] = useState(false);
  const [profileGroups, setProfileGroups] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [proxies, setProxies] = useState<ProxyItem[]>([]);
  const [page, setPage] = useState<AppPage>('accounts');
  const [openAccount, setOpenAccount] = useState<{ id: string; tab: AccountTab } | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [moveTargets, setMoveTargets] = useState<Profile[] | null>(null);
  const [folderDialog, setFolderDialog] = useState<FolderDialogState>(null);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [campaignAttention, setCampaignAttention] = useState(0);
  const [queueRequest, setQueueRequest] = useState<{ profileId?: string; key: number } | null>(null);
  const [profileActionError, setProfileActionError] = useState<ProfileActionError | null>(null);

  const [systemStats, setSystemStats] = useState<SystemStats>({
    docker_running: false,
    active_profiles: 0,
    total_profiles: 0,
    used_memory_mb: 0,
    total_memory_mb: 16384,
    available_proxies: 0,
  });

  const loadData = async () => {
    const [profilesResult, statsResult, proxiesResult, groupsResult] = await Promise.allSettled([
      fetchProfiles(),
      fetchSystemStats(),
      fetchProxies(),
      fetchProfileGroups(),
    ]);

    if (profilesResult.status === 'fulfilled') {
      const profilesData = profilesResult.value;
      setProfiles(profilesData);
      // Leave an account page whose account was deleted elsewhere.
      setOpenAccount((current) => current && profilesData.some((p) => p.id === current.id) ? current : null);
    } else {
      console.error('Failed to load profiles:', profilesResult.reason);
    }

    if (statsResult.status === 'fulfilled') {
      setSystemStats(statsResult.value);
      setStatsLoaded(true);
    } else {
      console.error('Failed to load system stats:', statsResult.reason);
    }
    if (proxiesResult.status === 'fulfilled') {
      setProxies(proxiesResult.value);
    } else {
      console.error('Failed to load proxies:', proxiesResult.reason);
    }
    if (groupsResult.status === 'fulfilled') {
      setProfileGroups(groupsResult.value);
    } else {
      console.error('Failed to load profile groups:', groupsResult.reason);
    }
  };

  const isModalOpenRef = React.useRef(false);
  isModalOpenRef.current = isCreateModalOpen;

  useEffect(() => {
    if (!isDesktopApp()) return;
    fetchSetupStatus()
      .then((status) => {
        setSetupStatus(status.ready ? null : status);
      })
      .catch((error) => {
        console.error('Failed to inspect desktop setup:', error);
        setSetupStatus(null);
      });
  }, []);

  useEffect(() => {
    if (setupStatus !== null) return;
    const controller = new AbortController();
    setBackendReady(false);
    setBackendError(null);
    void waitForBackend(probeBackendConnection, { signal: controller.signal })
      .then(async () => {
        if (controller.signal.aborted) return;
        await loadData();
        if (!controller.signal.aborted) setBackendReady(true);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setBackendError(error.message);
      });
    return () => controller.abort();
  }, [setupStatus, connectionAttempt]);

  useEffect(() => {
    if (setupStatus !== null || !backendReady) return;
    let loading = false;
    const interval = setInterval(() => {
      if (!isModalOpenRef.current && !loading) {
        loading = true;
        void loadData().finally(() => { loading = false; });
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [setupStatus, backendReady]);

  const { executions, reload: reloadExecutions } = useQueueExecutions(backendReady && page === 'accounts');

  const openAccountPage = useCallback((profileId: string, tab: AccountTab = 'overview') => {
    setPage('accounts');
    setOpenAccount({ id: profileId, tab });
  }, []);

  const runProfiles = async (targets: Profile[], action: ProfileAction) => {
    if (!targets.length) return;
    setBusyIds((current) => new Set([...current, ...targets.map((p) => p.id)]));
    setProfileActionError(null);
    const failures: ProfileActionError[] = [];
    // One at a time, so local resource admission sees each start in order.
    for (const profile of targets) {
      try {
        await executeProfileAction(profile.id, action);
      } catch (err: any) {
        failures.push({ profileId: profile.id, profileName: profile.name, action, reason: err?.message || `Couldn’t ${action} this account` });
      } finally {
        setBusyIds((current) => { const next = new Set(current); next.delete(profile.id); return next; });
      }
    }
    await loadData();
    if (targets.length === 1 && failures.length === 1) {
      setProfileActionError(failures[0]);
    } else if (failures.length) {
      showToast(`${ACTION_VERB[action]} ${targets.length - failures.length} of ${targets.length}. ${failures.map((f) => `${f.profileName}: ${f.reason}`).join(' · ')}`, 'error');
    } else if (targets.length > 1) {
      showToast(`${ACTION_VERB[action]} ${targets.length} accounts.`, 'success');
    }
  };

  const removeProfiles = async (targets: Profile[]) => {
    if (!targets.length) return;
    const confirmed = await showConfirm(
      targets.length === 1
        ? `Delete “${targets[0].name}”? Its browser data, cookies, and saved sessions are deleted too. This can’t be undone.`
        : `Delete ${targets.length} accounts? Their browser data, cookies, and saved sessions are deleted too. This can’t be undone.`,
      { title: targets.length === 1 ? 'Delete account' : 'Delete accounts', confirmLabel: 'Delete', variant: 'danger' },
    );
    if (!confirmed) return;
    const failures: string[] = [];
    const deleted = new Set<string>();
    for (const profile of targets) {
      try { await deleteProfile(profile.id, true); deleted.add(profile.id); }
      catch (err: any) { failures.push(`${profile.name}: ${err?.message || 'failed'}`); }
    }
    setOpenAccount((current) => current && deleted.has(current.id) ? null : current);
    await loadData();
    if (failures.length) await showAlert(`Couldn’t delete: ${failures.join(' · ')}`, { title: 'Delete failed', variant: 'danger' });
  };

  const moveProfiles = async (targets: Profile[], group: string, isNew: boolean) => {
    try {
      if (isNew && !profileGroups.includes(group)) await createProfileGroup(group);
      for (const profile of targets) await updateProfile(profile.id, { group });
      const who = targets.length === 1 ? targets[0].name : `${targets.length} accounts`;
      showToast(group ? `Moved ${who} to ${group}.` : `Moved ${who} out of their folder.`, 'success');
    } catch (err: any) {
      showToast(err?.message || 'Couldn’t move these accounts.', 'error');
      throw err;
    } finally {
      await loadData();
    }
  };

  const openQueue = (profileId?: string) => {
    setPage('campaigns');
    setQueueRequest({ profileId, key: Date.now() });
  };

  const clearFailures = async (profile: Profile, before?: number) => {
    const items = clearableFailures(profile.id, executions, before);
    const when = before === undefined ? '' : new Date(before).toDateString() === new Date().toDateString()
      ? ' from before today' : ` from before ${new Date(before).toLocaleDateString()}`;
    if (!items.length) {
      showToast(`${profile.name} has no failed items${when} to clear.`, 'info');
      return;
    }
    const confirmed = await showConfirm(
      `Remove ${items.length} failed item${items.length === 1 ? '' : 's'}${when} for ${profile.name}? These stopped before posting, so nothing went out. Unconfirmed posts are kept.`,
      { title: 'Clear old failures', confirmLabel: 'Remove', variant: 'danger' },
    );
    if (!confirmed) return;
    let removed = 0;
    for (const item of items) {
      try { await deleteExecution(item.execution_id); removed += 1; } catch (_) { /* reported below */ }
    }
    reloadExecutions();
    showToast(removed === items.length ? `Removed ${removed} failed item${removed === 1 ? '' : 's'}.` : `Removed ${removed} of ${items.length}. Some couldn’t be removed.`,
      removed === items.length ? 'success' : 'error');
  };

  const handlers: AccountHandlers = {
    openQueue,
    clearFailures: (profile, before) => { void clearFailures(profile, before); },
    open: openAccountPage,
    run: (targets, action) => { void runProfiles(targets, action); },
    moveToGroup: setMoveTargets,
    remove: (targets) => { void removeProfiles(targets); },
    copyId: (profile) => {
      writeHostClipboardText(profile.id)
        .then(() => showToast('Account ID copied.', 'success'))
        .catch(() => showToast('Clipboard access was blocked.', 'error'));
    },
  };

  const submitFolderName = async (name: string) => {
    if (!folderDialog) return;
    try {
      if (folderDialog.mode === 'create') {
        await createProfileGroup(name);
        showToast(`Created folder ${name}.`, 'success');
      } else {
        await renameProfileGroup(folderDialog.name, name);
        showToast(`Renamed folder to ${name}.`, 'success');
      }
    } finally {
      await loadData();
    }
  };

  const deleteFolder = async (name: string) => {
    const count = profiles.filter((p) => p.group === name).length;
    const confirmed = await showConfirm(
      count
        ? `Delete the folder “${name}”? Its ${count} account${count === 1 ? ' is' : 's are'} kept and moved to No folder.`
        : `Delete the empty folder “${name}”?`,
      { title: 'Delete folder', confirmLabel: 'Delete folder', variant: 'danger' },
    );
    if (!confirmed) return;
    try {
      await deleteProfileGroup(name);
      showToast(`Deleted folder ${name}.`, 'success');
    } catch (err: any) {
      showToast(err?.message || 'Couldn’t delete this folder.', 'error');
    } finally {
      await loadData();
    }
  };

  const folderHandlers: FolderHandlers = {
    create: () => setFolderDialog({ mode: 'create' }),
    rename: (name) => setFolderDialog({ mode: 'rename', name }),
    remove: (name) => { void deleteFolder(name); },
    // Errors are already shown as a toast by moveProfiles.
    moveTo: (targets, folder) => { void moveProfiles(targets, folder, false).catch(() => undefined); },
  };

  const handleCreate = async (profileData: ProfileCreateRequest) => {
    try {
      const newProfile = await createProfile(profileData);
      await loadData();
      openAccountPage(newProfile.id, 'overview');
    } catch (err: any) {
      await showAlert(`Couldn’t create the account: ${err.message}`, { title: 'Account not created', variant: 'danger' });
    }
  };

  // Rethrows so the settings form can show the error next to its Save button.
  const handleUpdate = async (profileId: string, updates: ProfileUpdateRequest) => {
    await updateProfile(profileId, updates);
    await loadData();
  };

  const accountsAttention = useMemo(() => profiles.filter((p) => p.status === 'error').length, [profiles]);
  const currentAccount = openAccount ? profiles.find((p) => p.id === openAccount.id) || null : null;

  if (setupStatus === undefined) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#08090c] text-zinc-400">
        <div className="flex items-center gap-3 text-sm">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-700 border-t-blue-500" />
          Checking this computer…
        </div>
      </div>
    );
  }

  if (setupStatus !== null) {
    return (
      <SetupCenter
        initialStatus={setupStatus}
        onComplete={() => {
          setSetupStatus(null);
        }}
      />
    );
  }

  if (!backendReady) {
    return <BackendConnectionScreen error={backendError}
      onRetry={() => setConnectionAttempt(attempt => attempt + 1)}
      onOpenSetup={isDesktopApp() ? () => {
        void fetchSetupStatus().then(status => setSetupStatus(status))
          .catch(() => setBackendError('Could not open Setup and repair. Reopen the app to try again.'));
      } : undefined} />;
  }

  return (
    <div
      onContextMenu={(e) => { if (!allowsNativeMenu(e.target)) e.preventDefault(); }}
      className="w-screen h-screen flex flex-col bg-background text-zinc-100 overflow-hidden font-sans"
    >
      <div className="flex-1 flex overflow-hidden">
        <NavRail page={page} onPageChange={(next) => { setPage(next); if (next === 'accounts' && page === 'accounts') setOpenAccount(null); }}
          onOpenSettings={() => setIsSettingsOpen(true)}
          badges={{ accounts: accountsAttention, campaigns: campaignAttention }} />

        <main className={`flex-1 flex min-w-0 overflow-hidden ${page === 'accounts' ? '' : 'hidden'}`}>
          {currentAccount && openAccount
            ? <AccountPage key={currentAccount.id} profile={currentAccount} tab={openAccount.tab}
              onTabChange={(tab) => setOpenAccount({ id: currentAccount.id, tab })}
              onBack={() => setOpenAccount(null)} handlers={handlers} busy={busyIds.has(currentAccount.id)}
              executions={executions} proxies={proxies} groups={profileGroups} onSave={handleUpdate}
              onOpenQueue={() => openQueue(currentAccount.id)} />
            : <AccountsPage profiles={profiles} proxies={proxies} groups={profileGroups} executions={executions} handlers={handlers} folderHandlers={folderHandlers}
              busyIds={busyIds} onCreate={() => setIsCreateModalOpen(true)} />}
        </main>

        <main className={`flex-1 flex min-w-0 overflow-hidden ${page === 'campaigns' ? '' : 'hidden'}`}>
          <CampaignsPanel
            profiles={profiles}
            queueRequest={queueRequest}
            onAttentionChange={setCampaignAttention}
            onEditProfile={(profileId) => openAccountPage(profileId, 'settings')}
            onSelectProfile={(profileId) => openAccountPage(profileId, 'browser')}
            onOpenAccount={(profileId) => openAccountPage(profileId, 'overview')}
          />
        </main>

        <main className={`flex-1 flex min-w-0 overflow-hidden ${page === 'proxies' ? '' : 'hidden'}`}>
          <ProxyPanel
            proxies={proxies}
            onRefresh={loadData}
            onSelectProfile={(profileId) => openAccountPage(profileId, 'settings')}
          />
        </main>
      </div>

      <TelemetryFooter stats={systemStats} loading={!statsLoaded} />

      <CreateProfileModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreate={handleCreate}
        existingCount={profiles.length}
        proxies={proxies}
        groups={profileGroups}
      />

      <FolderNameDialog state={folderDialog} onSubmit={submitFolderName} onClose={() => setFolderDialog(null)} />

      <MoveToGroupDialog profiles={moveTargets} groups={profileGroups} onMove={moveProfiles} onClose={() => setMoveTargets(null)} />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onOpenSetup={isDesktopApp() ? () => {
          void fetchSetupStatus().then(status => {
            setIsSettingsOpen(false);
            setSetupStatus(status);
          }).catch(error => window.alert(`Could not open setup: ${String(error)}`));
        } : undefined}
      />

      {profileActionError && (
        <ProfileActionErrorModal
          {...profileActionError}
          onClose={() => setProfileActionError(null)}
        />
      )}
    </div>
  );
};

export default App;
