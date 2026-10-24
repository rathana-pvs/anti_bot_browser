import { invoke } from '@tauri-apps/api/core';
import { API_BASE } from './api';
import { isDesktopApp } from './desktopEnvironment';
import { normalizeTiming } from './storyComposition';
import { emptyComposition, emptyEditorial } from '../types/reelCreator';
import type { ContentFit, TemplateNewsGeneration, ArticleText, AudioPreset, EditorialDraft, NewsGeneration, PhotoImport, ReelAsset, ReelCapabilities, ReelComposition, ReelDraft, ReelJob } from '../types/reelCreator';

const normalizeComposition = (value: ReelComposition): ReelComposition => ({ ...emptyComposition(), ...value, news_design: value.news_design ?? 'classic', slide_seconds: value.slide_seconds ?? null, version: value.version ?? 1, photos: value.photos ?? [], audio_preset: value.audio_preset ?? 'none' });

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

export const reelApi = {
  narrationScript: async (value: {api_key: string; model: string; text: string; source_url: string; source_title: string; publisher: string; language: 'English'; duration: number}, signal?: AbortSignal): Promise<{script:string;source_quotes:string[];word_limit:number}> => (await request('/narration/script', {...json('POST', value), signal})).json(),
  narrationVoice: async (value: {text:string;voice:NonNullable<ReelComposition['narration_voice']>;rate:NonNullable<ReelComposition['narration_rate']>}, signal?: AbortSignal): Promise<ReelAsset> => (await request('/narration/voice', {...json('POST', value), signal})).json(),
  narrationAudio: async (id: string, signal?: AbortSignal): Promise<Blob> => (await request(`/narration/${id}/audio`, {signal})).blob(),
  autoFraming: async (id: string, crop?: import('../types/reelCreator').PhotoCrop|null, aspectRatio: import('../types/reelCreator').ReelAspectRatio = '9:16'): Promise<import('../types/reelCreator').FramingSuggestion> => (await request(`/assets/${id}/framing?aspect_ratio=${encodeURIComponent(aspectRatio)}`,  crop ? json('POST', crop) : undefined)).json(),
  cropPreview: async (id: string, signal?: AbortSignal): Promise<Blob> => (await request(`/assets/${id}/crop-preview`, {signal})).blob(),
  capabilities: async (refresh = false): Promise<ReelCapabilities> => (await request(`/capabilities?refresh=${refresh}`)).json(),
  drafts: async (): Promise<ReelDraft[]> => ((await (await request('/drafts')).json()) as ReelDraft[]).map(d => ({
    ...d, composition: normalizeTiming(normalizeComposition(d.composition)), editorial: { ...emptyEditorial(), ...d.editorial },
  })),
  saveDraft: async (id: string, revision: number, composition: ReelComposition, editorial: EditorialDraft): Promise<ReelDraft> =>
    (await request(`/drafts/${id}`, json('PUT', { revision, composition, editorial }))).json(),
  asset: async (id: string): Promise<ReelAsset> => (await request(`/assets/${id}`)).json(),
  upload: async (kind: ReelAsset['kind'], file: File): Promise<ReelAsset> => {
    const data = new FormData();
    data.append('file', file);
    return (await request(`/assets/${kind}`, { method: 'POST', body: data })).json();
  },
  pickAudio: async (): Promise<ReelAsset | null> => invoke<ReelAsset | null>('pick_reel_audio'),
  thumbnail: async (id: string): Promise<Blob> => (await request(`/assets/${id}/thumbnail`)).blob(),
  pickImages: async (maxFiles: number): Promise<PhotoImport | null> => invoke<PhotoImport | null>('pick_reel_images', { maxFiles }),
  poster: async (composition: ReelComposition, signal?: AbortSignal, photoIndex = 0) => {
    const response = await request(`/poster?photo_index=${photoIndex}`, { ...json('POST', composition), signal });
    return { blob: await response.blob(), warnings: JSON.parse(response.headers.get('X-Reel-Warnings') || '[]') as string[] };
  },
  jobs: async (): Promise<ReelJob[]> => ((await (await request('/jobs')).json()) as ReelJob[]).map(j => ({ ...j, composition: normalizeComposition(j.composition) })),
  render: async (composition: ReelComposition, kind: ReelJob['kind'], request_id: string, post_caption = ''): Promise<ReelJob> =>
    (await request('/jobs', json('POST', { composition, kind, request_id, post_caption }))).json(),
  article: async (url: string, signal?: AbortSignal): Promise<ArticleText> => (await request('/article', { ...json('POST', { url }), signal })).json(),
  generateNews: async (value: { api_key: string; model: string; text: string; source_url: string; source_title: string; publisher: string; language: EditorialDraft['language']; category_hint: string }, signal?: AbortSignal): Promise<NewsGeneration> =>
    (await request('/generate-news', { ...json('POST', value), signal })).json(),
  contentFit: async (composition: ReelComposition, signal?: AbortSignal): Promise<ContentFit> =>
    (await request('/content-fit', {...json('POST', composition), signal})).json(),
  generateTemplateNews: async (value: {api_key:string; model:string; text:string; source_url:string; source_title:string; publisher:string; language:'English'; category_hint:string; composition:ReelComposition; tone:NonNullable<EditorialDraft['tone']>; word_count?:NonNullable<EditorialDraft['word_count']>; intent:'generate'|'adapt'|'shorten'; scene_index:number|null}, signal?:AbortSignal): Promise<TemplateNewsGeneration> =>
    (await request('/generate-template-news', {...json('POST',value),signal})).json(),
  audioPresets: async (): Promise<AudioPreset[]> => (await request('/audio-presets')).json(),
  audioPreview: async (id: string): Promise<Blob> => (await request(`/audio-presets/${id}/preview`)).blob(),
  cancel: async (id: string): Promise<ReelJob> => (await request(`/jobs/${id}/cancel`, { method: 'POST' })).json(),
  retry: async (id: string): Promise<ReelJob> => (await request(`/jobs/${id}/retry`, { method: 'POST' })).json(),
  video: async (id: string): Promise<Blob> => (await request(`/jobs/${id}/video`)).blob(),
  brand: async (): Promise<Pick<ReelComposition, 'brand' | 'accent' | 'logo_id'> | null> => (await request('/brand')).json(),
  saveBrand: async (composition: ReelComposition) => (await request('/brand', json('PUT', composition))).json(),
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
