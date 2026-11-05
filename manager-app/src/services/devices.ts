import { AndroidNetwork, Profile } from '../types/profile';
import { AndroidDevice, AndroidUserRow, DeviceListResponse, SetupProgress, SwitchReadiness, TakeOverResult } from '../types/device';
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

export async function restartDevice(serial: string): Promise<void> {
  const res = await fetch(`${devicePath(serial)}/restart`, { method: 'POST' });
  if (!res.ok) throw await apiError(res, 'Failed to restart the phone');
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

/** Shows the account's Android user, matches its proxy timezone and verifies its proxy.
 *  With one account at a time, also stops the phone's other users. Takes up to about two minutes. */
export async function openAccountOnPhone(profileId: string): Promise<OpenAccountResult> {
  const res = await fetch(`${API_BASE}/devices/accounts/${encodeURIComponent(profileId)}/open`, { method: 'POST' });
  if (!res.ok) throw await networkError(res, 'Failed to open the account on the phone');
  return res.json();
}

export async function updateDeviceSettings(serial: string,
  settings: { one_account_at_a_time?: boolean; switch_from_app_only?: boolean; active_hours?: boolean }): Promise<AndroidDevice> {
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

const accountPath = (profileId: string) => `${API_BASE}/devices/accounts/${encodeURIComponent(profileId)}`;

async function post<T>(url: string, body: unknown, fallback: string, method = 'POST'): Promise<T> {
  const res = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await networkError(res, fallback);
  return res.json();
}

/** Creates a new Android user on the phone to hold one account. */
export function createDeviceUser(serial: string, name: string): Promise<AndroidUserRow> {
  return post(`${devicePath(serial)}/users`, { name }, 'Failed to create the Android user');
}

/** Creates this PC's account for an Android user another PC set up, from the phone's details. */
export function adoptDeviceUser(serial: string, userId: number): Promise<Profile> {
  return post(`${devicePath(serial)}/users/${userId}/adopt`, undefined, 'Failed to add the account');
}

export async function fetchAccountSetup(profileId: string): Promise<SetupProgress> {
  const res = await fetch(`${accountPath(profileId)}/setup`);
  if (!res.ok) throw await networkError(res, 'Failed to read the setup');
  return res.json();
}

/** Starts or resumes setup on the phone; it runs in the background. */
export function startAccountSetup(profileId: string): Promise<SetupProgress> {
  return post(`${accountPath(profileId)}/setup`, undefined, 'Failed to start the setup');
}

/** Facebook's first screens (camera roll consent) answered once by taps; nothing is posted. */
export function startFacebookReady(profileId: string): Promise<SetupProgress> {
  return post(`${accountPath(profileId)}/setup/get-ready`, undefined, 'Failed to get Facebook ready');
}

export function confirmAccountLogin(profileId: string): Promise<SetupProgress> {
  return post(`${accountPath(profileId)}/setup/login-done`, undefined, 'Failed to confirm the login');
}

/** Gives a phone account another proxy; the phone keeps it only if traffic gets through. */
export function replaceAccountProxy(profileId: string, proxyId: string): Promise<AndroidNetwork> {
  return post(`${accountPath(profileId)}/proxy`, { proxy_id: proxyId }, 'Failed to change the proxy');
}

export function makeDevicePrimary(serial: string, options: { pin?: string; forgotPin?: boolean }): Promise<TakeOverResult> {
  return post(`${devicePath(serial)}/make-primary`, { pin: options.pin ?? null, forgot_pin: Boolean(options.forgotPin) },
    'Failed to make this PC primary');
}

/** Sets the phone's PIN, or clears it with null. */
export function setDevicePin(serial: string, pin: string | null): Promise<{ pin_set: boolean }> {
  return post(`${devicePath(serial)}/pin`, { pin }, 'Failed to save the PIN', 'PUT');
}
