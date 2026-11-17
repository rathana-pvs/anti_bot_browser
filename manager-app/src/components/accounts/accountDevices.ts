import { Profile } from '../../types/profile';
import { AndroidDevice } from '../../types/device';
import { isAndroid, phoneOf } from './accountModel';

/** The key of This PC in device filters; a phone's key is its serial. */
export const PC_DEVICE = 'pc';

export interface DeviceEntry {
  key: string;
  label: string;
  kind: 'pc' | 'phone';
  /** null when the phone isn't connected to this PC now (or was never seen). */
  online: boolean | null;
  connection: AndroidDevice['connection'];
  count: number;
}

/** Which device an account lives on: This PC, or its phone's serial (aliases resolved). */
export function deviceKeyOf(profile: Profile, devices: AndroidDevice[]): string {
  if (!isAndroid(profile) || !profile.runtime) return PC_DEVICE;
  return phoneOf(profile, devices)?.serial ?? profile.runtime.device_serial;
}

/** This PC, then every phone, each with its number of accounts. A phone that holds accounts but
 * isn't in the device list is still shown, so its accounts can be found. */
export function deviceEntries(profiles: Profile[], devices: AndroidDevice[]): DeviceEntry[] {
  const counts = new Map<string, number>();
  profiles.forEach(profile => {
    const key = deviceKeyOf(profile, devices);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  const phones: DeviceEntry[] = devices.map(device => ({
    key: device.serial, label: device.label || device.serial, kind: 'phone' as const,
    online: device.state === 'device', connection: device.connection, count: counts.get(device.serial) ?? 0,
  }));
  const known = new Set(phones.map(phone => phone.key));
  counts.forEach((count, key) => {
    if (key !== PC_DEVICE && !known.has(key)) phones.push({ key, label: key, kind: 'phone', online: null, connection: null, count });
  });
  phones.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  return [{ key: PC_DEVICE, label: 'This PC', kind: 'pc', online: true, connection: null, count: counts.get(PC_DEVICE) ?? 0 }, ...phones];
}

export function deviceSummary(entry: DeviceEntry): string {
  const accounts = `${entry.count} account${entry.count === 1 ? '' : 's'}`;
  if (entry.kind === 'pc') return accounts;
  const state = entry.online ? (entry.connection === 'usb' ? 'USB' : entry.connection === 'wifi' ? 'Wi-Fi' : 'Online')
    : entry.online === false ? 'Offline' : 'Not connected';
  return `${state} · ${accounts}`;
}
