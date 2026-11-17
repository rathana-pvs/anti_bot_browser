/** AI Reel Studio documents: scenes built from independent parts. Mirrors backend/services/reel_creator/composition.py. */
import type { ReelGoal, ReelRatio, SceneKind, StyleRole } from './reelCreator';

export type LayoutId = 'news_strap' | 'big_headline' | 'photo_panel' | 'light_card' | 'wire_report' | 'magazine' | 'pop_collage'
  | 'big_number' | 'quote_card' | 'centered_card' | 'split' | 'open_photo' | 'paper' | 'ruled_photo';
export type CameraId = 'zoom_in' | 'zoom_out' | 'still' | 'pan_left' | 'pan_right' | 'pan_up' | 'push_face';
export type TransitionId = 'crossfade' | 'cut' | 'slide' | 'wipe' | 'zoom_through';
export type AnimationId = 'fade' | 'rise' | 'left' | 'pop' | 'wipe' | 'none' | 'words' | 'typewriter' | 'count_up' | 'punch_in';
export type EmphasisId = 'highlight_sweep' | 'underline' | 'word_pop' | 'pulse' | 'drift';
export type ExitId = 'fade' | 'fade_up' | 'slide_out' | 'wipe_out';
export type Pacing = 'calm' | 'normal' | 'fast';
export type PartKind = 'layout' | 'camera' | 'transition' | 'animation' | 'emphasis' | 'exit' | 'text_style';

export interface PartStyle { text_style?: string | null; animation?: AnimationId | null; scale?: number | null; color?: string | null; background?: string | null; hidden?: boolean | null }

export interface CompositionScene {
  layout: LayoutId;
  kind: SceneKind;
  headline: string;
  detail: string;
  stat: string;
  attribution: string;
  highlight: string;
  credit: string;
  background: { image_id: string | null; fit: 'cover' | 'blur'; focal_x: number; focal_y: number; zoom: number; color: string | null; shade: number | null };
  camera: CameraId | null;
  transition: TransitionId | null;
  /** What the text does once it is in, while the photo moves. */
  emphasis?: EmphasisId | null;
  /** How the text leaves before the next scene; unset fades it with the scene. */
  exit?: ExitId | null;
  styles: Partial<Record<StyleRole, PartStyle>>;
  seconds: number | null;
  text_position: number | null;
  photo_size: number | null;
  locked: boolean | null;
  /** The scene's narration and when each word is spoken (seconds from the narration's start). */
  voice?: { asset_id: string; text: string; seconds: number; words: [string, number, number][] } | null;
  /** What the voice says instead of the screen text; empty reads the screen text. */
  say?: string;
  /** The detail stays off the screen; the voice still reads it. */
  hide_detail?: boolean | null;
}

export interface Sting { asset_id: string; seconds: number; at: 'start' | 'end' | 'both' }

export interface Composition {
  version: 5;
  recipe: string | null;
  pacing: Pacing;
  ratio: ReelRatio;
  theme: { accent: string | null; tag: string | null; live: boolean };
  scenes: CompositionScene[];
  styles: Partial<Record<StyleRole, PartStyle>>;
  brand: string;
  logo_id: string | null;
  source: string;
  dateline: string;
  show_progress: boolean;
  music: string;
  music_id: string | null;
  /** Word-by-word captions that follow the narration. */
  captions?: 'off' | 'voice';
  caption_size?: 'small' | 'medium' | 'large';
  /** A short sound from My music, before the voice starts, after it ends, or both. */
  sting?: Sting | null;
  /** The finished video's resolution: 480, 720 or 1080 wide. */
  quality?: 'sd' | 'hd' | 'fhd';
  /** At most this many caption words on screen at once (2–6). */
  caption_words?: number;
  /** The camera holds still while each scene's text arrives. */
  camera_waits?: boolean;
  /** Every scene's text floats slowly once it is in. */
  text_drift?: boolean;
  /** The first scene's text arrives like the others' instead of opening finished. */
  animate_opening?: boolean;
}

export interface Part { id: string; kind: PartKind; label: string; use_when: string; avoid_when: string; applies_to: SceneKind[]; mood: string[]; needs_photo: boolean; loud: boolean; look: string }
/** studio 'ai' recipes are AI Studio's own; 'reel' ones are Reel Studio's looks. */
export interface Recipe { id: string; label: string; use_when: string; urgent: boolean; pacing: Pacing; transition: TransitionId; look: string; studio?: 'ai' | 'reel' }
export interface PartsCatalog { parts: Part[]; recipes: Recipe[] }

export interface QualityNote { scene: number; rule: string; message: string }

export interface ComposeResult {
  composition: Composition;
  caption: string;
  comment: string;
  hooks: string[];
  mood: string;
  recipe: string;
  fixes: QualityNote[];
  problems: QualityNote[];
  usage: { input: number; output: number };
  model: string;
}

/** What AI Reel Studio keeps in a draft. */
export interface CompositionDoc {
  composition: Composition;
  caption: string;
  comment: string;
  goal: ReelGoal;
  hooks: string[];
  sourceText: string;
  sourceUrl: string;
  sourceTitle: string;
  sourcePublisher: string;
}
