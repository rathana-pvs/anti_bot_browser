import { API_BASE } from './api';

export interface LiveSettings {
  server_url: string;
  producer_url: string;
  key_saved: boolean;
}
export interface LiveSession {
  profile_id: string;
  status: 'idle' | 'running' | 'completed' | 'failed' | 'stopped' | 'needs_review';
  stage: string;
  queue_execution_id?: string;
  filename?: string;
  duration_seconds?: number;
  elapsed_seconds?: number;
  broadcast_confirmed?: boolean;
  error?: string | null;
}

async function request<T>(profileId: string, action: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}/profiles/${encodeURIComponent(profileId)}/live/${action}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 404 && action === 'settings' && (!result.detail || result.detail === 'Not Found')) {
      throw new Error('The connected backend does not provide Live settings. Restart or update the backend, then retry.');
    }
    throw new Error(typeof result.detail === 'string' ? result.detail : 'Live request failed');
  }
  return result;
}
export const fetchLiveSettings = (id: string) => request<LiveSettings>(id, 'settings');
export const saveLiveSettings = (id: string, value: {server_url: string; producer_url: string; stream_key: string}) => request<LiveSettings>(id, 'settings', 'PUT', value);
export const fetchLiveStatus = (id: string) => request<LiveSession>(id, 'status');
export const startLive = (id: string, filename: string, title: string, caption: string, pinned_comment = '') => request<LiveSession>(id, 'start', 'POST', {filename, title, caption, pinned_comment});
export const stopLive = (id: string, executionId?: string) => request(id, executionId ? `stop?execution_id=${encodeURIComponent(executionId)}` : 'stop', 'POST');
export const reviewLive = (id: string) => request<LiveSession>(id, 'review', 'POST', {confirmed_ended: true});
