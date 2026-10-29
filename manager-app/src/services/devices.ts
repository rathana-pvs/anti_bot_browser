import { AndroidNetwork, Profile } from '../types/profile';
import { AndroidDevice, DeviceListResponse, SwitchReadiness } from '../types/device';
import { API_BASE, apiError, backendWebSocketUrl } from './api';

const devicePath = (serial: string) => `${API_BASE}/devices/${encodeURIComponent(serial)}`;

export async function fetchDevices(): Promise<DeviceListResponse> {
  const res = await fetch(`${API_BASE}/devices`);
  if (!res.ok) throw await apiError(res, 'Failed to load phones');
  return res.json();
}

export async function scanDevice(serial: string): Promise<AndroidDevice> {
  const res = await fetch(`${devicePath(serial)}/scan`, { method: 'POST' });
  if (!res.ok) throw await apiError(res, 'Failed to scan the phone');
  return res.json();
}

export async function renameDevice(serial: string, label: string): Promise<AndroidDevice> {
  const res = await fetch(devicePath(serial), {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to rename the phone');
  return res.json();
}

export async function forgetDevice(serial: string): Promise<void> {
  const res = await fetch(devicePath(serial), { method: 'DELETE' });
  if (!res.ok) throw await apiError(res, 'Failed to remove the phone');
}

export async function setDeviceTimezone(serial: string, timezone: string): Promise<{ zone: string | null; auto: boolean | null }> {
  const res = await fetch(`${devicePath(serial)}/timezone`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ timezone }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to set the phone’s time zone');
  return res.json();
}

export interface AccountNetworkStatus {
  state: AndroidNetwork['status'];
  reason: string | null;
  agent: { version: string | null; tunnel_up: boolean | null; proxy: string | null; always_on: boolean | null; lockdown: boolean | null } | null;
}

/** A route-level 404 means the running service predates this screen, not that the phone lacks something. */
async function networkError(res: Response, fallback: string): Promise<Error> {
  const error = await apiError(res, fallback);
  return res.status === 404 && error.message === 'Not Found'
    ? new Error('The app’s background service is older than this screen. Quit and reopen the app, then try again.')
    : error;
}

/** Quick read of the companion app in the account's Android user; nothing is saved. */
export async function fetchAccountNetwork(profileId: string): Promise<AccountNetworkStatus> {
  const res = await fetch(`${API_BASE}/devices/accounts/${encodeURIComponent(profileId)}/network`);
  if (!res.ok) throw await networkError(res, 'Failed to read the phone’s network');
  return res.json();
}

/** Checks from inside the Android user that traffic leaves through the proxy, and saves the result. */
export async function verifyAccountNetwork(profileId: string): Promise<AndroidNetwork> {
  const res = await fetch(`${API_BASE}/devices/accounts/${encodeURIComponent(profileId)}/network/verify`, { method: 'POST' });
  if (!res.ok) throw await networkError(res, 'Failed to verify the phone’s network');
  return res.json();
}

export interface OpenAccountResult {
  ready: boolean;
  reason: string | null;
  stopped_users: number[];
  timezone_set: string | null;
  network: AndroidNetwork | { status: 'no_proxy'; reason: null } | null;
}

/** Shows the account's Android user and verifies its proxy; with one account at a time, also stops the
 *  phone's other users and sets the phone's time. Takes up to about two minutes. */
export async function openAccountOnPhone(profileId: string): Promise<OpenAccountResult> {
  const res = await fetch(`${API_BASE}/devices/accounts/${encodeURIComponent(profileId)}/open`, { method: 'POST' });
  if (!res.ok) throw await networkError(res, 'Failed to open the account on the phone');
  return res.json();
}

export async function updateDeviceSettings(serial: string,
  settings: { one_account_at_a_time?: boolean; switch_from_app_only?: boolean }): Promise<AndroidDevice> {
  const res = await fetch(`${devicePath(serial)}/settings`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
  });
  if (!res.ok) throw await networkError(res, 'Failed to save the phone’s settings');
  return res.json();
}

export async function switchDeviceUser(serial: string, userId: number): Promise<SwitchReadiness> {
  const res = await fetch(`${devicePath(serial)}/switch-user`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to switch Android user');
  return res.json();
}

export async function importDeviceUser(serial: string, userId: number, name: string, group: string, proxyId: string | null = null): Promise<Profile> {
  const res = await fetch(`${devicePath(serial)}/users/${userId}/import`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, group, proxy_id: proxyId }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to import the Android user');
  return res.json();
}

export function deviceLiveUrl(serial: string, userId: number): string {
  return backendWebSocketUrl(`/devices/${encodeURIComponent(serial)}/live`, { user_id: String(userId) });
}
