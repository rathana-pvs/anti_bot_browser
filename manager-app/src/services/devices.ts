import { Profile } from '../types/profile';
import { AndroidDevice, DeviceListResponse, SwitchReadiness } from '../types/device';
import { API_BASE, apiError } from './api';

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

export async function switchDeviceUser(serial: string, userId: number): Promise<SwitchReadiness> {
  const res = await fetch(`${devicePath(serial)}/switch-user`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to switch Android user');
  return res.json();
}

export async function importDeviceUser(serial: string, userId: number, name: string, group: string): Promise<Profile> {
  const res = await fetch(`${devicePath(serial)}/users/${userId}/import`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, group }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to import the Android user');
  return res.json();
}
