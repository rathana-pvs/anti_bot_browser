import type { ContentFit, EditorialDraft, ReelComposition, TemplateNewsGeneration } from '../types/reelCreator';

/** Only content constraints: changing appearance never requests a rewrite. */
export function contentSettingsKey(value: ReelComposition, tone: EditorialDraft['tone']='neutral'): string {
  return JSON.stringify([value.template,value.layout_revision ?? 1,value.aspect_ratio ?? '9:16',
    value.duration,value.auto_timing ?? false,value.slide_seconds,value.show_subtitle,value.news_design ?? 'classic',
    value.motion,value.text_animation,tone,value.photo_layout ?? 'template',value.photo_panel_end ?? null,
    value.photos.length ? value.photos.map(photo=>[photo.image_id,photo.duration_seconds ?? null]) : [value.image_id]]);
}
export function contentFitKey(value: ReelComposition): string {
  return JSON.stringify([contentSettingsKey(value),value.headline,value.supporting,value.category,
    value.photos.map(photo=>[photo.headline,photo.caption])]);
}
/** Protect source, manual video edits and post-caption edits during async work. */
export function contentReviewKey(editorial: EditorialDraft, value: ReelComposition): string {
  return JSON.stringify([editorial.mode,editorial.url,editorial.text,editorial.source_url,editorial.source_title,
    editorial.publisher,editorial.language,editorial.tone ?? 'neutral',editorial.word_count ?? 'standard',editorial.post_caption,contentFitKey(value),value.source]);
}
export function applyContentProposal(current: ReelComposition, result: TemplateNewsGeneration): Partial<ReelComposition> {
  const proposal=result.proposal;
  // Rendering/appearance and framing always come from the current editor.
  return {headline:proposal.headline,supporting:proposal.supporting,category:proposal.category,source:proposal.source,
    photos:current.photos.length ? current.photos.map((photo,index)=>({...photo,headline:proposal.photos[index]?.headline,caption:proposal.photos[index]?.caption})) : proposal.photos,
    image_id:proposal.image_id,version:proposal.version,duration:proposal.duration};
}

/** Keep the wording and total length; spread photos across a readable Fit video. */
export function moreReadingTime(value:ReelComposition, fit:ContentFit, allowed:readonly number[]):Partial<ReelComposition>|null {
  const longest=Math.max(3,...fit.scenes.map(scene=>scene.recommended_seconds ?? Math.ceil((scene.words ?? scene.max_words)/3+.6)));
  const required=Math.max(value.duration,longest*Math.max(1,fit.scenes.length));
  const duration=allowed.find(seconds=>seconds>=required);
  return duration == null ? null : {duration,auto_timing:false,slide_seconds:null};
}
