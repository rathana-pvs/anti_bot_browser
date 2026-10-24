"""Lens Report: versioned photographic and animated editorial layouts for both ratios."""
from functools import lru_cache
import json
import shutil

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from .composition import FONTS, fit_text, draw_lines
from .daily_brief import runtime_dir, runtime_status as base_runtime_status
from .framing import crop_image, transformed_photo
from .layout import canvas_size, output_size
from .story import photos_for, timeline


@lru_cache(maxsize=1)
def serif_data():
    path = FONTS / 'SourceSerif4-It.ttf'
    with TTFont(path) as font:
        return path, set(font.getBestCmap())


def runtime_status(revision=2):
    ready, reason = base_runtime_status()
    if not ready:
        return False, reason.replace('Daily Brief', 'Lens Report')
    try:
        manifest = json.loads((runtime_dir() / 'app-runtime.json').read_text())
        if revision not in manifest.get('lens_report_revisions', [manifest.get('lens_report')]):
            raise ValueError('Unsupported layout revision')
        serif_data()
    except (OSError, ValueError, KeyError):
        return False, 'Prepare Lens Report: in tools/news-reel run npm run prepare:app.'
    return True, ''


def validate(spec):
    if spec.version != 3 or spec.layout_revision not in (1, 2):
        raise ValueError('Lens Report requires composition version 3, a supported layout revision.')
    if spec.aspect_ratio not in ('9:16', '4:5'):
        raise ValueError('Lens Report supports 9:16 and 4:5. Choose one of these ratios.')
    if not spec.source:
        raise ValueError('Add source attribution before using Lens Report.')
    for scene in timeline(spec):
        photo = scene['photo']
        title = photo.headline if photo.headline is not None else spec.headline
        body = (photo.caption if photo.caption is not None else spec.supporting) if spec.show_subtitle else ''
        if len((title+' '+body).split()) > (scene['hold']/spec.fps-(.6 if spec.layout_revision == 2 and spec.motion and spec.text_animation != 'none' else 0))*3:
            raise ValueError('Lens Report needs more reading time. Shorten the photo title/caption, or choose a longer video with Fit video timing.')


def serif_fit(text, width, max_height):
    path, supported = serif_data()
    if any(ord(c) not in supported and c not in '\n' for c in text):
        raise ValueError('Lens Report uses an English editorial font. Remove unsupported characters.')
    measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    for size in range(88, 49, -2):
        font = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
        lines = []
        fits = True
        for paragraph in text.split('\n'):
            line = ''
            for word in paragraph.split():
                candidate = f'{line} {word}'.strip()
                if measure.textlength(candidate, font=font) <= width - 24:
                    line = candidate
                elif line and measure.textlength(word, font=font) <= width - 24:
                    lines.append(line); line = word
                else:
                    fits = False; break
            if not fits:
                break
            lines.append(line)
        if fits and len(lines) <= 3 and len(lines)*size*1.2 <= max_height:
            return lines, font, round(size*1.2)
    raise ValueError('Lens Report headline is too long. Shorten it or add a shorter photo headline.')


def motion_for(spec, photo):
    from .flexible_scene import enabled, movement_for
    if enabled(spec):return movement_for(spec,photo)
    treatment = photo.framing if photo.framing != 'inherit' else spec.treatment
    # Never zoom fitted artwork or manually positioned subjects out of the canvas.
    if not spec.motion or treatment != 'fill' or photo.zoom != 1 or photo.offset_x or photo.offset_y:
        return 'still'
    movement = photo.movement if photo.movement != 'auto' else spec.slideshow_effect
    return 'zoom_in' if movement == 'auto' else movement if movement in ('zoom_in', 'zoom_out', 'pan_left', 'pan_right') else 'still'


def fit_lens_text(spec, title, supporting):
    """Canonical text geometry, shared by content checks and rendered layers."""
    _, height = canvas_size(spec)
    portrait = spec.aspect_ratio == '9:16'
    animated = spec.layout_revision == 2
    panel_top = (1180 if portrait else 740) if animated else height
    from .flexible_scene import enabled, panel_top as flexible_panel_top
    if enabled(spec):panel_top=flexible_panel_top(spec)
    # Separate geometry: the 4:5 version has a shallower, wider caption band.
    footer_y = height-180
    max_title_height = (300 if portrait else 234) if animated else (320 if portrait else 270)
    body_lines, body_size = fit_text(supporting, 912, 3 if portrait else 2, 42, 30, False) if supporting else ([], 42)
    body_height = len(body_lines)*round(body_size*1.55)
    if animated:
        max_title_height = min(max_title_height, footer_y-20-panel_top-42-(30+body_height if supporting else 0))
    lines, font, leading = serif_fit(title, 912, max_title_height)
    block_height = len(lines)*leading + (30+body_height if supporting else 0)
    first_baseline = footer_y - 50 - block_height + font.size
    if animated:
        first_baseline = panel_top+42+font.size
        if first_baseline-font.size+block_height > footer_y-20:
            raise ValueError('Lens Report text is too long for the caption panel. Shorten the photo title or caption.')
    if not animated and first_baseline < height*(.53 if portrait else .40):
        raise ValueError('Lens Report text fills too much of the photograph. Shorten the headline or caption.')
    return lines, font, leading, body_lines, body_size, first_baseline


def layers(spec, photo, path, logo=None, text_sprites=None):
    validate(spec)
    from .flexible_scene import enabled, layers as flexible_layers
    if enabled(spec):return flexible_layers(spec,photo,path,logo,text_sprites)
    width, height = canvas_size(spec)
    portrait = spec.aspect_ratio == '9:16'
    animated = spec.layout_revision == 2
    size = (width, height)
    dark = animated and spec.lens_theme == 'dark'
    paper = '#000000' if dark else '#f7f5ef'
    ink = '#f7f5ef' if dark else '#101318'
    muted = '#b8bbc2' if dark else '#62666d'
    border = '#34383f' if dark else '#d6d3cb'
    with Image.open(path) as raw:
        image = crop_image(raw.convert('RGB'), photo.crop)
    treatment = photo.framing if photo.framing != 'inherit' else spec.treatment
    compact_header = animated and not portrait
    photo_top = (128 if spec.brand or logo else 96) if compact_header else 210 if animated else 0
    panel_top = (1180 if portrait else 740) if animated else height
    photo_height = panel_top-photo_top
    scale = (min if treatment == 'fit' else max)(width/image.width, photo_height/image.height)*photo.zoom
    w, h = image.width*scale, image.height*scale
    x = (width-w)*(.5 if treatment == 'fit' else photo.focal_x)+photo.offset_x*width
    y = (photo_height-h)*(.5 if treatment == 'fit' else photo.focal_y)+photo.offset_y*height+photo_top
    foreground = transformed_photo(image, (x,y,w,h), size)
    background = ImageOps.fit(image, (270, round(height/4))).filter(ImageFilter.GaussianBlur(16)).resize(size).convert('RGBA')
    background = Image.alpha_composite(background, Image.new('RGBA', size, (6,16,14,110)))
    picture = Image.alpha_composite(background, foreground)
    foreground.close(); background.close(); image.close()
    gradient = Image.new('RGBA', size)
    gd = ImageDraw.Draw(gradient)
    caption = Image.new('RGBA', size)
    brand = Image.new('RGBA', size)
    draw = ImageDraw.Draw(caption)
    title = photo.headline if photo.headline is not None else spec.headline
    supporting = (photo.caption if photo.caption is not None else spec.supporting) if spec.show_subtitle else ''
    footer_y = height-180
    lines, font, leading, body_lines, body_size, first_baseline = fit_lens_text(spec, title, supporting)
    # Tie the gradient to the actual text block, including long fitted titles.
    ramp_start = first_baseline-font.size-180
    for py in range(height):
        top = round(150*max(0,1-py/260))
        bottom = round(235*max(0,min(1,(py-ramp_start)/300)))
        gd.line((0,py,width,py),fill=(5,17,14,max(top,bottom)))
    if animated:
        gd.rectangle((0,0,width,photo_top),fill=paper)
        gd.rectangle((0,panel_top,width,height),fill=paper)
        gd.line((72,photo_top-14,1008,photo_top-14),fill=border,width=2)
    title_color = ink if animated else '#f5f0e4'
    body_color = muted if animated else '#eee9df'
    def collect(layer, kind, index):
        caption.alpha_composite(layer)
        if text_sprites is not None:
            bounds = layer.getbbox()
            if bounds:
                text_sprites.append(dict(image=layer.crop(bounds),x=bounds[0],y=bounds[1],kind=kind,index=index))
        layer.close()
    for index,line in enumerate(lines):
        sprite=Image.new('RGBA',size)
        ImageDraw.Draw(sprite).text((72,first_baseline),line,font=font,fill=title_color,anchor='ls')
        collect(sprite,'title',index)
        first_baseline += leading
    if supporting:
        baseline=first_baseline+20
        for index,line in enumerate(body_lines):
            sprite=Image.new('RGBA',size)
            draw_lines(ImageDraw.Draw(sprite),(72,baseline),[line],body_size,body_color,False)
            collect(sprite,'body',index)
            baseline+=round(body_size*1.55)
    bd = ImageDraw.Draw(brand)
    brand_x = 72
    if logo:
        with Image.open(logo) as raw:
            mark = ImageOps.contain(raw.convert('RGBA'),(48,48) if compact_header else (64,64))
        brand.alpha_composite(mark,(72,14 if compact_header else 75));brand_x=138 if compact_header else 158
    for value,xy,available,preferred,minimum,color,line_limit in (
        (spec.brand,(brand_x,60 if compact_header else 121),1008-brand_x,28,22,ink if animated else '#f5f0e4',1),
        (spec.category,(72,photo_top-30 if compact_header else 174),912,22,18,spec.accent if animated else '#e3d5b5',1),
        (spec.source,(72,footer_y+54),912,26,22,muted if animated else '#eee9df',2)):
        if value:
            fitted, fs = fit_text(value,available,line_limit,preferred,minimum,False)
            draw_lines(bd,xy,fitted,fs,color,False)
    bd.line((72,footer_y,1008,footer_y), fill=border if animated else (238,233,223,130), width=2)
    if animated and spec.show_progress:
        gd.rectangle((0,height-6,width,height),fill=border)
    return picture, gradient, caption, brand


def poster(spec, paths, logo=None, photo_index=0):
    photos = photos_for(spec)
    if not 0 <= photo_index < len(photos):
        raise ValueError('Choose an available photo.')
    prepared = layers(spec,photos[photo_index],paths[photo_index],logo)
    combined = prepared[0].copy()
    for layer in prepared[1:]:
        combined = Image.alpha_composite(combined,layer)
    for layer in prepared:layer.close()
    return combined.convert('RGB').resize((540,canvas_size(spec)[1]//2),Image.Resampling.LANCZOS), []


def prepare(spec, paths, logo, directory, preview=False, ffmpeg='ffmpeg'):
    ready, reason = runtime_status(spec.layout_revision)
    if not ready:raise ValueError(reason)
    from .flexible_scene import enabled, prepare_photo
    from .daily_brief import require_flexible_runtime
    if enabled(spec):require_flexible_runtime()
    entries, movements, sprite_entries, photo_entries = [], [], [], []
    size = output_size(spec,preview)
    for index,(photo,path) in enumerate(zip(photos_for(spec),paths)):
        names = []
        sprites = []
        prepared = layers(spec,photo,path,logo,text_sprites=sprites if spec.layout_revision == 2 else None)
        for layer_index,image in enumerate(prepared):
            name=f'lens-{index}-{layer_index}.png'
            image.resize(size,Image.Resampling.LANCZOS).save(directory/name)
            image.close();names.append(name)
        entries.append(names);movements.append(motion_for(spec,photo))
        if enabled(spec):photo_entries.append(prepare_photo(spec,photo,path,directory,index,preview))
        records = []
        for sprite_index,sprite in enumerate(sprites):
            name=f'lens-{index}-text-{sprite_index}.png'
            scale=size[0]/1080
            sprite_size=(max(1,round(sprite['image'].width*scale)),max(1,round(sprite['image'].height*scale)))
            sprite['image'].resize(sprite_size,Image.Resampling.LANCZOS).save(directory/name)
            sprite['image'].close()
            records.append(dict(file=name,x=round(sprite['x']*scale),y=round(sprite['y']*scale),width=sprite_size[0],height=sprite_size[1],kind=sprite['kind'],index=sprite['index']))
        sprite_entries.append(records)
    transition = round(.45*spec.fps) if spec.motion and spec.transition=='fade' else 0
    scenes = timeline(spec)
    payload = dict(template='lens_report',layoutRevision=spec.layout_revision,width=size[0],height=size[1],fps=spec.fps,
                   durationInFrames=spec.duration*spec.fps,motion=spec.motion,accent=spec.accent,
                   images=entries,movements=movements,transitionFrames=transition,
                   scenes=[dict(fromFrame=s['start'],hold=s['hold'],frames=s['hold']+(transition if i<len(scenes)-1 else 0),imageIndex=s['photo_index']) for i,s in enumerate(scenes)])
    if spec.layout_revision == 2:
        payload.update(textSprites=sprite_entries,textAnimation=spec.text_animation,showProgress=spec.show_progress,progressTrack='#34383f' if spec.lens_theme == 'dark' else '#d6d3cb')
    if enabled(spec):payload.update(flexibleScene=True,photoLayers=photo_entries,motionIntensity=spec.motion_intensity)
    manifest=directory/'lens-report.json';manifest.write_text(json.dumps(payload))
    return [shutil.which('node'),str(runtime_dir()/'app-render.mjs'),str(manifest),str(directory/'remotion.mp4'),ffmpeg]
