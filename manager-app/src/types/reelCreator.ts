export interface PhotoCrop { left: number; top: number; right: number; bottom: number }

export type ReelAspectRatio = '9:16' | '4:5' | '1:1';
export type SavedPhotoFraming = Pick<ReelPhoto, 'framing' | 'zoom' | 'offset_x' | 'offset_y' | 'focal_x' | 'focal_y'>;

export interface ReelPhoto {
  headline?: string | null;
  caption?: string | null;
  duration_seconds?: number | null;
  image_id: string;
  movement: 'auto' | 'zoom_in' | 'zoom_out' | 'pan_left' | 'pan_right' | 'still';
  focal_x: number;
  focal_y: number;
  framing?: 'inherit' | 'fit' | 'fill';
  zoom?: number;
  offset_x?: number;
  offset_y?: number;
  crop?: PhotoCrop | null;
  framing_by_ratio?: Partial<Record<ReelAspectRatio, SavedPhotoFraming>>;
}

export interface ReelComposition {
  version: 1 | 2 | 3;
  layout_revision?: 1 | 2;
  lens_theme?: 'light' | 'dark';
  photo_layout?: 'template' | 'full_bleed' | 'adjustable';
  photo_panel_end?: number | null;
  motion_intensity?: 'subtle' | 'balanced' | 'dramatic';
  show_progress?: boolean;
  progress_position?: 'top' | 'bottom';
  photos: ReelPhoto[];
  motion_style: 'bulletin' | 'politics' | 'story';
  text_animation: 'auto' | 'slide' | 'slide_down' | 'slide_left' | 'slide_right' | 'fade' | 'none';
  auto_timing?: boolean;
  slide_seconds: 3 | 5 | 7 | 10 | null;
  slideshow_effect: 'auto' | 'zoom_in' | 'zoom_out' | 'pan_left' | 'pan_right' | 'fade' | 'still';
  transition: 'fade' | 'cut';
  show_subtitle: boolean;
  aspect_ratio?: ReelAspectRatio;
  resolution: 720 | 1080;
  fps: 24 | 30 | 60;
  template: 'bbc' | 'economist' | 'reuters' | 'ap_wire' | 'lens_report' | 'daily_brief' | 'newsroom' | 'editorial' | 'brief';
  news_design?: 'classic' | 'clean';
  headline_emphasis?: string;
  headline_size?: 'auto' | 'small' | 'medium' | 'large' | 'xlarge';
  subtitle_size?: 'auto' | 'small' | 'medium' | 'large';
  image_id: string | null;
  logo_id: string | null;
  audio_id: string | null;
  narration_enabled?: boolean;
  narration_text?: string;
  narration_voice?: 'en-US-GuyNeural' | 'en-US-JennyNeural';
  narration_rate?: '-10%' | '+0%' | '+10%';
  narration_id?: string | null;
  narration_duration?: number | null;
  audio_preset: 'none' | 'breaking' | 'headlines' | 'politics' | 'urgent_wire' | 'alert_desk' | 'live_update' | 'developing_story' | 'major_headline' | 'washington_watch';
  treatment: 'fit' | 'fill';
  headline: string;
  supporting: string;
  category: string;
  brand: string;
  source: string;
  accent: string;
  duration: number;
  motion: boolean;
  focal_x: number;
  focal_y: number;
  volume: number;
}

export const emptyComposition = (): ReelComposition => ({
  version: 2, photos: [], motion_style: 'bulletin', text_animation: 'auto', slide_seconds: 5, slideshow_effect: 'auto', transition: 'fade', show_subtitle: true, show_progress: true, progress_position: 'top', aspect_ratio: '9:16', resolution: 1080, fps: 30, template: 'bbc', image_id: null, logo_id: null, audio_id: null, audio_preset: 'breaking',
  treatment: 'fit', headline: '', supporting: '', category: '', brand: '', source: '', news_design: 'clean', headline_emphasis: '', headline_size: 'auto', subtitle_size: 'auto', narration_enabled: false, narration_text: '', narration_voice: 'en-US-GuyNeural', narration_rate: '+0%', narration_id: null,
  accent: '#ef4444', duration: 10, motion: true, focal_x: 0.5, focal_y: 0.5, volume: 0.2,
});

export interface ReelDraft {
  id: string;
  revision: number;
  composition: ReelComposition;
  updated_at: number;
  editorial: EditorialDraft;
}

export interface ReelAsset {
  id: string;
  kind: 'image' | 'logo' | 'audio';
  name: string;
  width?: number;
  height?: number;
  duration?: number;
  suggested_framing?: FramingSuggestion;
}

export interface FramingSuggestion {
  settings: Partial<ReelPhoto>;
  faces: number;
  status: 'detected' | 'none' | 'unavailable';
}

export interface ReelJob {
  id: string;
  kind: 'final' | 'preview';
  state: 'queued' | 'rendering' | 'verifying' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  composition: ReelComposition;
  progress: number;
  reason: string | null;
  error: string | null;
  warnings?: string[];
  created_at: number;
  attempt: number;
  output: { filename: string; size_bytes: number } | null;
  post_caption?: string;
}

export interface ReelCapabilities {
  lens_report_revision?: 1 | 2;
  flexible_scenes?: boolean;
  lens_report?: boolean;
  lens_report_reason?: string;
  daily_brief?: boolean;
  daily_brief_reason?: string;
  story_motion?: boolean;
  slideshow_controls?: boolean;
  photo_framing?: boolean;
  aspect_ratios?: ReelAspectRatio[];
  max_photos?: number;
  ready: boolean;
  missing: string[];
  repair: string;
  version: string | null;
}

export interface CreatedReelMedia {
  jobId: string;
  filename: string;
  name: string;
  caption?: string;
}

export interface NewsScene { headline: string; caption: string; source_quotes: string[] }
export interface ContentFit { fits: boolean; issues: {scene: number | null; kind: 'layout' | 'reading' | 'settings'; message: string}[]; scenes: {scene:number; seconds:number; max_words:number; words?:number; recommended_seconds?:number}[] }
export interface TemplateNewsGeneration extends NewsGeneration { proposal: ReelComposition; fit: ContentFit; warnings?:string[] }

export interface NewsCopy {
  scenes?: NewsScene[];
  hook: string;
  headline: string;
  highlights: { text: string; source_quote: string }[];
  category: string;
  post_caption: string;
  hashtags: string[];
  source_label: string;
  suggested_template: ReelComposition['template'];
  suggested_audio: 'breaking' | 'headlines' | 'politics';
}

export interface EditorialDraft {
  mode: 'url' | 'text';
  url: string;
  text: string;
  source_url: string;
  source_title: string;
  publisher: string;
  language: 'English' | 'Khmer' | 'Match source';
  post_caption: string;
  generated: NewsCopy | null;
  generated_source: string;
  generated_caption: string;
  tone?: 'neutral' | 'explainer' | 'concise';
  word_count?: 'short' | 'standard' | 'detailed';
  applied_settings?: string;
}

export const emptyEditorial = (): EditorialDraft => ({
  mode: 'text', url: '', text: '', source_url: '', source_title: '', publisher: '',
  language: 'English', post_caption: '', generated: null, generated_source: '', generated_caption: '',
  word_count: 'standard',
});

export interface AudioPreset { id: Exclude<ReelComposition['audio_preset'], 'none'>; name: string; description: string; duration: number }
export interface ArticleText { url: string; title: string; text: string; publisher: string; published_date: string; characters: number }
export interface NewsGeneration { copy: NewsCopy; post_caption: string; model: string; source_fingerprint: string }

export interface PhotoImport { assets: ReelAsset[]; errors: string[] }
