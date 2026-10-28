import { useCallback, useEffect, useState } from 'react';
import { BatteryCharging, BatteryMedium, PencilLine, RefreshCw, ScanLine, Smartphone, Thermometer, Trash2, Usb, Wifi } from 'lucide-react';
import { AndroidDevice, AndroidUserRow, DeviceListResponse } from '../../types/device';
import { fetchDevices, forgetDevice, importDeviceUser, renameDevice, scanDevice, switchDeviceUser } from '../../services/devices';
import { Alert } from '../ui/Alert';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Dialog } from '../ui/Dialog';
import { EmptyState } from '../ui/EmptyState';
import { Field } from '../ui/Field';
import { IconButton } from '../ui/IconButton';
import { Input } from '../ui/Input';
import { useAppDialog } from '../ui/AppDialogProvider';
import { ProfileGroupField } from '../ProfileGroupField';
import { cn } from '../ui/cn';

const HOT_C = 40;

const STATE_LABEL: Record<string, { label: string; variant: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  device: { label: 'Online', variant: 'success' },
  unauthorized: { label: 'Allow USB debugging', variant: 'warning' },
  offline: { label: 'Offline', variant: 'neutral' },
};

function stateBadge(device: AndroidDevice) {
  const state = STATE_LABEL[device.state] ?? { label: device.state, variant: 'danger' as const };
  return <Badge variant={state.variant}>{state.label}</Badge>;
}

/** Physical Android phones: inventory, health, and import of Android users as accounts. */
export function DevicesPage({ active, groups, onProfilesChanged, onOpenAccount }: {
  active: boolean; groups: string[]; onProfilesChanged: () => void; onOpenAccount: (profileId: string) => void;
}) {
  const { showAlert, showConfirm, showPrompt, showToast } = useAppDialog();
  const [data, setData] = useState<DeviceListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [scanning, setScanning] = useState<string | null>(null);
  const [switching, setSwitching] = useState<number | null>(null);
  const [importing, setImporting] = useState<AndroidUserRow | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await fetchDevices();
      setData(next);
      setLoadError('');
      setSelected(current => current && next.devices.some(d => d.serial === current) ? current : next.devices[0]?.serial ?? null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Failed to load phones');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (active) void load(); }, [active, load]);

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

  const switchTo = async (user: AndroidUserRow) => {
    if (!device) return;
    setSwitching(user.user_id);
    try {
      const result = await switchDeviceUser(device.serial, user.user_id);
      showToast(result.current_user === user.user_id ? `Switched to ${user.name}` : 'Switch requested; the phone has not confirmed it yet', result.current_user === user.user_id ? 'success' : 'warning');
      await scan(device.serial);
    } catch (error) {
      await showAlert(error instanceof Error ? error.message : 'Switch failed', { title: 'Could not switch user', variant: 'danger' });
    } finally {
      setSwitching(null);
    }
  };

  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
    <header className="flex items-center gap-3 border-b border-border px-5 py-3">
      <div className="min-w-0 flex-1">
        <h1 className="text-sm font-semibold text-zinc-100">Phones</h1>
        <p className="text-[11px] text-zinc-500">Each Android user on a phone can hold one Facebook account.</p>
      </div>
      <Button variant="outline" size="sm" onClick={() => void load()} loading={loading}>
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />Refresh
      </Button>
    </header>

    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      {loadError && <Alert variant="danger" className="mb-4">{loadError}</Alert>}
      {data && !data.adb_available && <Alert variant="warning" className="mb-4">
        <p className="font-medium">ADB is not available</p>
        <p className="mt-0.5 text-amber-200/80">{data.adb_error} Install Android platform-tools on this computer, then press Refresh.</p>
      </Alert>}

      {data && data.devices.length === 0 && <EmptyState icon={<Smartphone className="h-8 w-8" />} title="Add a phone"
        description="1. Connect the phone with a USB cable.  2. Turn on USB debugging in Developer options and allow this computer.  3. Press Refresh."
        action={<Button size="sm" onClick={() => void load()} loading={loading}>Refresh</Button>} />}

      {data && data.devices.length > 0 && <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="space-y-2" role="list" aria-label="Phones">
          {data.devices.map(item => <DeviceCard key={item.serial} device={item} selected={item.serial === selected}
            onSelect={() => setSelected(item.serial)} />)}
        </div>
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
          <DeviceWarnings device={device} />
          <UsersTable device={device} switching={switching} onSwitch={switchTo} onImport={setImporting} onOpenAccount={onOpenAccount} />
        </section>}
      </div>}
    </div>

    {device && importing && <ImportDialog device={device} user={importing} groups={groups} onClose={() => setImporting(null)}
      onImported={async (name) => {
        setImporting(null);
        showToast(`${name} added to Accounts`, 'success');
        onProfilesChanged();
        await scan(device.serial);
        await load();
      }} />}
  </div>;
}

function DeviceCard({ device, selected, onSelect }: { device: AndroidDevice; selected: boolean; onSelect: () => void }) {
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
      <span>{device.profile_count ?? 0} {device.profile_count === 1 ? 'account' : 'accounts'}</span>
    </div>
  </button>;
}

function DeviceWarnings({ device }: { device: AndroidDevice }) {
  const health = device.health;
  const warnings: string[] = [];
  if (device.state === 'unauthorized') warnings.push('Unlock the phone and accept the "Allow USB debugging" prompt for this computer.');
  if (device.state === 'offline') warnings.push('This phone is not connected. Check the cable or Wi-Fi debugging.');
  if ((health?.battery?.temperature_c ?? 0) >= HOT_C) warnings.push(`The phone is hot (${health!.battery!.temperature_c!.toFixed(0)}°C). Let it cool before running jobs.`);
  if (health?.free_storage_mb != null && health.free_storage_mb < 1024) warnings.push(`Only ${health.free_storage_mb} MB of storage is free.`);
  if ((device.profile_count ?? 0) > 1) warnings.push(`${device.profile_count} accounts share this phone's hardware and network.`);
  const facts = [
    health?.facebook_version && `Facebook ${health.facebook_version}`,
    health?.max_users != null && `Up to ${health.max_users} Android users`,
    health?.free_storage_mb != null && `${(health.free_storage_mb / 1024).toFixed(1)} GB free`,
  ].filter(Boolean);
  return <div className="space-y-2 px-4 pt-3">
    {facts.length > 0 && <p className="text-[11px] text-zinc-500">{facts.join(' · ')}</p>}
    {warnings.map(text => <Alert key={text} variant="warning">{text}</Alert>)}
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
            <th className="px-3 py-2 font-medium">Network</th><th className="px-3 py-2 font-medium">Account</th><th className="px-3 py-2" /></tr>
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
                : user.profile_id ? <button type="button" className="text-blue-300 underline-offset-2 hover:underline" onClick={() => onOpenAccount(user.profile_id!)}>{user.profile_name}</button>
                  : <Button size="sm" variant="outline" onClick={() => onImport(user)}>Import</Button>}
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
      Scanned {new Date(scan.scanned_at).toLocaleString()}. Proxy setup and automation for phone accounts are not available yet.
    </p>
  </div>;
}

function ImportDialog({ device, user, groups, onClose, onImported }: {
  device: AndroidDevice; user: AndroidUserRow; groups: string[]; onClose: () => void; onImported: (name: string) => void;
}) {
  const [name, setName] = useState(user.name);
  const [group, setGroup] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    if (!name.trim()) { setError('Enter an account name'); return; }
    setSaving(true);
    try {
      const profile = await importDeviceUser(device.serial, user.user_id, name.trim(), group);
      onImported(profile.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
      setSaving(false);
    }
  };
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title="Import Android user"
    description={`${user.name} (user ${user.user_id}) on ${device.label} becomes an account. Log in to Facebook on the phone yourself.`}
    footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button onClick={() => void submit()} loading={saving}>Import</Button></>}>
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <Field label="Account name" htmlFor="import-account-name" error={error || undefined}>
        <Input id="import-account-name" value={name} maxLength={120} autoFocus onChange={(e) => { setName(e.target.value); setError(''); }} />
      </Field>
      <ProfileGroupField value={group} onChange={setGroup} groups={groups} />
      {user.facebook_installed === false && <Alert variant="warning">Facebook is not installed for this Android user yet.</Alert>}
    </form>
  </Dialog>;
}
