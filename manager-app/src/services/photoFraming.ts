import type { PhotoCrop, ReelComposition, ReelPhoto, ReelAspectRatio, SavedPhotoFraming } from '../types/reelCreator';

export const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

export const fullCrop: PhotoCrop = {left:0, top:0, right:1, bottom:1};
export type CropHandle = 'move'|'n'|'s'|'e'|'w'|'nw'|'ne'|'sw'|'se';

export function dragCrop(crop: PhotoCrop, handle: CropHandle, dx: number, dy: number): PhotoCrop {
  if (handle === 'move') {
    dx = clamp(dx, -crop.left, 1 - crop.right); dy = clamp(dy, -crop.top, 1 - crop.bottom);
    return {left:crop.left+dx, right:crop.right+dx, top:crop.top+dy, bottom:crop.bottom+dy};
  }
  return {
    left:handle.includes('w') ? clamp(crop.left+dx,0,crop.right-.05) : crop.left,
    right:handle.includes('e') ? clamp(crop.right+dx,crop.left+.05,1) : crop.right,
    top:handle.includes('n') ? clamp(crop.top+dy,0,crop.bottom-.05) : crop.top,
    bottom:handle.includes('s') ? clamp(crop.bottom+dy,crop.top+.05,1) : crop.bottom,
  };
}

export function cropSourceStyle(crop: PhotoCrop = fullCrop) {
  const width = crop.right-crop.left, height = crop.bottom-crop.top;
  return {left:`${-crop.left/width*100}%`,top:`${-crop.top/height*100}%`,width:`${100/width}%`,height:`${100/height}%`};
}

/** Visible photo area; full-frame scenes allow placement across the entire canvas. */
export function photoArea(spec: ReelComposition) {
  const height=reelDimensions({...spec,resolution:1080})[1];
  const flexible=['lens_report','daily_brief'].includes(spec.template) && !!spec.photo_layout && spec.photo_layout!=='template';
  const top=spec.aspect_ratio==='4:5'?(spec.brand||spec.logo_id?128:96):210;
  const end=spec.photo_panel_end != null?Math.round(height*spec.photo_panel_end):spec.aspect_ratio==='4:5'?740:1180;
  if(flexible)return {x:0,y:spec.photo_layout==='full_bleed'?0:top,width:1080,height:spec.photo_layout==='full_bleed'?height:end-top};
  if(spec.template==='daily_brief')return {x:72,y:250,width:912,height:690};
  if(spec.template==='lens_report' && spec.layout_revision===2)return {x:0,y:top,width:1080,height:(spec.aspect_ratio==='4:5'?740:1180)-top};
  return {x:0,y:0,width:1080,height};
}

/** Same canonical canvas and bounds as the local renderer. */
export function photoBounds(spec: ReelComposition, photo: ReelPhoto, width: number, height: number) {
  if (photo.crop) {
    const c = photo.crop;
    // Python round uses ties-to-even; one-pixel rounding differences in this
    // illustrative canvas do not affect the canonical server-rendered preview.
    width = Math.max(1, Math.round(c.right*width)-Math.round(c.left*width));
    height = Math.max(1, Math.round(c.bottom*height)-Math.round(c.top*height));
  }
  const flexible=['lens_report','daily_brief'].includes(spec.template) && !!spec.photo_layout && spec.photo_layout!=='template';
  if(flexible || spec.template==='daily_brief'){
    const area=photoArea(spec),canvasHeight=reelDimensions({...spec,resolution:1080})[1];
    const treatment=photo.framing && photo.framing!=='inherit'?photo.framing:spec.treatment;
    const factor=(treatment==='fit'?Math.min:Math.max)(area.width/width,area.height/height)*(photo.zoom ?? 1);
    const w=width*factor,h=height*factor;
    const offsetWidth=flexible?1080:912,offsetHeight=flexible?canvasHeight:690;
    return {x:area.x+(area.width-w)*(treatment==='fit'?.5:photo.focal_x)+(photo.offset_x ?? 0)*offsetWidth,
      y:area.y+(area.height-h)*(treatment==='fit'?.5:photo.focal_y)+(photo.offset_y ?? 0)*offsetHeight,width:w,height:h,treatment};
  }
  const treatment = photo.framing && photo.framing !== 'inherit' ? photo.framing : spec.treatment;
  const zoom = photo.zoom ?? 1;
  const hasText = !!(spec.headline || (spec.show_subtitle && spec.supporting) || spec.category);
  const canvasHeight = reelDimensions({...spec, resolution:1080})[1];
  const layoutY = (value: number) => Math.round(value * canvasHeight / 1920);
  const lensPanel = spec.template === 'lens_report' && spec.layout_revision === 2;
  const bottom = lensPanel ? (spec.aspect_ratio === '4:5' ? 740 : 1180) : spec.template === 'lens_report' ? canvasHeight : layoutY(hasText ? 1210 : 1730), top = lensPanel ? (spec.aspect_ratio === '4:5' ? (spec.brand || spec.logo_id ? 128 : 96) : 210) : spec.template === 'lens_report' ? 0 : layoutY(230);
  const scale = (treatment === 'fit' ? Math.min((spec.template === 'lens_report' ? 1080 : 880) / width, (bottom - top) / height) : Math.max(1080 / width, (lensPanel ? bottom-top : canvasHeight) / height)) * zoom;
  const w = width * scale, h = height * scale;
  const x = treatment === 'fit' ? (1080 - w) / 2 : (1080 - w) * photo.focal_x;
  const y = treatment === 'fit' ? (top + bottom - h) / 2 : lensPanel ? top+(bottom-top-h)*photo.focal_y : (canvasHeight - h) * photo.focal_y;
  return { x: x + (photo.offset_x ?? 0) * 1080, y: y + (photo.offset_y ?? 0) * canvasHeight, width: w, height: h, treatment };
}

export const resetFraming = { zoom: 1, offset_x: 0, offset_y: 0, focal_x: .5, focal_y: .5 } as const;

export function framingSettings(photo: ReelPhoto): Partial<ReelPhoto> {
  return { framing: photo.framing ?? 'inherit', zoom: photo.zoom ?? 1, offset_x: photo.offset_x ?? 0, offset_y: photo.offset_y ?? 0, focal_x: photo.focal_x, focal_y: photo.focal_y };
}


export function reelDimensions(spec: Pick<ReelComposition, 'aspect_ratio' | 'resolution'>, preview = false): [number, number] {
  const width = preview ? 360 : spec.resolution ?? 1080;
  const height = {'9:16':1920, '4:5':1350, '1:1':1080}[spec.aspect_ratio ?? '9:16'];
  return [width, Math.round(height * width / 1080)];
}

/** Store each ratio's placement; crops and motion stay attached to the source photo. */
export function switchReelRatio(value: ReelComposition, ratio: ReelAspectRatio): Partial<ReelComposition> {
  const previous = value.aspect_ratio ?? '9:16';
  if (previous === ratio) return {};
  const source = value.photos?.length ? value.photos : value.image_id
    ? [{image_id:value.image_id, movement:'auto' as const, focal_x:value.focal_x, focal_y:value.focal_y}] : [];
  const photos = source.map(photo => {
    const framing_by_ratio = {...photo.framing_by_ratio, [previous]:framingSettings(photo) as SavedPhotoFraming};
    const framing = framing_by_ratio[ratio] ?? {...resetFraming, framing:'fit' as const};
    return {...photo, ...framing, framing_by_ratio};
  });
  return {aspect_ratio:ratio, ...(photos.length ? {version:value.template === 'lens_report' ? 3 : 2, photos, image_id:photos[0].image_id} : {})};
}
