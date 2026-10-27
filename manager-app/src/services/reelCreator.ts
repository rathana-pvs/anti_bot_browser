import { invoke } from '@tauri-apps/api/core';
import { API_BASE } from './api';
import { isDesktopApp } from './desktopEnvironment';
import type { ArticleText, PhotoImport, Reel, ReelAsset, ReelGoal, ReelBrand, ReelCapabilities, HouseStyle, ReelDraft, ReelJob, SceneKind, SceneLayout, WrittenReel } from '../types/reelCreator';

async function request(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE}/reel-creator${path}`, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item: { msg: string }) => item.msg).join('; ');
    } catch { /* Keep the HTTP error when the response is not JSON. */ }
    throw new Error(message);
  }
  return response;
}

function json(method: string, value: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) };
}

export interface WriteRequest {
  api_key: string; model: string; text: string; source_url: string; source_title: string; publisher: string;
  look: 'auto' | Reel['look'];
  goal?: ReelGoal;
  /** One scene per photo; omit to let the AI choose. */
  scenes?: number;
  /** Headlines already in the reel, when adding scenes to it. */
  existing?: string[];
  /** The post caption is written only when this is true. */
  caption?: boolean;
  /** Rewrite one scene as this kind; `existing` then holds the other scenes' headlines. */
  rewrite?: SceneKind;
  /** That scene's place in the reel, 1 = the hook. */
  position?: number;
  /** Its current text, so the new text is not a copy. */
  replacing?: string;
}

export const reelApi = {
  capabilities: async (refresh = false): Promise<ReelCapabilities> => (await request(`/capabilities?refresh=${refresh}`)).json(),
  drafts: async (): Promise<ReelDraft[]> => (await request('/drafts')).json(),
  saveDraft: async (id: string, revision: number, draft: Pick<ReelDraft, 'reel' | 'caption' | 'goal' | 'comment' | 'hooks' | 'source_text' | 'source_url' | 'source_title' | 'source_publisher'>): Promise<ReelDraft> =>
    (await request(`/drafts/${id}`, json('PUT', { revision, ...draft }))).json(),
  deleteDraft: async (id: string): Promise<void> => { await request(`/drafts/${id}`, { method: 'DELETE' }); },
  article: async (url: string, signal?: AbortSignal): Promise<ArticleText> => (await request('/article', { ...json('POST', { url }), signal })).json(),
  write: async (value: WriteRequest, signal?: AbortSignal): Promise<WrittenReel> => (await request('/write', { ...json('POST', value), signal })).json(),
  upload: async (kind: ReelAsset['kind'], file: File): Promise<ReelAsset> => {
    const data = new FormData();
    data.append('file', file);
    return (await request(`/assets/${kind}`, { method: 'POST', body: data })).json();
  },
  /** Desktop uses the native picker; a browser falls back to a file input. Returns null when cancelled. */
  asset: async (id: string): Promise<ReelAsset> => (await request(`/assets/${id}`)).json(),
  musicPreview: async (track: string): Promise<Blob> => (await request(`/music/${track}/preview`)).blob(),
  pickAudio: async (): Promise<ReelAsset | null> => {
    if (isDesktopApp()) return invoke<ReelAsset | null>('pick_reel_audio');
    const file = await new Promise<File | undefined>(resolve => {
      const input = document.createElement('input');
      input.type = 'file'; input.accept = 'audio/*';
      input.onchange = () => resolve(input.files?.[0]);
      input.oncancel = () => resolve(undefined);
      input.click();
    });
    return file ? reelApi.upload('audio', file) : null;
  },
  pickImages: async (maxFiles: number): Promise<PhotoImport | null> => {
    if (isDesktopApp()) return invoke<PhotoImport | null>('pick_reel_images', { maxFiles });
    const files = await new Promise<File[]>(resolve => {
      const input = document.createElement('input');
      input.type = 'file'; input.accept = 'image/*'; input.multiple = maxFiles > 1;
      input.onchange = () => resolve(Array.from(input.files ?? []).slice(0, maxFiles));
      input.oncancel = () => resolve([]);
      input.click();
    });
    if (!files.length) return null;
    const result: PhotoImport = { assets: [], errors: [] };
    for (const file of files) {
      try { result.assets.push(await reelApi.upload('image', file)); }
      catch (err) { result.errors.push(`${file.name}: ${err instanceof Error ? err.message : 'Could not import.'}`); }
    }
    return result;
  },
  thumbnail: async (id: string): Promise<Blob> => (await request(`/assets/${id}/thumbnail`)).blob(),
  poster: async (reel: Reel, scene: number, signal?: AbortSignal): Promise<Blob> =>
    (await request(`/poster?scene=${scene}`, { ...json('POST', reel), signal })).blob(),
  layout: async (reel: Reel, scene: number, signal?: AbortSignal): Promise<SceneLayout> =>
    (await request(`/layout?scene=${scene}`, { ...json('POST', reel), signal })).json(),
  houseStyles: async (): Promise<HouseStyle[]> => (await request('/house-styles')).json(),
  saveHouseStyle: async (style: HouseStyle): Promise<HouseStyle[]> => (await request(`/house-styles/${style.id}`, json('PUT', style))).json(),
  deleteHouseStyle: async (id: string): Promise<HouseStyle[]> => (await request(`/house-styles/${id}`, { method: 'DELETE' })).json(),
  brand: async (): Promise<ReelBrand | null> => (await request('/brand')).json(),
  saveBrand: async (reel: Reel): Promise<ReelBrand> => (await request('/brand', json('PUT', reel))).json(),
  jobs: async (): Promise<ReelJob[]> => (await request('/jobs')).json(),
  render: async (reel: Reel, kind: ReelJob['kind'], request_id: string, caption = ''): Promise<ReelJob> =>
    (await request('/jobs', json('POST', { reel, kind, request_id, caption }))).json(),
  cancel: async (id: string): Promise<ReelJob> => (await request(`/jobs/${id}/cancel`, { method: 'POST' })).json(),
  retry: async (id: string): Promise<ReelJob> => (await request(`/jobs/${id}/retry`, { method: 'POST' })).json(),
  jobThumbnail: async (id: string): Promise<Blob> => (await request(`/jobs/${id}/thumbnail`)).blob(),
  video: async (id: string): Promise<Blob> => (await request(`/jobs/${id}/video`)).blob(),
  export: async (id: string, filename: string): Promise<string> => {
    if (isDesktopApp()) return invoke<string>('export_reel_video', { jobId: id });
    const blob = await (await request(`/jobs/${id}/download`)).blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    return 'Video download started.';
  },
};
