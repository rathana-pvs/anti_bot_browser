/** AI Reel Studio calls. Rendering, jobs, photos and exports are Reel Studio's own (reelApi), which take either document. */
import type { AIRequestConfig } from './aiSettings';
import { json, request } from './reelCreator';
import type { ComposeResult, Composition, PartsCatalog, QualityNote } from '../types/composition';
import type { DirectorResult, DirectorTurn, ReelDraft, ReelGoal, ReelJob } from '../types/reelCreator';

export interface ComposeRequest extends AIRequestConfig {
  text: string; source_url: string; source_title: string; publisher: string;
  goal: ReelGoal; photos: string[]; scenario: string; recipe: string | null;
}

export type CompositionDirectorResult = Omit<DirectorResult, 'reel'> & { reel: Composition };
export interface CompositionDraft extends Omit<ReelDraft, 'reel'> { reel: Composition }

export const compositionApi = {
  catalog: async (): Promise<PartsCatalog> => ((await (await request('/capabilities')).json()) as { components: PartsCatalog }).components,
  compose: async (value: ComposeRequest): Promise<ComposeResult> => (await request('/compositions/compose', json('POST', value))).json(),
  direct: async (value: AIRequestConfig & { reel: Composition; instruction: string; source_text: string; goal: ReelGoal; history: DirectorTurn[] }): Promise<CompositionDirectorResult> =>
    (await request('/compositions/direct', json('POST', value))).json(),
  review: async (composition: Composition): Promise<{ composition: Composition; fixes: QualityNote[]; problems: QualityNote[] }> =>
    (await request('/compositions/review', json('POST', composition))).json(),
  poster: async (composition: Composition, scene: number, signal?: AbortSignal): Promise<Blob> =>
    (await request(`/poster?scene=${scene}`, { ...json('POST', composition), signal })).blob(),
  render: async (composition: Composition, kind: ReelJob['kind'], request_id: string, caption = ''): Promise<ReelJob> =>
    (await request('/jobs', json('POST', { reel: composition, kind, request_id, caption }))).json(),
  visionCheck: async (value: AIRequestConfig & { composition: Composition }): Promise<{ composition: Composition; message: string; notes: QualityNote[]; refused: string[]; usage: { input: number; output: number } }> =>
    (await request('/compositions/vision-check', json('POST', value))).json(),
  voiceover: async (value: AIRequestConfig & { composition: Composition; voice: string }): Promise<{ composition: Composition; characters: number }> =>
    (await request('/compositions/voiceover', json('POST', value))).json(),
  cover: async (composition: Composition): Promise<{ filename: string }> => (await request('/cover', json('POST', composition))).json(),
  drafts: async (): Promise<CompositionDraft[]> => ((await (await request('/drafts')).json()) as CompositionDraft[]).filter(d => d.reel?.version === 5),
  saveDraft: async (id: string, revision: number, draft: Omit<CompositionDraft, 'id' | 'revision' | 'updated_at'>): Promise<CompositionDraft> =>
    (await request(`/drafts/${id}`, json('PUT', { revision, ...draft }))).json(),
  deleteDraft: async (id: string): Promise<void> => { await request(`/drafts/${id}`, { method: 'DELETE' }); },
};
