import type { ReelComposition, ReelPhoto } from '../types/reelCreator';

export const reelPhotos = (value: ReelComposition): ReelPhoto[] => value.version >= 2 && value.photos.length ? value.photos : value.image_id
  ? [{ image_id: value.image_id, movement: 'auto', focal_x: value.focal_x, focal_y: value.focal_y }] : [];

export const reelDurations = [5, 10, 15, 20, 25, 30, 45, 60] as const;
export const minimumDuration = (value: ReelComposition, count = reelPhotos(value).length) => count * (value.slide_seconds ?? 3);

/** One immutable update keeps the cover, scene order and reading time consistent. */
export function storyUpdate(value: ReelComposition, photos = reelPhotos(value)): Partial<ReelComposition> {
  if (photos.length > 5) throw new Error('Choose up to five photos.');
  if (value.auto_timing && photos.length) {
    return {version: value.template === 'lens_report' ? 3 : 2, photos, image_id: photos[0]?.image_id || null,
      duration: photoSeconds(value, photos).reduce((a, b) => a + b, 0)};
  }
  const duration = reelDurations.find(seconds => seconds >= Math.max(value.duration, minimumDuration(value, photos.length)))!;
  return { version: value.template === 'lens_report' ? 3 : 2, photos, image_id: photos[0]?.image_id || null, duration };
}

/** Match the local renderer, including unspaced Khmer headlines. */
export function readingSeconds(headline: string): number {
  headline = headline.trim();
  const words = (headline.match(/[A-Za-z0-9]+/g) || []).length
    + Math.ceil(Array.from(headline).filter(char => char.codePointAt(0)! > 127).length / 5);
  return words <= 6 ? 3 : words <= 12 ? 5 : words <= 20 ? 7 : 10;
}

export function photoSeconds(value: ReelComposition, photos = reelPhotos(value)): number[] {
  const seconds = photos.map(photo => photo.duration_seconds || ((value.template === 'lens_report' || value.template==='daily_brief' && !!value.photo_layout && value.photo_layout!=='template')
    ? Math.max(4, Math.min(10, Math.ceil(`${photo.headline ?? value.headline} ${value.show_subtitle ? (value.template==='lens_report'?photo.caption ?? value.supporting:value.supporting) : ''}`.trim().split(/\s+/).filter(Boolean).length / 3 + ((value.layout_revision === 2 || value.template==='daily_brief') && value.motion && value.text_animation !== 'none' ? .6 : 0))))
    : readingSeconds(photo.headline ?? value.headline)));
  if (!seconds.length || !(value.narration_enabled && value.narration_id && value.narration_duration)) return seconds;
  const minimum = photos.map(photo => photo.duration_seconds || 3);
  const total = Math.max(minimum.reduce((a, b) => a + b, 0), Math.ceil(value.narration_duration + .25));
  const flexible = photos.map((_, index) => index).filter(index => photos[index].duration_seconds == null);
  if (!flexible.length) return minimum;
  const remaining = total - minimum.reduce((a, b) => a + b, 0);
  const weight = flexible.reduce((sum, index) => sum + seconds[index], 0);
  const shares = flexible.map(index => ({ index, share: remaining * seconds[index] / weight }));
  for (const {index, share} of shares) minimum[index] += Math.floor(share);
  const leftover = total - minimum.reduce((a, b) => a + b, 0);
  shares.sort((a, b) => (b.share % 1) - (a.share % 1)).slice(0, leftover)
    .forEach(({index}) => { minimum[index] += 1; });
  return minimum;
}

export function normalizeTiming(value: ReelComposition): ReelComposition {
  if (value.auto_timing && reelPhotos(value).length) {
    return {...value, duration: photoSeconds(value).reduce((a, b) => a + b, 0)};
  }
  return reelDurations.includes(value.duration as typeof reelDurations[number]) ? value
    : {...value, duration: reelDurations.find(seconds => seconds >= value.duration) ?? 60};
}
