import type { ElementStyle, HouseStyle, Reel, ReelAsset, ReelBrand, ReelScene, ReelStyles, SceneKind, StyleRole, WrittenReel, WrittenScene } from '../types/reelCreator';

export const MAX_SCENES = 8;
export const MAX_SECONDS = 90;

export const emptyScene = (): ReelScene => ({ headline: '', detail: '', image_id: null, focal_x: .5, focal_y: .4, seconds: null, fit: 'cover', kind: 'story', stat: '', attribution: '', highlight: '', credit: '' });

export const emptyReel = (brand?: ReelBrand | null): Reel => ({
  version: 4, look: 'breaking', tag: null, accent: null, scenes: [emptyScene()],
  brand: brand?.brand ?? '', logo_id: brand?.logo_id ?? null, source: brand?.source ?? '', dateline: '',
  ratio: '9:16', motion: 'auto', show_progress: true, live: false, music: 'auto', music_id: null,
});

/** Mirrors the renderer's timing: enough time to read, within a comfortable range. */
export function sceneSeconds(scene: ReelScene): number {
  if (scene.seconds != null) return scene.seconds;
  const count = (text = '') => text.split(/\s+/).filter(Boolean).length;
  const words = count(scene.headline) + count(scene.detail) + (scene.kind === 'quote' ? count(scene.attribution) : 0) + (scene.kind === 'stat' && scene.stat ? 1 : 0);
  return Math.round(Math.min(8, Math.max(3.5, 2.6 + .3 * words)) * 10) / 10;
}

export const reelSeconds = (reel: Reel): number => Math.round(reel.scenes.reduce((sum, scene) => sum + sceneSeconds(scene), 0) * 10) / 10;

/** Photos from the picker, in order, fill scenes that have none; extra scenes are never invented. */
export function fillPhotos(reel: Reel, assets: ReelAsset[]): Reel {
  const queue = [...assets];
  return { ...reel, scenes: reel.scenes.map(scene => {
    if (scene.image_id || scene.kind === 'end' || !queue.length) return scene;
    const asset = queue.shift()!;
    return { ...scene, image_id: asset.id, focal_x: asset.focus?.x ?? .5, focal_y: asset.focus?.y ?? .4 };
  }) };
}

/**
 * New photos fill scenes that have none, then each remaining photo becomes a scene of its own.
 * `created` lists the new scenes, which still need text.
 */
export function placePhotos(reel: Reel, assets: ReelAsset[]): { reel: Reel; created: number[] } {
  let next = fillPhotos(reel, assets);
  const used = new Set(next.scenes.map(s => s.image_id));
  const created: number[] = [];
  for (const asset of assets.filter(a => !used.has(a.id))) {
    if (next.scenes.length >= MAX_SCENES) break;
    const index = insertIndex(next);
    created.push(index);
    next = setPhoto(addScene(next), index, asset);
  }
  return { reel: next, created };
}

/** Write text into specific scenes, leaving photos, timing and every other scene alone. */
export function fillScenes(reel: Reel, indexes: number[], copy: WrittenScene[]): Reel {
  return { ...reel, scenes: reel.scenes.map((scene, i) => {
    const k = indexes.indexOf(i);
    return k < 0 || !copy[k] ? scene : { ...scene, ...words(copy[k]) };
  }) };
}

/** The written fields of a scene; the photo, framing and timing stay the user's. */
const words = (copy: WrittenScene): Partial<ReelScene> => ({
  headline: copy.headline, detail: copy.detail, kind: copy.kind ?? 'story', stat: copy.stat ?? '', attribution: copy.attribution ?? '', highlight: copy.highlight ?? '',
});

export const headlines = (reel: Reel): string[] => reel.scenes.filter(s => s.kind !== 'end').map(s => s.headline.trim()).filter(Boolean).slice(0, MAX_SCENES);

/** Where a new scene goes: before the end card, which stays last. */
export const insertIndex = (reel: Reel): number => reel.scenes.at(-1)?.kind === 'end' ? reel.scenes.length - 1 : reel.scenes.length;

export function setPhoto(reel: Reel, index: number, asset: ReelAsset | null): Reel {
  return { ...reel, scenes: reel.scenes.map((scene, i) => i !== index ? scene : asset
    ? { ...scene, image_id: asset.id, focal_x: asset.focus?.x ?? .5, focal_y: asset.focus?.y ?? .4 }
    : { ...scene, image_id: null }) };
}

/** A written draft replaces the words only; photos already chosen stay with their scene position. */
export function applyWriting(reel: Reel, written: WrittenReel, keepLook = false): Reel {
  const scenes: ReelScene[] = written.scenes.slice(0, MAX_SCENES).map((copy, i) => ({
    ...(reel.scenes[i] ?? emptyScene()), ...words(copy), seconds: null,
  }));
  if (written.end && scenes.length < MAX_SCENES) {
    scenes.push({ ...emptyScene(), kind: 'end', headline: written.end.headline, detail: written.end.detail, highlight: written.end.highlight });
  }
  return { ...reel, look: keepLook ? reel.look : written.look, scenes, source: reel.source || written.source_label };
}

export const withHook = (reel: Reel, hook: string): Reel => ({ ...reel, scenes: reel.scenes.map((s, i) => i ? s : { ...s, headline: hook }) });

export function moveScene(reel: Reel, from: number, to: number): Reel {
  if (to < 0 || to >= reel.scenes.length || from === to) return reel;
  const scenes = [...reel.scenes];
  const [scene] = scenes.splice(from, 1);
  scenes.splice(to, 0, scene);
  return { ...reel, scenes };
}

export const removeScene = (reel: Reel, index: number): Reel =>
  reel.scenes.length < 2 ? reel : { ...reel, scenes: reel.scenes.filter((_, i) => i !== index) };

export function addScene(reel: Reel): Reel {
  if (reel.scenes.length >= MAX_SCENES) return reel;
  const scenes = [...reel.scenes];
  scenes.splice(insertIndex(reel), 0, emptyScene());
  return { ...reel, scenes };
}

export const hasContent = (reel: Reel): boolean => reel.scenes.some(scene => scene.headline.trim() || scene.detail.trim());

/** Stable identity of what a render shows; captions and hooks do not change the video. */
export const reelKey = (reel: Reel): string => JSON.stringify(reel);

export function draftTitle(reel: Reel): string {
  const first = reel.scenes.find(scene => scene.headline.trim())?.headline.replace(/\s+/g, ' ').trim();
  return first ? (first.length > 48 ? first.slice(0, 47) + '…' : first) : 'Untitled reel';
}

/** "Oct 25, 2026" from an article's ISO publish date, or from today when it has none. */
export function datelineFor(published?: string, today = new Date()): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(published ?? '');
  const date = match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : today;
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** A pasted web address is fetched; anything else is the article itself. */
export function sourceKind(input: string): 'url' | 'text' | 'empty' {
  const value = input.trim();
  if (!value) return 'empty';
  return /^https?:\/\/\S+$/i.test(value) ? 'url' : 'text';
}

/** A role's style with `patch` applied; cleared fields and emptied roles are dropped so a reset reel matches simple mode. */
export function patchStyle(styles: ReelStyles | undefined, role: StyleRole, patch: Partial<Record<keyof ElementStyle, unknown>> | null): ReelStyles {
  const next: ReelStyles = { ...styles };
  const merged = Object.fromEntries(Object.entries(patch ? { ...next[role], ...patch } : {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')) as ElementStyle;
  if (Object.keys(merged).length) next[role] = merged; else delete next[role];
  return next;
}

/** What a role looks like in one scene: the scene's own overrides laid over the reel's. */
export const effectiveStyle = (reel: Reel, scene: ReelScene | undefined, role: StyleRole): ElementStyle =>
  ({ ...reel.style?.[role], ...scene?.style?.[role] });

/** Whether a scene sets anything of its own in advanced mode. */
export const sceneIsStyled = (scene: ReelScene): boolean =>
  !!(Object.keys(scene.style ?? {}).length || scene.motion || scene.transition || scene.shade != null || scene.backdrop || scene.text_position != null);

/** Advanced settings of a scene removed, leaving its words, photo and timing. */
export const unstyledScene = ({ style: _style, motion: _motion, transition: _transition, shade: _shade, backdrop: _backdrop, text_position: _position, ...scene }: ReelScene): ReelScene => scene;

/** Key order and absent-versus-null do not change what a style means. */
const stable = (value: unknown): string => JSON.stringify(value, (_key, v) =>
  v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).filter(([, x]) => x != null).sort(([a], [b]) => a.localeCompare(b))) : v);

/** What a house style keeps from a reel. An uploaded track is this reel's own, so the style keeps the look's music. */
export const houseStyleOf = (reel: Reel, id: string, name: string): HouseStyle => ({
  id, name: name.trim(), look: reel.look, tag: reel.tag, accent: reel.accent, ratio: reel.ratio, motion: reel.motion,
  show_progress: reel.show_progress, music: reel.music === 'custom' ? 'auto' : reel.music, style: reel.style ?? {},
});

/** The reel in a house style: its words, photos, per-scene settings and any uploaded track stay. */
export const applyHouseStyle = (reel: Reel, house: HouseStyle): Reel => ({
  ...reel, look: house.look, tag: house.tag, accent: house.accent, ratio: house.ratio, motion: house.motion,
  show_progress: house.show_progress, style: house.style, ...(reel.music === 'custom' ? {} : { music: house.music, music_id: null }),
});

/** Whether the reel currently looks exactly as the house style says (an uploaded track counts as the look's music). */
export const matchesHouseStyle = (reel: Reel, house: HouseStyle): boolean => {
  const { id: _id, name: _name, ...mine } = houseStyleOf(reel, house.id, house.name);
  const { id: _hid, name: _hname, ...theirs } = house;
  return stable(mine) === stable(theirs);
};

/** The label a reel carries once it has been updated with new reporting. */
export const UPDATE_LABEL = 'UPDATE';

/**
 * A developing story rewritten from newer reporting: every scene's words are new, while its photo, credit, timing
 * settings and styles stay in place (the end card keeps its own). The look is kept; `mark` sets the UPDATE label.
 */
export function updatedStory(reel: Reel, written: WrittenReel, dateline: string, mark: boolean): Reel {
  const end = reel.scenes.find(scene => scene.kind === 'end');
  const next = applyWriting({ ...reel, scenes: reel.scenes.filter(scene => scene.kind !== 'end') }, written, true);
  const scenes = next.scenes.map(scene => scene.kind === 'end' && end
    ? { ...end, headline: scene.headline, detail: scene.detail, highlight: scene.highlight, seconds: null } : scene);
  return { ...next, scenes, tag: mark ? UPDATE_LABEL : reel.tag, dateline: dateline || reel.dateline };
}

export const KIND_NAMES: Record<SceneKind, string> = { story: 'Story', stat: 'Figure', quote: 'Quote', end: 'End card' };

/** The words of a scene: what a rewrite replaces and Undo puts back. Photo, credit, timing and styles are not words. */
export const sceneWords = (scene: ReelScene): Partial<ReelScene> => ({
  headline: scene.headline, detail: scene.detail, kind: scene.kind ?? 'story', stat: scene.stat ?? '', attribution: scene.attribution ?? '', highlight: scene.highlight ?? '',
});

/** A one-scene rewrite as new words for that scene, or null when the AI returned nothing for it. */
export function rewrittenWords(written: WrittenReel, kind: SceneKind): Partial<ReelScene> | null {
  if (kind === 'end') {
    const end = written.end;
    return end ? { kind: 'end', headline: end.headline, detail: end.detail, highlight: end.highlight, stat: '', attribution: '' } : null;
  }
  const scene = written.scenes[0];
  return scene ? { kind: scene.kind, headline: scene.headline, detail: scene.detail, stat: scene.stat, attribution: scene.attribution, highlight: scene.highlight } : null;
}
