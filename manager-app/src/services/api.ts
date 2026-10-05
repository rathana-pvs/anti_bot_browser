import {
  NetworkIntent, Profile, ProfileCreateRequest, ProfileDefaults, ProfileUpdateRequest, SystemStats,
} from '../types/profile';
import { ProxyItem } from '../types/proxy';
import { BrainActionResponse, BrainCatalogResponse, BrainUploadResponse } from '../types/brain';
import { invoke } from '@tauri-apps/api/core';
import { isDesktopApp } from './desktopEnvironment';

export let BACKEND_BASE = '';
export let API_BASE = '/api';
let backendToken = '';
let authenticatedFetchInstalled = false;

interface ApiSessionInfo {
  baseUrl: string;
  token: string;
}

export async function initializeBackendSession(): Promise<void> {
  if (!isDesktopApp()) return;

  const session = await invoke<ApiSessionInfo>('get_api_session');
  BACKEND_BASE = session.baseUrl;
  API_BASE = `${BACKEND_BASE}/api`;
  backendToken = session.token;

  if (authenticatedFetchInstalled) return;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const target = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
    if (!target.startsWith(BACKEND_BASE)) return nativeFetch(input, init);

    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    if (init?.headers) {
      new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    }
    headers.set('X-Manager-Token', backendToken);
    return nativeFetch(input, { ...init, headers });
  };
  authenticatedFetchInstalled = true;
}

export function getSharedMediaUrl(filename?: string | null): string {
  if (!filename) return '';
  if (filename.startsWith('http://') || filename.startsWith('https://')) return filename;
  const cleanPath = filename.startsWith('/') ? filename : `/shared_media/${filename}`;
  const url = `${BACKEND_BASE}${cleanPath}`;
  if (!backendToken) return url;
  return `${url}?access_token=${encodeURIComponent(backendToken)}`;
}

export async function fetchProfiles(): Promise<Profile[]> {
  const res = await fetch(`${API_BASE}/profiles`);
  if (!res.ok) throw new Error('Failed to fetch profiles');
  return res.json();
}

async function apiError(res: Response, fallback: string): Promise<Error> {
  try {
    const text = await res.text();
    try {
      const payload = JSON.parse(text);
      const detail =
        typeof payload.detail === 'string'
          ? payload.detail
          : payload.detail?.error || payload.message || payload.error;
      return new Error(detail || fallback);
    } catch {
      return new Error(text && text.length < 200 ? text : `${fallback} (${res.status} ${res.statusText || 'Error'})`);
    }
  } catch {
    return new Error(fallback);
  }
}

export async function fetchProfileGroups(): Promise<string[]> {
  const res = await fetch(`${API_BASE}/profiles/groups`);
  if (!res.ok) throw await apiError(res, 'Failed to fetch profile groups');
  return res.json();
}

export async function createProfileGroup(name: string): Promise<{ name: string }> {
  const res = await fetch(`${API_BASE}/profiles/groups`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to create profile group');
  return res.json();
}

export interface TimezoneDetection {
  detected: boolean;
  timezone: string;
  source: 'host' | 'proxy';
  timezone_options: string[];
}

export async function detectNetworkTimezone(network: NetworkIntent): Promise<TimezoneDetection> {
  if (network.mode === 'direct') {
    const defaults = await fetchProfileDefaults();
    return {
      // Older backends cannot distinguish detection from a guessed default.
      // Require confirmation rather than silently trusting that timezone.
      detected: defaults.host_timezone_detected === true,
      timezone: defaults.host_timezone_detected === true
        ? defaults.default_environment.timezone!
        : 'America/Guatemala',
      source: 'host',
      timezone_options: defaults.timezone_options || FALLBACK_TIMEZONE_OPTIONS,
    };
  }
  const res = await fetch(`${API_BASE}/profiles/detect-timezone`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(network),
  });
  if (res.status === 404 || res.status === 405) {
    throw new Error('The running backend does not support proxy timezone detection. Restart the manager to load the updated backend, then try again.');
  }
  if (!res.ok) throw await apiError(res, 'Could not detect timezone');
  return res.json();
}

// Compatibility for a frontend loaded while the previous backend is still running.
const FALLBACK_TIMEZONE_OPTIONS = [
  'America/Guatemala', 'Asia/Phnom_Penh',
  'America/New_York', 'America/Detroit', 'America/Kentucky/Louisville',
  'America/Kentucky/Monticello', 'America/Indiana/Indianapolis',
  'America/Indiana/Vincennes', 'America/Indiana/Winamac', 'America/Indiana/Marengo',
  'America/Indiana/Petersburg', 'America/Indiana/Vevay', 'America/Chicago',
  'America/Indiana/Tell_City', 'America/Indiana/Knox', 'America/Menominee',
  'America/North_Dakota/Center', 'America/North_Dakota/New_Salem',
  'America/North_Dakota/Beulah', 'America/Denver', 'America/Boise', 'America/Phoenix',
  'America/Los_Angeles', 'America/Anchorage', 'America/Juneau', 'America/Sitka',
  'America/Metlakatla', 'America/Yakutat', 'America/Nome', 'America/Adak',
  'Pacific/Honolulu', 'America/Puerto_Rico', 'America/St_Thomas', 'Pacific/Guam',
  'Pacific/Saipan', 'Pacific/Pago_Pago', 'Pacific/Midway', 'Pacific/Wake',
];

export async function fetchProfileDefaults(): Promise<ProfileDefaults> {
  const res = await fetch(`${API_BASE}/profiles/defaults`);
  if (!res.ok) throw await apiError(res, 'Failed to fetch profile defaults');
  return res.json();
}

export async function createProfile(profileData: ProfileCreateRequest): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profiles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profileData),
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to create profile');
  }
  return res.json();
}

export async function updateProfile(
  profileId: string,
  updates: ProfileUpdateRequest
): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profiles/${profileId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to update profile');
  }
  return res.json();
}

export async function executeProfileAction(
  profileId: string,
  action: 'start' | 'stop' | 'pause' | 'unpause'
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/profiles/${profileId}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  if (!res.ok) {
    throw await apiError(res, `Failed to ${action} profile`);
  }
  return res.json();
}

export async function pasteToProfile(
  profileId: string,
  text: string,
  mode: 'both' | 'type' | 'clipboard' | 'paste' = 'paste'
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/profiles/${profileId}/paste`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, mode }),
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to send text to profile');
  }
  return res.json();
}

export async function deleteProfile(
  profileId: string,
  deleteData: boolean = false
): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/profiles/${profileId}?deleteData=${deleteData}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete profile');
  return res.json();
}

export async function fetchSystemStats(): Promise<SystemStats> {
  const res = await fetch(`${API_BASE}/system/stats`);
  if (!res.ok) throw new Error('Failed to fetch system stats');
  return res.json();
}

// Workflow Brain API Functions
export async function fetchBrains(): Promise<BrainCatalogResponse> {
  const res = await fetch(`${API_BASE}/brains`);
  if (!res.ok) throw new Error('Failed to fetch workflow Brains');
  return res.json();
}

export async function validateBrain(brainId: string, version?: string): Promise<BrainActionResponse> {
  const res = await fetch(`${API_BASE}/brains/${encodeURIComponent(brainId)}/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.error || 'Brain validation failed');
  }
  return res.json();
}

export async function activateBrain(brainId: string, version: string): Promise<BrainActionResponse> {
  const res = await fetch(`${API_BASE}/brains/${encodeURIComponent(brainId)}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.error || 'Brain activation failed');
  }
  return res.json();
}

export async function rollbackBrain(brainId: string): Promise<BrainActionResponse> {
  const res = await fetch(`${API_BASE}/brains/${encodeURIComponent(brainId)}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.error || 'Brain rollback failed');
  }
  return res.json();
}

export async function uploadBrainPackage(file: File, activate = false): Promise<BrainUploadResponse> {
  const body = new FormData();
  body.append('package', file);
  body.append('activate', String(activate));
  const res = await fetch(`${API_BASE}/brains/upload`, {
    method: 'POST',
    body,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.error || 'Brain package upload failed');
  }
  return res.json();
}

// Proxy API Functions
export async function fetchProxies(): Promise<ProxyItem[]> {
  const res = await fetch(`${API_BASE}/proxies`);
  if (!res.ok) throw new Error('Failed to fetch proxies');
  return res.json();
}

export async function importProxies(text: string): Promise<{ added: number; total: number; status_checked: number; online: number; offline: number; geo_checked: number; geo_failed: number }> {
  const res = await fetch(`${API_BASE}/proxies/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error('Failed to import proxies');
  return res.json();
}

export async function checkProxyGeography(proxyId: string): Promise<ProxyItem> {
  const res = await fetch(`${API_BASE}/proxies/${proxyId}/geo-check`, { method: 'POST' });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to check proxy location');
  }
  return res.json();
}

export async function checkAllProxyGeographies(): Promise<{ checked: number; failed: number }> {
  const res = await fetch(`${API_BASE}/proxies/geo-check-all`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to check proxy locations');
  return res.json();
}

export interface ProxyRefreshResult {
  total: number;
  online: number;
  offline: number;
  geo_checked: number;
  geo_failed: number;
}

export async function refreshProxyStatuses(): Promise<ProxyRefreshResult> {
  const res = await fetch(`${API_BASE}/proxies/refresh-status`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to refresh proxy status');
  return res.json();
}

export async function testProxyPing(proxyId: string): Promise<ProxyItem> {
  const res = await fetch(`${API_BASE}/proxies/${proxyId}/test`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to test proxy');
  return res.json();
}

export interface ProxyLatencyResult {
  success: boolean;
  latency_ms: number | null;
  last_checked: string;
  error?: string;
}

export interface FacebookResponseResult {
  success: boolean;
  response_ms: number | null;
  http_status: number | null;
  last_checked: string;
  error?: string | null;
}

export interface ProxySpeedResult {
  success: boolean;
  mbps: number | null;
  bytes: number;
  provider: string;
  last_checked: string;
  error?: string | null;
}

export async function checkProfileProxySpeed(profileId: string, direction: 'download' | 'upload'): Promise<ProxySpeedResult> {
  const res = await fetch(`${API_BASE}/profiles/${encodeURIComponent(profileId)}/proxy-speed?direction=${direction}`, { method: 'POST' });
  if (!res.ok) throw await apiError(res, 'Failed to check proxy speed');
  return res.json();
}

export async function checkProfileFacebookResponse(profileId: string): Promise<FacebookResponseResult> {
  const res = await fetch(`${API_BASE}/profiles/${encodeURIComponent(profileId)}/facebook-response`, { method: 'POST' });
  if (!res.ok) throw await apiError(res, 'Failed to check Facebook response');
  return res.json();
}

export async function checkProfileProxyLatency(profileId: string): Promise<ProxyLatencyResult> {
  const res = await fetch(`${API_BASE}/profiles/${encodeURIComponent(profileId)}/proxy-latency`, { method: 'POST' });
  if (!res.ok) throw await apiError(res, 'Failed to check proxy latency');
  return res.json();
}

export async function deleteProxy(proxyId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/proxies/${proxyId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw await apiError(res, 'Failed to delete proxy');
  return res.json();
}

export interface ProxyDeletionResult {
  deleted_ids: string[];
  blocked_ids: string[];
  missing_ids: string[];
}

export async function deleteSelectedProxies(proxyIds: string[]): Promise<ProxyDeletionResult> {
  const res = await fetch(`${API_BASE}/proxies/delete-selected`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proxy_ids: proxyIds }),
  });
  if (!res.ok) throw await apiError(res, 'Failed to delete selected proxies');
  return res.json();
}

// Automation API Functions
export async function runAutomation(
  params: import('../types/automation').RunAutomationParams
): Promise<{ success: boolean; message: string; state: import('../types/automation').AutomationTaskState }> {
  const res = await fetch(`${API_BASE}/automation/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to start automation');
  }
  return res.json();
}

export async function fetchAutomationStatus(
  profileId: string
): Promise<import('../types/automation').AutomationTaskState> {
  const res = await fetch(`${API_BASE}/automation/status/${profileId}`);
  if (!res.ok) throw new Error('Failed to fetch automation status');
  return res.json();
}

export async function stopAutomation(
  profileId: string
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/automation/stop/${profileId}`, {
    method: 'POST',
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to stop automation');
  }
  return res.json();
}

export async function fetchAutomationTasks(): Promise<
  Record<string, import('../types/automation').AutomationTaskState>
> {
  const res = await fetch(`${API_BASE}/automation/tasks`);
  if (!res.ok) throw new Error('Failed to fetch automation tasks');
  return res.json();
}

// Queue & Batch API Functions
export async function fetchQueue(): Promise<import('../types/automation').QueueDataResponse> {
  const res = await fetch(`${API_BASE}/queue`);
  if (!res.ok) throw new Error('Failed to fetch queue');
  return res.json();
}

export interface SupportBundleOptions {
  executionId?: string;
  description?: string;
  includeEvidence?: boolean;
  includeContent?: boolean;
}

export async function downloadSupportBundle(options: SupportBundleOptions = {}): Promise<string> {
  const endpoint = options.executionId
    ? `${API_BASE}/support/execution/${encodeURIComponent(options.executionId)}`
    : `${API_BASE}/support/export`;
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      description: options.description || '',
      include_evidence: options.includeEvidence === true,
      include_content: options.includeContent === true,
    }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.error || 'Failed to create support bundle');
  }

  const disposition = res.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const filename = filenameMatch?.[1] || 'automat_fb_support.zip';
  const blob = await res.blob();
  if (isDesktopApp()) {
    const bytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
    return invoke<string>('save_support_bundle', { filename, bytes });
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return filename;
}

export async function fetchResourceMode(): Promise<import('../types/automation').ResourceModeSettings> {
  const res = await fetch(`${API_BASE}/settings/resource-mode`);
  if (!res.ok) throw new Error('Failed to fetch resource mode');
  return res.json();
}

export async function updateResourceMode(
  mode: import('../types/automation').ResourceMode
): Promise<import('../types/automation').ResourceModeSettings> {
  const res = await fetch(`${API_BASE}/settings/resource-mode`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.error || 'Failed to update resource mode');
  }
  return res.json();
}

export async function createBatch(
  params: import('../types/automation').CreateBatchParams
): Promise<{ success: boolean; batch_id: string; total_executions: number; batch: import('../types/automation').DailyBatch }> {
  const res = await fetch(`${API_BASE}/queue/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to create batch');
  }
  return res.json();
}

export async function appendPostsToBatch(
  batchId: string,
  posts: import('../types/automation').AppendBatchPostInput[]
): Promise<{ success: boolean; batch_id: string; posts_added: number; executions_added: number }> {
  const res = await fetch(`${API_BASE}/queue/batch/${encodeURIComponent(batchId)}/posts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ posts }),
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to append posts to batch');
  }
  return res.json();
}

export async function deleteBatch(batchId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/queue/batch/${batchId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete batch');
  return res.json();
}

export async function deleteExecution(executionId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/queue/execution/${executionId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete execution');
  return res.json();
}

export async function runExecutionNow(executionId: string): Promise<{ success: boolean; queued?: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/queue/run-now/${executionId}`, {
    method: 'POST',
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to run execution now');
  }
  return res.json();
}

export async function retryExecutionComment(executionId: string): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/queue/retry-comment/${executionId}`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to start comment-only retry');
  }
  return res.json();
}

export async function resolveUncertainExecution(
  executionId: string,
  resolution: 'published' | 'not_published',
  note?: string,
  post_url?: string
): Promise<{ success: boolean; message: string; execution: import('../types/automation').QueueExecutionItem }> {
  const res = await fetch(`${API_BASE}/queue/resolve-uncertain/${executionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resolution, note, post_url }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to resolve uncertain execution');
  }
  return res.json();
}

export async function backfillExecutionPermalink(
  executionId: string,
  post_url: string,
  match_confidence?: number,
  note?: string,
  source?: string
): Promise<{ success: boolean; message: string; execution: import('../types/automation').QueueExecutionItem }> {
  const res = await fetch(`${API_BASE}/queue/backfill-permalink/${executionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ post_url, match_confidence, note, source }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to attach verified permalink');
  }
  return res.json();
}

export async function backfillExecutionCommentStatus(
  executionId: string,
  status: 'submitted_verified' | 'submitted_unverified' | 'submission_pending',
  evidence_dir?: string,
  note?: string,
  source?: string
): Promise<{ success: boolean; message: string; execution: import('../types/automation').QueueExecutionItem }> {
  const res = await fetch(`${API_BASE}/queue/backfill-comment/${executionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, evidence_dir, note, source }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to attach comment evidence');
  }
  return res.json();
}

export async function fetchMediaList(): Promise<import('../types/automation').MediaItem[]> {
  const res = await fetch(`${API_BASE}/media/list`);
  if (!res.ok) throw new Error('Failed to fetch media list');
  return res.json();
}

export async function uploadMediaFiles(
  formData: FormData
): Promise<{ success: boolean; count: number; files: import('../types/automation').MediaItem[] }> {
  const res = await fetch(`${API_BASE}/media/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    throw await apiError(res, 'Failed to upload media files');
  }
  return res.json();
}

export async function generateAiSpins(
  baseCaption: string,
  count: number,
  profileIds: string[]
): Promise<{ success: boolean; variations: { profile_id: string; spun_caption: string }[] }> {
  const res = await fetch(`${API_BASE}/ai/spin-caption`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_caption: baseCaption, count, profile_ids: profileIds }),
  });
  if (!res.ok) throw new Error('Failed to generate AI spins');
  return res.json();
}

export interface CleanEvidenceResult {
  success: boolean;
  profile_id?: string;
  days_kept: number;
  deleted_runs: number;
  freed_bytes: number;
  freed_mb: number;
  freed_formatted: string;
  kept_runs: number;
  new_disk_usage?: string | null;
}

export interface CleanAllEvidenceResult {
  success: boolean;
  days_kept: number;
  total_deleted_runs: number;
  total_freed_bytes: number;
  total_freed_mb: number;
  total_freed_formatted: string;
  total_kept_runs: number;
  profiles: Record<
    string,
    {
      deleted_runs: number;
      freed_bytes: number;
      freed_mb: number;
      freed_formatted: string;
      kept_runs: number;
    }
  >;
}

export async function cleanProfileEvidence(profileId: string, days = 3): Promise<CleanEvidenceResult> {
  const res = await fetch(`${API_BASE}/profiles/${profileId}/clean-evidence`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ days }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to clean profile evidence');
  }
  return res.json();
}

export async function cleanAllEvidence(days = 3): Promise<CleanAllEvidenceResult> {
  const res = await fetch(`${API_BASE}/evidence/clean`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ days }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to clean evidence across profiles');
  }
  return res.json();
}
