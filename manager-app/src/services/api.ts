import { Profile, SystemStats } from '../types/profile';
import { ProxyItem } from '../types/proxy';

const API_BASE = '/api';

export async function fetchProfiles(): Promise<Profile[]> {
  const res = await fetch(`${API_BASE}/profiles`);
  if (!res.ok) throw new Error('Failed to fetch profiles');
  return res.json();
}

export async function createProfile(profileData: Partial<Profile>): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profiles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profileData),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.message || 'Failed to create profile');
  }
  return res.json();
}

export async function updateProfile(
  profileId: string,
  updates: Partial<Profile>
): Promise<Profile> {
  const res = await fetch(`${API_BASE}/profiles/${profileId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.message || 'Failed to update profile');
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
    const err = await res.json();
    throw new Error(err.message || `Failed to ${action} profile`);
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
    const err = await res.json();
    throw new Error(err.error || 'Failed to send text to profile');
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

// Proxy API Functions
export async function fetchProxies(): Promise<ProxyItem[]> {
  const res = await fetch(`${API_BASE}/proxies`);
  if (!res.ok) throw new Error('Failed to fetch proxies');
  return res.json();
}

export async function importProxies(text: string): Promise<{ added: number; total: number }> {
  const res = await fetch(`${API_BASE}/proxies/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error('Failed to import proxies');
  return res.json();
}

export async function testProxyPing(proxyId: string): Promise<ProxyItem> {
  const res = await fetch(`${API_BASE}/proxies/${proxyId}/test`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to test proxy');
  return res.json();
}

export async function deleteProxy(proxyId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/proxies/${proxyId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error('Failed to delete proxy');
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
    const err = await res.json();
    throw new Error(err.error || 'Failed to start automation');
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
    const err = await res.json();
    throw new Error(err.error || 'Failed to stop automation');
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
    const err = await res.json();
    throw new Error(err.error || 'Failed to create batch');
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

export async function runExecutionNow(executionId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/queue/run-now/${executionId}`, {
    method: 'POST',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Failed to run execution now');
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
    const err = await res.json();
    throw new Error(err.error || 'Failed to upload media files');
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
