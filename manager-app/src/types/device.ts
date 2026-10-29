export type AdbState = 'device' | 'unauthorized' | 'offline' | 'no permissions' | string;

export interface DeviceHealth {
  battery: { level: number | null; temperature_c: number | null; charging: boolean } | null;
  free_storage_mb: number | null;
  storage_total_mb?: number | null;
  /** Share of all cores in use, 0-100. */
  cpu_percent?: number | null;
  memory?: { used_mb: number; total_mb: number } | null;
  current_user: number | null;
  max_users: number | null;
  facebook_version: string | null;
}

export interface AndroidUserRow {
  user_id: number;
  name: string;
  user_serial: number | null;
  running: boolean;
  current: boolean;
  control_user: boolean;
  facebook_installed: boolean | null;
  profile_id: string | null;
  profile_name: string | null;
}

export interface SwitchReadiness {
  current_user: number | null;
  running: boolean;
  screen_awake: boolean | null;
  lock_screen_showing: boolean | null;
  ready: boolean;
  reason: string | null;
}

export interface DeviceScan {
  scanned_at: string;
  current_user: number | null;
  users: AndroidUserRow[];
}

export interface AndroidDevice {
  serial: string;
  label: string;
  state: AdbState;
  connection: 'usb' | 'wifi' | 'unknown' | null;
  model?: string | null;
  manufacturer?: string | null;
  android_version?: string | null;
  sdk?: number | null;
  first_seen?: string;
  last_seen?: string;
  last_scan?: DeviceScan;
  profile_count?: number;
  health?: DeviceHealth | null;
}

export interface DeviceListResponse {
  adb_available: boolean;
  adb_error: string | null;
  devices: AndroidDevice[];
}

export interface AndroidRuntime {
  kind: 'android_user';
  device_serial: string;
  android_user_id: number;
  android_user_serial: number | null;
  android_user_name: string | null;
}
