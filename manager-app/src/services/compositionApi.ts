/** AI Reel Studio calls. Rendering, jobs, photos and exports are Reel Studio's own (reelApi), which take either document. */
import type { AIRequestConfig } from './aiSettings';
import { json, request } from './reelCreator';
import type { ComposeResult, Composition, PartsCatalog, QualityNote } from '../types/composition';
import type { DirectorResult, DirectorTurn, ReelDraft, ReelGoal, ReelJob } from '../types/reelCreator';

/** A track the operator added: level-matched to the built-in ones; an intro plays once, a bed under the whole reel. */
export interface MyTrack { id: string; name: string; seconds: number; role: 'bed' | 'intro'; source_url: string; added_at: number }

export interface FactCheck { field: 'stat' | 'headline' | 'detail' | 'attribution' | 'voice'; text: string; source: string;
  missing_numbers: string[]; missing_names: string[]; quote_missing: boolean; note: string; status: 'ok' | 'check' | 'problem' }
export interface FactPanel { scenes: { scene: number; checks: FactCheck[] }[]; counts: { ok: number; check: number; problem: number }; has_article: boolean }
export interface HookIdea { text: string; angle: 'question' | 'number' | 'consequence' | 'surprise' | 'human'; highlight: string; score: number; why: string }
export interface StorySuggestion { kind: 'move' | 'cut' | 'rewrite' | 'add'; scene: number | null; to: number | null; after: number | null; problem: string; instruction: string }
export interface StoryReview { verdict: 'strong' | 'ok' | 'weak'; summary: string; suggestions: StorySuggestion[] }
type CoachInput = AIRequestConfig & { composition: Composition; source_text: string; goal: string };

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
  recipe: async (composition: Composition, recipe: string): Promise<{ composition: Composition; fixes: QualityNote[]; problems: QualityNote[] }> =>
    (await request('/compositions/recipe', json('POST', { composition, recipe }))).json(),
  review: async (composition: Composition): Promise<{ composition: Composition; fixes: QualityNote[]; problems: QualityNote[] }> =>
    (await request('/compositions/review', json('POST', composition))).json(),
  poster: async (composition: Composition, scene: number, signal?: AbortSignal): Promise<Blob> =>
    (await request(`/poster?scene=${scene}`, { ...json('POST', composition), signal })).blob(),
  render: async (composition: Composition, kind: ReelJob['kind'], request_id: string, caption = ''): Promise<ReelJob> =>
    (await request('/jobs', json('POST', { reel: composition, kind, request_id, caption }))).json(),
  visionCheck: async (value: AIRequestConfig & { composition: Composition }): Promise<{ composition: Composition; message: string; notes: QualityNote[]; refused: string[]; usage: { input: number; output: number } }> =>
    (await request('/compositions/vision-check', json('POST', value))).json(),
  facts: async (composition: Composition, source_text: string): Promise<FactPanel> =>
    (await request('/compositions/facts', json('POST', { composition, source_text }))).json(),
  hooks: async (value: CoachInput): Promise<{ hooks: HookIdea[] }> => (await request('/compositions/hooks', json('POST', value))).json(),
  storyCheck: async (value: CoachInput): Promise<StoryReview> => (await request('/compositions/story-check', json('POST', value))).json(),
  script: async (value: AIRequestConfig & { composition: Composition; source_text: string; goal: string }): Promise<{ composition: Composition; written: number; skipped: { scene: number; reason: string }[] }> =>
    (await request('/compositions/script', json('POST', value))).json(),
  voiceover: async (value: AIRequestConfig & { composition: Composition; voice: string }): Promise<{ composition: Composition; characters: number }> =>
    (await request('/compositions/voiceover', json('POST', value))).json(),
  musicLibrary: async (): Promise<MyTrack[]> => (await request('/music/library')).json(),
  addMusic: async (asset_id: string, source_url = ''): Promise<MyTrack> => (await request('/music/library', json('POST', { asset_id, source_url }))).json(),
  updateMusic: async (id: string, change: { source_url?: string; role?: MyTrack['role'] }): Promise<MyTrack> =>
    (await request(`/music/library/${id}`, json('PUT', change))).json(),
  removeMusic: async (id: string): Promise<void> => { await request(`/music/library/${id}`, { method: 'DELETE' }); },
  myTrackPreview: async (id: string): Promise<Blob> => (await request(`/music/library/${id}/preview`)).blob(),
  cover: async (composition: Composition): Promise<{ filename: string }> => (await request('/cover', json('POST', composition))).json(),
  drafts: async (): Promise<CompositionDraft[]> => ((await (await request('/drafts')).json()) as CompositionDraft[]).filter(d => d.reel?.version === 5),
  saveDraft: async (id: string, revision: number, draft: Omit<CompositionDraft, 'id' | 'revision' | 'updated_at'>): Promise<CompositionDraft> =>
    (await request(`/drafts/${id}`, json('PUT', { revision, ...draft }))).json(),
  deleteDraft: async (id: string): Promise<void> => { await request(`/drafts/${id}`, { method: 'DELETE' }); },
};
