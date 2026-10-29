import { useCallback, useEffect, useState } from 'react';
import { BatteryCharging, BatteryMedium, Check, ChevronRight, CircleAlert, Copy, Cpu, MemoryStick, Monitor, PencilLine, Plus, RefreshCw, ScanLine, ShieldCheck, Smartphone, Thermometer, Trash2, Usb, UserRound, Wifi } from 'lucide-react';
import { AndroidDevice, AndroidUserRow, DeviceListResponse } from '../../types/device';
import { Profile, SystemStats } from '../../types/profile';
import { ProxyItem } from '../../types/proxy';
import { fetchDevices, forgetDevice, openAccountOnPhone, renameDevice, scanDevice, setDeviceTimezone, switchDeviceUser, updateDeviceSettings } from '../../services/devices';
import { Checkbox } from '../ui/Checkbox';
import { formatUtcOffset, phoneTimeReport } from './phoneTime';
import { API_BASE } from '../../services/api';
import { writeHostClipboardText } from '../../services/clipboard';
import { Alert } from '../ui/Alert';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { EmptyState } from '../ui/EmptyState';
import { IconButton } from '../ui/IconButton';
import { useAppDialog } from '../ui/AppDialogProvider';
import { cn } from '../ui/cn';
import { STATUS_LABEL, STATUS_TONE, isAndroid } from '../accounts/accountModel';

const HOT_C = 40;
/** List keys for the two entries that are not phones. ADB serials never take these values. */
const THIS_PC = 'this-pc';
const ADD_PHONE = 'add-phone';

const STATE_LABEL: Record<string, { label: string; variant: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  device: { label: 'Online', variant: 'success' },
  unauthorized: { label: 'Allow USB debugging', variant: 'warning' },
  offline: { label: 'Offline', variant: 'neutral' },
};

function stateBadge(device: AndroidDevice) {
  const state = STATE_LABEL[device.state] ?? { label: device.state, variant: 'danger' as const };
  return <Badge variant={state.variant}>{state.label}</Badge>;
}

/** The hardware accounts live on: This PC (browser accounts) and physical Android phones. */
export function DevicesPage({ active, profiles, proxies, stats, onImport, onOpenAccount, focus, rescan }: {
  active: boolean; profiles: Profile[]; proxies: ProxyItem[]; stats: SystemStats;
  /** Opens New account for this Android user. */
  onImport: (device: AndroidDevice, user: AndroidUserRow) => void;
  onOpenAccount: (profileId: string) => void;
  /** Device to select when another page links here (null is This PC); a new object re-selects it. */
  focus?: { serial: string | null } | null;
  /** Phone to scan again after one of its users became an account. */
  rescan?: { serial: string } | null;
}) {
  const { showAlert, showConfirm, showPrompt, showToast } = useAppDialog();
  const [data, setData] = useState<DeviceListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<string>(THIS_PC);
  const [scanning, setScanning] = useState<string | null>(null);
  const [switching, setSwitching] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchDevices();
      setData(next);
      setLoadError('');
      setSelected(current => current === THIS_PC || current === ADD_PHONE || next.devices.some(d => d.serial === current) ? current : THIS_PC);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Failed to load phones');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (active) void load(); }, [active, load]);

  useEffect(() => { if (focus) setSelected(focus.serial ?? THIS_PC); }, [focus]);

  const device = data?.devices.find(d => d.serial === selected) ?? null;

  const scan = async (serial: string) => {
    setScanning(serial);
    try {
      const scanned = await scanDevice(serial);
      setData(current => current && { ...current, devices: current.devices.map(d => d.serial === serial ? { ...d, ...scanned } : d) });
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Scan failed', { title: 'Could not scan phone', variant: 'danger' });
    } finally {
      setScanning(null);
    }
  };

  useEffect(() => {
    if (rescan) void scan(rescan.serial).then(load);
    // Only a new rescan request triggers this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rescan]);

  const rename = async (target: AndroidDevice) => {
    const label = await showPrompt('Name shown for this phone.', { title: 'Rename phone', defaultValue: target.label, confirmLabel: 'Rename' });
    if (label === null || !label.trim()) return;
    try {
      await renameDevice(target.serial, label.trim());
      await load();
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Rename failed', { variant: 'danger' });
    }
  };

  const forget = async (target: AndroidDevice) => {
    if (!await showConfirm(`Remove ${target.label} from the list? It reappears when it is connected again.`,
      { title: 'Remove phone', confirmLabel: 'Remove', variant: 'danger' })) return;
    try {
      await forgetDevice(target.serial);
      await load();
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Remove failed', { title: 'Could not remove phone', variant: 'danger' });
    }
  };

  const setTimezone = async (target: AndroidDevice, zone: string) => {
    if (!await showConfirm(`Set ${target.label} to ${zone} (${formatUtcOffset(zone)})? Every Android user on this phone sees the new time at once, and automatic time zone is turned off so Android won’t change it back.`,
      { title: 'Set phone time', confirmLabel: 'Set time' })) return;
    try {
      await setDeviceTimezone(target.serial, zone);
      showToast(`${target.label} now uses ${zone}`, 'success');
      await load();
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Could not set the time zone', { title: 'Phone time not changed', variant: 'danger' });
    }
  };

  const saveSettings = async (target: AndroidDevice, settings: Parameters<typeof updateDeviceSettings>[1]) => {
    try {
      await updateDeviceSettings(target.serial, settings);
      await load();
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Could not save', { title: 'Setting not saved', variant: 'danger' });
    }
  };

  const switchTo = async (user: AndroidUserRow) => {
    if (!device) return;
    setSwitching(user.user_id);
    try {
      // An account's user opens through the safe path: other users stop, time is set, proxy verified.
      if (user.profile_id && device.one_account_at_a_time !== false) {
        const opened = await openAccountOnPhone(user.profile_id);
        if (opened.ready) showToast(`${user.profile_name ?? user.name} is on screen with its proxy verified`, 'success');
        else await showAlert(`${opened.reason ?? 'The phone didn’t confirm it.'} Don’t use Facebook on this account until this is fixed.`,
          { title: `${user.profile_name ?? user.name} isn’t ready`, variant: 'warning' });
        await scan(device.serial);
        await load();
        return;
      }
      const result = await switchDeviceUser(device.serial, user.user_id);
      if (result.ready) showToast(`Switched to ${user.name}`, 'success');
      else await showAlert(`${result.reason ?? 'The phone didn’t confirm the switch.'} Check the phone, then switch again.`,
        { title: `${user.name} isn’t ready yet`, variant: 'warning' });
      await scan(device.serial);
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Switch failed', { title: 'Could not switch user', variant: 'danger' });
    } finally {
      setSwitching(null);
    }
  };

  const phones = data?.devices ?? [];
  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
    <header className="flex items-center gap-3 border-b border-border px-5 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="text-sm font-semibold text-zinc-100">Devices</h1>
        <p className="text-[11px] text-zinc-500">This PC runs browser accounts. Each Android user on a phone can hold one account. An account never moves between devices.</p>
      </div>
      <Button variant="outline" size="sm" onClick={() => void load()} loading={loading}>
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />Refresh
      </Button>
    </header>

    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      {loadError && data && <Alert variant="warning" className="mb-4">
        <p className="font-medium">Couldn’t refresh phones</p>
        <p className="mt-1">Your last loaded phones are still shown. Try refreshing again.</p>
      </Alert>}
      {data && !data.adb_available && <Alert variant="warning" className="mb-4">
        <p className="font-medium">ADB is not available</p>
        <p className="mt-0.5 text-amber-200/80">{data.adb_error} Install Android platform-tools on this computer, then press Refresh.</p>
      </Alert>}

      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="space-y-2" role="list" aria-label="Devices">
          <PcCard profiles={profiles} stats={stats} selected={selected === THIS_PC} onSelect={() => setSelected(THIS_PC)} />
          {phones.map(item => <DeviceCard key={item.serial} device={item} timeIssue={(() => { const report = phoneTimeReport(item, profiles, proxies); return report.mixed || report.mismatched.length > 0; })()} selected={item.serial === selected}
            onSelect={() => setSelected(item.serial)} />)}
          {phones.length === 0 && <button type="button" role="listitem" onClick={() => setSelected(ADD_PHONE)} aria-current={selected === ADD_PHONE ? 'true' : undefined}
            className={cn('flex w-full items-center gap-2 rounded-panel border border-dashed p-3 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
              selected === ADD_PHONE ? 'border-blue-700 bg-blue-950/25 text-zinc-100' : 'border-zinc-700 text-zinc-400 hover:bg-zinc-900')}>
            <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />{loadError && !data ? 'Phones unavailable' : 'Add a phone'}
          </button>}
        </div>

        {selected === THIS_PC && <PcDetail profiles={profiles} stats={stats} onOpenAccount={onOpenAccount} />}
        {selected === ADD_PHONE && (loadError && !data
          ? <PhoneUnavailable error={loadError} loading={loading} onRetry={() => void load()} />
          : <ConnectPhone loading={loading} onCheck={() => void load()} />)}
        {device && <section aria-label={device.label} className="min-w-0 rounded-panel border border-zinc-800 bg-surface/70">
          <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 truncate text-sm font-semibold text-zinc-100">{device.label} {stateBadge(device)}</p>
              <p className="truncate text-[11px] text-zinc-500">
                {[device.manufacturer, device.model, device.android_version && `Android ${device.android_version}`, device.serial].filter(Boolean).join(' · ')}
              </p>
            </div>
            <Button size="sm" onClick={() => void scan(device.serial)} loading={scanning === device.serial} disabled={device.state !== 'device'}>
              <ScanLine className="mr-1.5 h-3.5 w-3.5" />Scan phone
            </Button>
            <IconButton aria-label="Rename phone" onClick={() => void rename(device)}><PencilLine className="h-4 w-4" /></IconButton>
            <IconButton aria-label="Remove phone" onClick={() => void forget(device)}><Trash2 className="h-4 w-4" /></IconButton>
          </div>
          <label className="flex items-start gap-2 px-4 pt-3 text-xs text-zinc-300">
            <Checkbox checked={device.switch_from_app_only !== false} onCheckedChange={(on) => void saveSettings(device, { switch_from_app_only: on })}
              ariaLabel="Switch accounts from this app only" disabled={device.state !== 'device'} className="mt-0.5" />
            <span><span className="font-medium">Switch accounts from this app only</span>
              <span className="block text-[11px] text-zinc-500">Hides the user switcher on the phone, so accounts change only with Open on phone, where the proxy and time are checked first. Signing in still works on the phone after it opens.</span>
              {device.switch_from_app_only !== false && device.health?.user_switcher === true && <span className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-amber-300">
                The phone shows its user switcher again (turned on in Settings).
                <Button size="sm" variant="outline" onClick={() => void saveSettings(device, { switch_from_app_only: true })}>Hide it</Button></span>}</span>
          </label>
          <label className="flex items-start gap-2 px-4 pt-3 text-xs text-zinc-300">
            <Checkbox checked={device.one_account_at_a_time !== false} onCheckedChange={(on) => void saveSettings(device, { one_account_at_a_time: on })} ariaLabel="One account at a time" className="mt-0.5" />
            <span><span className="font-medium">One account at a time</span>
              <span className="block text-[11px] text-zinc-500">Opening an account stops this phone’s other accounts and sets the phone’s time to that account’s proxy, so nothing runs in the background on the wrong time or uses resources. Opening takes up to about two minutes while the proxy is checked.</span></span>
          </label>
          <DeviceWarnings device={device} profiles={profiles} proxies={proxies} onSetTimezone={(zone) => void setTimezone(device, zone)} />
          <UsersTable device={device} switching={switching} onSwitch={switchTo} onImport={(user) => onImport(device, user)} onOpenAccount={onOpenAccount} />
        </section>}
      </div>
    </div>
  </div>;
}

function ConnectPhone({ loading, onCheck }: { loading: boolean; onCheck: () => void }) {
  return <section className="min-w-0 overflow-hidden rounded-panel border border-border bg-surface/70">
    <div className="flex flex-col items-center px-6 py-10 text-center sm:px-10">
      <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-teal-500/20 bg-teal-500/10 text-teal-300">
        <Smartphone className="h-8 w-8" aria-hidden="true" />
      </div>
      <h2 className="text-base font-semibold text-zinc-100">Connect your first phone</h2>
      <p className="mt-2 max-w-sm text-xs leading-5 text-zinc-400">Your Android phones and their accounts will appear here once connected.</p>
      <ol className="my-7 w-full max-w-md space-y-4 text-left">
        {[
          { icon: Usb, title: 'Connect with USB', description: 'Use a USB cable that supports data transfer.' },
          { icon: ShieldCheck, title: 'Allow USB debugging', description: 'Enable USB debugging in Developer options, then unlock the phone and allow this computer.' },
          { icon: RefreshCw, title: 'Refresh your phones', description: 'Check for connected phones when you’re ready.' },
        ].map(({ icon: Icon, title, description }, index) => <li key={title} className="flex gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-elevated text-zinc-400"><Icon className="h-4 w-4" aria-hidden="true" /></span>
          <div><p className="text-xs font-medium text-zinc-200">{index + 1}. {title}</p><p className="mt-1 text-xs leading-5 text-zinc-500">{description}</p></div>
        </li>)}
      </ol>
      <Button onClick={onCheck} loading={loading}><RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} aria-hidden="true" />{loading ? 'Checking for phones…' : 'Check for phones'}</Button>
    </div>
  </section>;
}

function pcFacts(profiles: Profile[], stats: SystemStats) {
  const accounts = profiles.filter(p => !isAndroid(p));
  const runtime = stats.runtime || stats.wsl_runtime;
  return {
    accounts,
    running: accounts.filter(p => p.status === 'running').length,
    cpu: runtime?.cpu_percent ?? stats.cpu_percent,
    usedMb: runtime?.used_memory_mb ?? stats.used_memory_mb,
    totalMb: runtime?.total_memory_mb ?? stats.total_memory_mb,
  };
}

const gb = (mb: number) => (mb / 1024).toFixed(1);

function PcCard({ profiles, stats, selected, onSelect }: { profiles: Profile[]; stats: SystemStats; selected: boolean; onSelect: () => void }) {
  const facts = pcFacts(profiles, stats);
  return <button type="button" role="listitem" onClick={onSelect} aria-current={selected ? 'true' : undefined}
    className={cn('w-full rounded-panel border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
      selected ? 'border-blue-700 bg-blue-950/25' : 'border-zinc-800 bg-surface/70 hover:bg-zinc-900')}>
    <div className="flex items-center gap-2">
      <Monitor className="h-4 w-4 shrink-0 text-violet-300" />
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-100">This PC</span>
      {stats.docker_running ? <Badge variant="success">Online</Badge> : <Badge variant="danger">Docker offline</Badge>}
    </div>
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-400">
      <span>{facts.running} of {facts.accounts.length} running</span>
      {facts.cpu != null && <span className="flex items-center gap-1"><Cpu className="h-3 w-3" />{Math.round(facts.cpu)}%</span>}
      {facts.totalMb > 0 && <span className="flex items-center gap-1"><MemoryStick className="h-3 w-3" />{gb(facts.usedMb)} / {gb(facts.totalMb)} GB</span>}
    </div>
  </button>;
}

function PcDetail({ profiles, stats, onOpenAccount }: { profiles: Profile[]; stats: SystemStats; onOpenAccount: (profileId: string) => void }) {
  const facts = pcFacts(profiles, stats);
  return <section aria-label="This PC" className="min-w-0 rounded-panel border border-zinc-800 bg-surface/70">
    <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 truncate text-sm font-semibold text-zinc-100">This PC {stats.docker_running ? <Badge variant="success">Online</Badge> : <Badge variant="danger">Docker offline</Badge>}</p>
        <p className="truncate text-[11px] text-zinc-500">Each browser account runs in its own container on this computer.</p>
      </div>
    </div>
    <div className="space-y-2 px-4 pt-3">
      <p className="text-[11px] text-zinc-500">{[
        `${facts.running} of ${facts.accounts.length} browsers running`,
        facts.cpu != null && `CPU ${Math.round(facts.cpu)}%`,
        facts.totalMb > 0 && `RAM ${gb(facts.usedMb)} / ${gb(facts.totalMb)} GB`,
      ].filter(Boolean).join(' · ')}</p>
      {!stats.docker_running && <Alert variant="warning">Docker isn’t running, so browser accounts can’t start. Open Setup and repair from Settings.</Alert>}
    </div>
    <div className="p-4">
      {facts.accounts.length === 0
        ? <EmptyState icon={<Monitor className="h-6 w-6" />} title="No browser accounts yet" description="Create an account on This PC from the Accounts page." />
        : <div className="overflow-x-auto rounded-control border border-zinc-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-zinc-900/60 text-[11px] text-zinc-500">
              <tr><th className="px-3 py-2 font-medium">Account</th><th className="px-3 py-2 font-medium">Folder</th><th className="px-3 py-2 font-medium">Status</th></tr>
            </thead>
            <tbody>
              {facts.accounts.map(account => <tr key={account.id} className="border-t border-zinc-800/70">
                <td className="px-3 py-2.5">
                  <button type="button" title="Open this account" className="inline-flex max-w-full items-center gap-1.5 text-blue-300 underline-offset-2 hover:underline" onClick={() => onOpenAccount(account.id)}>
                    <UserRound className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{account.name}</span></button>
                </td>
                <td className="px-3 py-2.5 text-zinc-400">{account.group || 'No folder'}</td>
                <td className={cn('px-3 py-2.5', STATUS_TONE[account.status])}>{STATUS_LABEL[account.status]}</td>
              </tr>)}
            </tbody>
          </table>
        </div>}
    </div>
  </section>;
}

function PhoneUnavailable({ error, loading, onRetry }: { error: string; loading: boolean; onRetry: () => void }) {
  const notFound = error.trim().toLowerCase() === 'not found';
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const [wasLoading, setWasLoading] = useState(loading);
  const [copied, setCopied] = useState(false);
  if (wasLoading !== loading) {
    setWasLoading(loading);
    if (!loading) setCheckedAt(new Date());
  }
  const details = `GET ${API_BASE}/devices\n${error}`;
  const copy = async () => {
    try {
      await writeHostClipboardText(details);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable; details stay visible */ }
  };
  return <section role="alert" className="mx-auto mt-6 max-w-lg overflow-hidden rounded-panel border border-border bg-surface/70 sm:mt-12">
    <div className="flex flex-col items-center px-6 pb-7 pt-8 text-center sm:px-10">
      <div className="relative mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border border-amber-500/20 bg-amber-500/10">
        <Smartphone className="h-7 w-7 text-amber-300/90" aria-hidden="true" />
        <CircleAlert className="absolute -bottom-1.5 -right-1.5 h-5 w-5 rounded-full bg-surface text-amber-400 ring-4 ring-surface" aria-hidden="true" />
      </div>
      <h2 className="text-base font-semibold text-zinc-100">{notFound ? 'Phone support isn’t running' : 'Couldn’t load your phones'}</h2>
      <p className="mt-2 max-w-sm text-xs leading-5 text-zinc-400">{notFound
        ? 'The local service running now is an older version without phone management. Quit and reopen Automat FB to load the updated service.'
        : 'The local service didn’t respond. Make sure Automat FB’s service is running, then try again.'}</p>
      <Button className="mt-6" onClick={onRetry} loading={loading}>
        <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} aria-hidden="true" />{loading ? 'Checking…' : 'Try again'}
      </Button>
      <p className="mt-3 h-4 text-[11px] text-zinc-500" aria-live="polite">
        {checkedAt && !loading && `Still unavailable · checked ${checkedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}
      </p>
    </div>
    <details className="group border-t border-border text-xs text-zinc-500">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-5 py-3 hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />Technical details
      </summary>
      <div className="flex items-start gap-3 px-5 pb-4">
        <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words rounded-control bg-elevated px-3 py-2 font-mono text-[11px] leading-5 text-zinc-400">{details}</pre>
        <IconButton aria-label={copied ? 'Copied' : 'Copy technical details'} onClick={() => void copy()}>
          {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
        </IconButton>
      </div>
    </details>
  </section>;
}

function DeviceCard({ device, timeIssue, selected, onSelect }: { device: AndroidDevice; timeIssue: boolean; selected: boolean; onSelect: () => void }) {
  const battery = device.health?.battery;
  const hot = (battery?.temperature_c ?? 0) >= HOT_C;
  return <button type="button" role="listitem" onClick={onSelect} aria-current={selected ? 'true' : undefined}
    className={cn('w-full rounded-panel border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
      selected ? 'border-blue-700 bg-blue-950/25' : 'border-zinc-800 bg-surface/70 hover:bg-zinc-900')}>
    <div className="flex items-center gap-2">
      <Smartphone className="h-4 w-4 shrink-0 text-zinc-400" />
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-100">{device.label}</span>
      {stateBadge(device)}
    </div>
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-400">
      {device.connection === 'usb' && <span className="flex items-center gap-1"><Usb className="h-3 w-3" />USB</span>}
      {device.connection === 'wifi' && <span className="flex items-center gap-1"><Wifi className="h-3 w-3" />Wi-Fi</span>}
      {battery?.level != null && <span className="flex items-center gap-1">
        {battery.charging ? <BatteryCharging className="h-3 w-3" /> : <BatteryMedium className="h-3 w-3" />}{battery.level}%</span>}
      {battery?.temperature_c != null && <span className={cn('flex items-center gap-1', hot && 'text-amber-300')}>
        <Thermometer className="h-3 w-3" />{battery.temperature_c.toFixed(0)}°C</span>}
      {device.health?.cpu_percent != null && <span className="flex items-center gap-1"><Cpu className="h-3 w-3" />{device.health.cpu_percent}%</span>}
      {device.health?.memory && <span className="flex items-center gap-1"><MemoryStick className="h-3 w-3" />{gb(device.health.memory.used_mb)} / {gb(device.health.memory.total_mb)} GB</span>}
      <span>{device.profile_count ?? 0} {device.profile_count === 1 ? 'account' : 'accounts'}</span>
      {timeIssue && <span className="text-amber-300">Time mismatch</span>}
    </div>
  </button>;
}

function DeviceWarnings({ device, profiles, proxies, onSetTimezone }: {
  device: AndroidDevice; profiles: Profile[]; proxies: ProxyItem[]; onSetTimezone: (zone: string) => void;
}) {
  const health = device.health;
  const time = phoneTimeReport(device, profiles, proxies);
  const describe = (entries: typeof time.accounts) => entries.map(entry => `${entry.profile.name} (${entry.zone}, ${formatUtcOffset(entry.zone)})`).join(', ');
  const warnings: string[] = [];
  if (device.state === 'unauthorized') warnings.push('Unlock the phone and accept the "Allow USB debugging" prompt for this computer.');
  if (device.state === 'offline') warnings.push('This phone is not connected. Check the cable or Wi-Fi debugging.');
  if ((health?.battery?.temperature_c ?? 0) >= HOT_C) warnings.push(`The phone is hot (${health!.battery!.temperature_c!.toFixed(0)}°C). Let it cool before running jobs.`);
  if (health?.free_storage_mb != null && health.free_storage_mb < 1024) warnings.push(`Only ${health.free_storage_mb} MB of storage is free.`);
  if ((device.profile_count ?? 0) > 1) warnings.push(`${device.profile_count} accounts share this phone's hardware and network.`);
  const facts = [
    time.zone && `Time ${time.zone} (${formatUtcOffset(time.zone)}${time.auto ? ', automatic' : ''})`,
    health?.cpu_percent != null && `CPU ${health.cpu_percent}%`,
    health?.memory && `RAM ${gb(health.memory.used_mb)} / ${gb(health.memory.total_mb)} GB`,
    health?.free_storage_mb != null && (health.storage_total_mb
      ? `Storage ${gb(health.free_storage_mb)} GB free of ${gb(health.storage_total_mb)} GB`
      : `${gb(health.free_storage_mb)} GB free`),
    health?.facebook_version && `Facebook ${health.facebook_version}`,
    health?.max_users != null && `Up to ${health.max_users} Android users`,
  ].filter(Boolean);
  return <div className="space-y-2 px-4 pt-3">
    {facts.length > 0 && <p className="text-[11px] text-zinc-500">{facts.join(' · ')}</p>}
    {warnings.map(text => <Alert key={text} variant="warning">{text}</Alert>)}
    {time.mixed
      ? <Alert variant="warning">Accounts on this phone need different times: {describe(time.accounts)}. A phone has one time zone for all its accounts, so use proxies on the same time for accounts that share a phone.</Alert>
      : time.suggested && <Alert variant="warning">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1">{time.mismatched.length > 0
            ? `The phone is on ${time.zone} (${formatUtcOffset(time.zone!)}), but its accounts’ proxies are on ${describe(time.mismatched)}.`
            : 'Automatic time zone is on, so Android can change the phone’s time away from its accounts’ proxies.'}</span>
          <Button size="sm" variant="outline" onClick={() => onSetTimezone(time.suggested!)}>
            {time.mismatched.length > 0 ? `Set phone time to ${time.suggested}` : `Lock time to ${time.suggested}`}</Button>
        </div>
      </Alert>}
  </div>;
}

function UsersTable({ device, switching, onSwitch, onImport, onOpenAccount }: {
  device: AndroidDevice; switching: number | null; onSwitch: (user: AndroidUserRow) => void;
  onImport: (user: AndroidUserRow) => void; onOpenAccount: (profileId: string) => void;
}) {
  const scan = device.last_scan;
  if (!scan) {
    return <div className="p-4"><EmptyState icon={<ScanLine className="h-6 w-6" />} title="Not scanned yet"
      description={device.state === 'device' ? 'Scan the phone to list its Android users.' : 'Connect the phone, then scan it to list its Android users.'} /></div>;
  }
  const online = device.state === 'device';
  return <div className="p-4">
    <div className="overflow-x-auto rounded-control border border-zinc-800">
      <table className="w-full min-w-[36rem] text-left text-xs">
        <thead className="bg-zinc-900/60 text-[11px] text-zinc-500">
          <tr><th className="px-3 py-2 font-medium">Android user</th><th className="px-3 py-2 font-medium">Facebook</th>
            <th className="px-3 py-2 font-medium">Network</th><th className="px-3 py-2 font-medium">Linked account</th><th className="px-3 py-2" /></tr>
        </thead>
        <tbody>
          {scan.users.map(user => <tr key={user.user_id} className="border-t border-zinc-800/70">
            <td className="px-3 py-2.5">
              <p className="font-medium text-zinc-100">{user.name}</p>
              <p className="text-[11px] text-zinc-500">User {user.user_id}{user.current ? ' · on screen' : user.running ? ' · running' : ''}</p>
            </td>
            <td className="px-3 py-2.5 text-zinc-300">
              {user.control_user ? <span className="text-zinc-500">—</span>
                : user.facebook_installed == null ? <span className="text-zinc-500">Unknown</span>
                  : user.facebook_installed ? 'Installed' : <span className="text-amber-300">Not installed</span>}
            </td>
            <td className="px-3 py-2.5">
              {user.control_user ? <span className="text-zinc-500">Control user</span> : <span className="text-zinc-400">Not set up</span>}
            </td>
            <td className="px-3 py-2.5">
              {user.control_user ? <span className="text-zinc-500">Not used for accounts</span>
                : user.profile_id ? <button type="button" title="Open this account" className="inline-flex max-w-full items-center gap-1.5 text-blue-300 underline-offset-2 hover:underline" onClick={() => onOpenAccount(user.profile_id!)}>
                  <UserRound className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{user.profile_name}</span></button>
                  : <Button size="sm" variant="outline" onClick={() => onImport(user)}>Create account</Button>}
            </td>
            <td className="px-3 py-2.5 text-right">
              {!user.current && <Button size="sm" variant="ghost" disabled={!online || switching !== null} loading={switching === user.user_id}
                onClick={() => onSwitch(user)}>Show on screen</Button>}
            </td>
          </tr>)}
        </tbody>
      </table>
    </div>
    <p className="mt-2 text-[11px] text-zinc-500">
      Scanned {new Date(scan.scanned_at).toLocaleString()}. A phone account’s proxy is reserved now and used once the companion app is set up. Automation for phone accounts isn’t available yet.
    </p>
  </div>;
}
