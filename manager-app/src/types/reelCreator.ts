export type ReelLook = 'breaking' | 'hot' | 'politics' | 'clean' | 'wire' | 'editorial' | 'pop_art';
export type ReelMotion = 'auto' | 'zoom_in' | 'zoom_out' | 'none';
/** engage: get people sharing their view. readmore: send them to the article, linked in the first comment. */
export type ReelGoal = 'engage' | 'readmore';
export type ReelRatio = '9:16' | '4:5';

/** Advanced mode: the elements a look draws, by role. */
export type StyleRole = 'headline' | 'detail' | 'lead' | 'label' | 'credit' | 'brand' | 'logo' | 'ticker';
export type StyleFace = 'bold' | 'regular' | 'display' | 'condensed' | 'condensed-semi' | 'serif' | 'serif-italic';
export type StyleAnimation = 'fade' | 'rise' | 'left' | 'pop' | 'wipe' | 'none';
export type SceneTransition = 'fade' | 'cut' | 'slide';
/** One role's overrides; an absent field keeps the look's own value. */
export interface ElementStyle {
  font?: StyleFace;
  /** Times the look's preferred size, 0.7–1.3; text still shrinks to fit. */
  scale?: number;
  color?: string;
  /** A box behind each line, or the fill of a label or ticker. */
  background?: string;
  animation?: StyleAnimation;
  hidden?: boolean;
}
export type ReelStyles = Partial<Record<StyleRole, ElementStyle>>;

export interface ReelScene {
  headline: string;
  detail: string;
  image_id: string | null;
  focal_x: number;
  focal_y: number;
  seconds: number | null;
  /** blur: the whole photo over a blurred copy of itself instead of a crop. Absent in older drafts. */
  fit?: 'cover' | 'blur';
  /** stat: a big figure over a short label; quote: the headline is quoted words; end: the closing card. */
  kind?: SceneKind;
  stat?: string;
  attribution?: string;
  /** Words of the headline drawn in the accent color. */
  highlight?: string;
  /** Who took or supplied the photo, e.g. "Photo: AP"; drawn only with a photo. Absent in older drafts. */
  credit?: string;
  /** Advanced mode, all absent in simple reels: this scene's styles over the reel's, and its own backdrop. */
  style?: ReelStyles;
  motion?: Exclude<ReelMotion, 'auto'> | null;
  /** How this scene replaces the one before it; absent = crossfade. */
  transition?: SceneTransition | null;
  /** Times the look's darkening behind text, 0–2. */
  shade?: number | null;
  /** Color behind a scene with no photo. */
  backdrop?: string | null;
  /** Text block height in the safe area, 0 highest to 1 lowest; absent = the look's place. Breaking and Wire only. */
  text_position?: number | null;
}

/** Where a scene preview draws each styled element, as fractions of the frame; smaller boxes come last. */
export interface SceneLayout {
  boxes: { role: StyleRole; x: number; y: number; w: number; h: number }[];
  /** Where the text block sits on the text_position scale, or null when this look cannot move it. */
  position: number | null;
}

export type SceneKind = 'story' | 'stat' | 'quote' | 'end';
export interface WrittenScene { headline: string; detail: string; kind: Exclude<SceneKind, 'end'>; stat: string; attribution: string; highlight: string }

export interface Reel {
  version: 4;
  look: ReelLook;
  tag: string | null;
  accent: string | null;
  scenes: ReelScene[];
  brand: string;
  logo_id: string | null;
  source: string;
  dateline: string;
  ratio: ReelRatio;
  motion: ReelMotion;
  show_progress: boolean;
  /** Breaking only: show the LIVE badge. Absent in older drafts. */
  live?: boolean;
  /** auto = the look's own track, off, a bundled track id, or custom (an uploaded file). */
  music: string;
  music_id: string | null;
  /** Advanced mode: styles for every scene. Absent in older drafts. */
  style?: ReelStyles;
}

export interface ReelDraft {
  id: string;
  revision: number;
  updated_at: number;
  reel: Reel;
  caption: string;
  goal: ReelGoal;
  /** First comment: the article link for read-more reels. */
  comment: string;
  hooks: string[];
  source_text: string;
  source_url: string;
  /** Absent in drafts saved before they were kept. */
  source_title?: string;
  source_publisher?: string;
}

export interface ReelAsset {
  id: string;
  kind: 'image' | 'logo' | 'audio';
  name: string;
  width?: number;
  height?: number;
  duration?: number;
  focus?: { x: number; y: number; faces: number };
}

export interface ReelBrand { brand: string; logo_id: string | null; source: string }

/** A newsroom's saved look: how its reels look, never what they say. */
export type HouseStyle = { id: string; name: string } & Pick<Reel, 'look' | 'tag' | 'accent' | 'ratio' | 'motion' | 'show_progress' | 'music'> & { style: ReelStyles };

export interface LookInfo { id: ReelLook; name: string; tag: string; accent: string; music: string }
export interface MusicTrack { id: string; name: string; description: string; duration: number }

export interface ReelCapabilities {
  ready: boolean;
  missing: string[];
  repair: string;
  version: string | null;
  looks: LookInfo[];
  ratios: ReelRatio[];
  music: MusicTrack[];
  max_scenes: number;
  max_seconds: number;
}

export interface ReelJob {
  id: string;
  kind: 'final' | 'preview';
  state: 'queued' | 'rendering' | 'verifying' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  reel: Reel;
  progress: number;
  reason: string | null;
  error: string | null;
  created_at: number;
  attempt: number;
  output: { filename: string; size_bytes: number; thumbnail?: string | null } | null;
  post_caption?: string;
}

export interface WrittenReel {
  look: ReelLook;
  hooks: string[];
  scenes: WrittenScene[];
  /** The closing card in the goal's spirit; absent when adding scenes to a reel. */
  end: { headline: string; detail: string; highlight: string } | null;
  caption: string;
  comment: string;
  source_label: string;
  source_fingerprint: string;
}

export interface ArticleText { url: string; title: string; text: string; publisher: string; published_date: string; characters: number }
export interface PhotoImport { assets: ReelAsset[]; errors: string[] }

export interface CreatedReelMedia {
  jobId: string;
  filename: string;
  /** Reel Creator videos always use the Reel posting workflow, regardless of ratio. */
  postType: 'reel';
  /** The complete opening frame, with the headline and subtitle visible. */
  thumbnail?: string;
  name: string;
  caption?: string;
  /** Posted as the first comment, e.g. the link to the full story. */
  comment?: string;
}
