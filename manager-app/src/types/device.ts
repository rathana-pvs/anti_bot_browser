export type AdbState = 'device' | 'unauthorized' | 'offline' | 'no permissions' | string;

export interface DeviceHealth {
  battery: { level: number | null; temperature_c: number | null; charging: boolean } | null;
  free_storage_mb: number | null;
  storage_total_mb?: number | null;
  /** Share of all cores in use, 0-100. */
  cpu_percent?: number | null;
  memory?: { used_mb: number; total_mb: number } | null;
  /** One timezone for the whole phone; `auto` is Android's automatic detection. */
  timezone?: { zone: string | null; auto: boolean | null } | null;
  /** Whether the phone shows its own user switcher (Quick Settings, lock screen). */
  user_switcher?: boolean | null;
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
  /** The companion app in this user; null when it is not installed. */
  agent?: AgentInfo | null;
}

/** What a user's companion app reports. The proxy's password never leaves the phone this way. */
export interface AgentInfo {
  readable: boolean;
  reason?: string;
  version?: string | null;
  account?: { profile_id: string | null; name: string | null; group: string | null; exit_ip: string | null; timezone: string | null };
  pin_set?: boolean;
  proxy?: { host: string; port: number; username: string } | null;
}

/** primary: this PC set the phone up and may change it. secondary: another PC manages it. */
export type PhoneRole = 'primary' | 'secondary' | 'unclaimed';

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
  aliases?: string[];
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
  /** Opening an account stops the phone's other users and sets its time to that account's proxy. */
  one_account_at_a_time?: boolean;
  /** The phone's own user switcher is hidden, so accounts change only through this app. */
  switch_from_app_only?: boolean;
  /** Scheduled posts wait for each account's active hours; off, they start at their scheduled time. */
  active_hours?: boolean;
  health?: DeviceHealth | null;
  role?: PhoneRole;
  /** Name of the PC that manages the phone, when known. */
  primary_name?: string | null;
  /** This PC keeps a PIN for the phone, needed by another PC to take it over. */
  pin_set?: boolean;
}

export type SetupStepStatus = 'pending' | 'running' | 'passed' | 'failed';

export interface SetupProgress {
  profile_id: string;
  state: 'not_started' | 'running' | 'failed' | 'waiting_login' | 'done';
  steps: { key: string; label: string; status: SetupStepStatus; reason: string | null }[];
  updated_at: string | null;
}

export interface TakeOverResult {
  role: PhoneRole;
  accounts: { profile_id: string; user_id: number; status: 'ok' | 'needs_proxy'; reason: string | null }[];
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
