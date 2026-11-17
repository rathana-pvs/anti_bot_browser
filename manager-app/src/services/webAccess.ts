import { API_BASE, apiError } from './api';

export interface WebAccessStatus {
  enabled: boolean;
  /** Saved as on: it turns back on by itself after the app restarts. */
  remembered: boolean;
  /** Saved as on and still turning back on (for example, waiting for Tailscale). */
  starting: boolean;
  error: string | null;
  url: string | null;
  username: string | null;
  /** Shown only in the app on this PC, never to a web visitor. */
  password: string | null;
  https_port: number;
  web_app_built: boolean;
  opened_from_web: boolean;
}

export async function fetchWebAccess(): Promise<WebAccessStatus> {
  const res = await fetch(`${API_BASE}/system/web-access`, { cache: 'no-store' });
  if (!res.ok) throw await apiError(res, 'Could not read web access status.');
  return res.json();
}

export async function setWebAccess(enabled: boolean): Promise<WebAccessStatus> {
  const res = await fetch(`${API_BASE}/system/web-access`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw await apiError(res, enabled ? 'Web access could not be turned on.' : 'Web access could not be turned off.');
  return res.json();
}

/** Replaces the web password; devices using the old one must sign in again. */
export async function newWebAccessPassword(): Promise<WebAccessStatus> {
  const res = await fetch(`${API_BASE}/system/web-access/password`, { method: 'POST' });
  if (!res.ok) throw await apiError(res, 'A new password could not be made.');
  return res.json();
}
