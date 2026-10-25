"""Version-2 scene timing and independent photo/text motion. Legacy stays unchanged."""
from pathlib import Path
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps
from .composition import WIDTH, HEIGHT, cover, fit_text, draw_lines, text_width, output_size, fit_news_text, draw_headline, accent_ink, news_brand
from .models import Photo
from .photo_timing import photo_seconds
from .layout import canvas_size, layout_y, type_scale
from .framing import photo_bounds, transformed_photo, crop_image

def photos_for(spec):
    return spec.photos or ([Photo(image_id=spec.image_id, focal_x=spec.focal_x, focal_y=spec.focal_y)] if spec.image_id else [])


# Shared by preview and export. Explicit photo/text overrides still take precedence.
MOTION_PROFILES = {
    'bulletin': dict(fit_amount=.065, fill_amount=.11, transition=.22, entrance=.24),
    'politics': dict(fit_amount=.015, fill_amount=.025, transition=.75, entrance=.9),
    'story': dict(fit_amount=.085, fill_amount=.15, transition=.95, entrance=.65),
}


def transition_frames(spec):
    return round(MOTION_PROFILES[spec.motion_style]['transition'] * spec.fps) if spec.motion and spec.transition == 'fade' else 0


def text_cue(spec, name, scenes):
    if name not in ('category', 'headline') and not name.startswith('highlight'):
        return None
    if name.startswith('highlight'):
        index = int(name.removeprefix('highlight'))
        if spec.motion_style == 'bulletin': return .65 + index * .6
        if spec.motion_style == 'politics': return 1.5 + index * 1.3
        # Keep the hook visible; bring supporting facts in on photo changes.
        beat = scenes[index]['start'] / spec.fps if index < len(scenes) else index * spec.duration / (index + 1)
        return max(.9, beat + .35)
    return {'bulletin': {'category': .03, 'headline': .12},
            'politics': {'category': .15, 'headline': .45},
            'story': {'category': .05, 'headline': .05}}[spec.motion_style][name]


def timeline(spec):
    photos = photos_for(spec)
    fps = spec.fps
    total = spec.duration * fps
    if spec.auto_timing and photos:
        holds = [seconds * fps for seconds in photo_seconds(spec, photos)]
        result, start = [], 0
        transition = transition_frames(spec)
        for index, (photo, hold) in enumerate(zip(photos, holds)):
            overlap = transition if index < len(photos) - 1 else 0
            result.append(dict(photo=photo, photo_index=index, start=start, hold=hold,
                               frames=hold + overlap, transition=transition))
            start += hold
        return result
    if spec.slide_seconds is not None and photos:
        hold = spec.slide_seconds * fps
        starts = range(0, total, hold)
        transition = transition_frames(spec)
        result = []
        for index, start in enumerate(starts):
            duration = min(hold, total - start)
            overlap = min(transition, total - start - duration)
            result.append(dict(photo=photos[index % len(photos)], photo_index=index % len(photos),
                               start=start, hold=duration, frames=duration + overlap, transition=transition))
        return result
    transition = transition_frames(spec) if len(photos) > 1 else 0
    base, remainder = divmod(total, max(1, len(photos)))
    result, start = [], 0
    for index, photo in enumerate(photos):
        hold = base + (index < remainder)
        frames = hold + (transition if index < len(photos) - 1 else 0)
        result.append(dict(photo=photo, photo_index=index, start=start, hold=hold, frames=frames, transition=transition))
        start += hold
    return result


def photo_frame(spec, photo, path):
    WIDTH, HEIGHT = canvas_size(spec)
    with Image.open(path) as raw:
        image = crop_image(raw.convert('RGB'), photo.crop)
    treatment, bounds = photo_bounds(spec, photo, image.size)
    background = cover(image, photo.focal_x, photo.focal_y, (WIDTH, HEIGHT))
    warnings = []
    if treatment == 'fit' or photo.zoom < 1 or photo.offset_x or photo.offset_y:
        background = background.resize((270, 480)).filter(ImageFilter.GaussianBlur(18)).resize((WIDTH, HEIGHT))
        background = Image.alpha_composite(background, Image.new('RGBA', background.size, (9, 12, 18, 150)))
        if spec.template in ('editorial', 'economist'):
            background = Image.new('RGBA', (WIDTH, HEIGHT), '#0e1622')
    if treatment == 'fit':
        x, y, w, h = bounds
        draw = ImageDraw.Draw(background)
        draw.rounded_rectangle((x - 8, y - 8, x + w + 8, y + h + 8), radius=10, fill=(255, 255, 255, 28))
        if h < 350:
            warnings.append('This wide photo may be difficult to read on a phone. Check the video.')
    if treatment == 'fit' or photo.zoom != 1 or photo.offset_x or photo.offset_y:
        foreground = transformed_photo(image, bounds, (WIDTH, HEIGHT))
        background = Image.alpha_composite(background, foreground)
    return background.convert('RGB'), warnings


def overlays(spec, logo_path=None):
    """Full-size canvases keep the same geometry in posters, previews and exports."""
    WIDTH, HEIGHT = canvas_size(spec)
    Y = lambda value: layout_y(spec, value)
    def layer(): return Image.new('RGBA', (WIDTH, HEIGHT))
    panel, category, headline, brand = layer(), layer(), layer(), layer()
    accent = ImageColor.getrgb(spec.accent)
    has_text = bool(spec.headline or (spec.show_subtitle and spec.supporting) or spec.category)
    draw = ImageDraw.Draw(panel)
    if has_text:
        if spec.template in ('editorial', 'economist'):
            draw.rectangle((0, Y(1250), WIDTH, HEIGHT), fill=(13, 19, 29, 255))
            draw.rectangle((0, Y(1250), WIDTH, Y(1262)), fill=accent)
        elif spec.template == 'ap_wire':
            for y in range(Y(1100), HEIGHT):
                draw.line((0, y, WIDTH, y), fill=(8, 10, 14, min(245, round((y - Y(1100)) / Y(380) * 245))))
        elif spec.template in ('reuters', 'brief'):
            for y in range(Y(1100), HEIGHT):
                draw.line((0, y, WIDTH, y), fill=(9, 13, 19, min(240, round((y - Y(1100)) / Y(400) * 240))))
        else:
            for y in range(Y(1120), HEIGHT):
                draw.line((0, y, WIDTH, y), fill=(7, 10, 16, min(245, round((y - Y(1120)) / Y(400) * 245))))
    elif spec.source:
        for y in range(Y(1740), HEIGHT):
            draw.line((0, y, WIDTH, y), fill=(5, 8, 13, round((y - Y(1740)) / Y(180) * 210)))
    y = Y(1430 if spec.category else 1390)
    if spec.category:
        lines, size = fit_text(spec.category, 870, 1, round(30 * type_scale(spec)), round(24 * type_scale(spec)))
        cd = ImageDraw.Draw(category)
        width = round(text_width(lines[0], size)) + 36
        if spec.template == 'ap_wire':
            cd.rounded_rectangle((64, Y(1286), 64 + width, Y(1341)), radius=4, fill=(217, 35, 42, 255))
            draw_lines(cd, (82, Y(1324)), lines, size, '#fef08a')
        elif spec.template in ('reuters', 'brief'):
            cd.rounded_rectangle((64, Y(1286), 64 + width, Y(1341)), radius=4, fill=(20, 26, 36, 240), outline='#ff8000', width=2)
            draw_lines(cd, (82, Y(1324)), lines, size, '#ff8000')
        elif spec.template in ('economist', 'editorial'):
            cd.rounded_rectangle((64, Y(1286), 64 + width, Y(1341)), radius=4, fill=accent)
            draw_lines(cd, (82, Y(1324)), lines, size, 'white')
        else:
            cd.rounded_rectangle((64, Y(1286), 64 + width, Y(1341)), radius=6, fill=accent)
            draw_lines(cd, (82, Y(1324)), lines, size, accent_ink(spec.accent) if spec.news_design == 'clean' else 'white')
    hd = ImageDraw.Draw(headline)
    if has_text:
        accent_bar = ImageColor.getrgb('#ff8000') if spec.template in ('reuters', 'brief') else accent
        hd.rounded_rectangle((64, y - Y(48), 71, y + Y(30)), radius=3, fill=accent_bar)
    headline_lines, headline_size, subtitle_lines, subtitle_size = fit_news_text(spec)
    if headline_lines:
        y = draw_headline(hd, (92, y), headline_lines, headline_size, spec)
    highlights = []
    if subtitle_lines:
        y += 10
        # A wrapped sentence is one fact, not two independently timed fragments.
        groups = [[line] for line in subtitle_lines] if '\n' in spec.supporting else [subtitle_lines]
        for group in groups:
            image = layer()
            y = draw_lines(ImageDraw.Draw(image), (92, y), group, subtitle_size, '#d5dbe4', False)
            highlights.append(image)
    if spec.news_design == 'clean':
        brand = news_brand(spec, logo_path)
    else:
        bd = ImageDraw.Draw(brand)
        if spec.brand or logo_path:
            for yy in range(Y(200)):
                bd.line((0, yy, WIDTH, yy), fill=(5, 8, 13, round(170 * (1 - yy / Y(200)))))
            bd.line((64, Y(181), 1016, Y(181)), fill=(*accent, 210), width=3)
        brand_x = 64
        if logo_path:
            with Image.open(logo_path) as raw:
                logo = ImageOps.contain(raw.convert('RGBA'), (Y(82), Y(82)), Image.Resampling.LANCZOS)
            brand.alpha_composite(logo, (64, Y(78))); brand_x = 165
        if spec.brand:
            lines, size = fit_text(spec.brand, 900 - (brand_x - 64), 1, round(32 * type_scale(spec)), round(26 * type_scale(spec)))
            draw_lines(bd, (brand_x, Y(131)), lines, size)
        if spec.source:
            lines, size = fit_text(spec.source, 950, 1, 26, 22, False)
            draw_lines(bd, (64, Y(1842)), lines, size, '#c4cbd5', False)
    return [('panel', panel), ('category', category), ('headline', headline),
            *[(f'highlight{i}', image) for i, image in enumerate(highlights)], ('brand', brand)]


def photo_spec(spec, photo):
    return spec.model_copy(update={'headline': photo.headline}) if photo.headline is not None else spec


def story_poster(spec, paths, logo_path=None, photo_index=0):
    photos = photos_for(spec)
    if not 0 <= photo_index < len(photos):
        raise ValueError('Choose an available photo for the layout preview.')
    image, warnings = photo_frame(spec, photos[photo_index], paths[photo_index])
    combined = image.convert('RGBA')
    for _, canvas in overlays(photo_spec(spec, photos[photo_index]), logo_path):
        combined = Image.alpha_composite(combined, canvas)
    if spec.show_progress:
        cw, ch = canvas_size(spec)
        prog_h = max(6, layout_y(spec, 10))
        prog_y = 0 if getattr(spec, 'progress_position', 'top') == 'top' else (ch - prog_h)
        accent_rgb = ImageColor.getrgb(spec.accent)
        prog_draw = ImageDraw.Draw(combined)
        prog_draw.rectangle((0, prog_y, cw, prog_y + prog_h), fill=(0, 0, 0, 140))
        prog_draw.rectangle((0, prog_y, round(cw * 0.35), prog_y + prog_h), fill=(*accent_rgb, 240))
    return combined.convert('RGB').resize((540, combined.height // 2), Image.Resampling.LANCZOS), warnings


def save_story(spec, paths, logo_path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    images, warnings, text = [], [], []
    for i, (photo, source) in enumerate(zip(photos_for(spec), paths)):
        image, notes = photo_frame(spec, photo, source)
        path = directory / f'photo-{i}.png'; image.save(path); image.close()
        images.append(path)
        warnings.extend(f'Photo {i + 1}: {note}' for note in notes)
    photos = photos_for(spec)
    if any(photo.headline is not None for photo in photos):
        # At most five extra canvases; repeated scenes reuse their photo's text.
        for i, photo in enumerate(photos):
            scene_text = Image.new('RGBA', canvas_size(spec))
            for name, canvas in overlays(photo_spec(spec, photo), logo_path):
                if name in ('category', 'brand'):
                    if i == 0:
                        path = directory / f'{name}.png'; canvas.save(path)
                        text.append((name, path))
                else:
                    scene_text = Image.alpha_composite(scene_text, canvas)
                canvas.close()
            path = directory / f'scene_text{i}.png'; scene_text.save(path); scene_text.close()
            text.append((f'scene_text{i}', path))
    else:
        for name, canvas in overlays(spec, logo_path):
            path = directory / f'{name}.png'; canvas.save(path); canvas.close()
            text.append((name, path))
    return images, text, warnings


def movement_for(spec, photo, index):
    if not spec.motion: return 'still'
    if photo.movement != 'auto': return photo.movement
    if spec.slideshow_effect != 'auto':
        return 'still' if spec.slideshow_effect == 'fade' else spec.slideshow_effect
    if spec.motion_style == 'politics': return 'zoom_out'
    if spec.motion_style == 'story': return ('pan_right', 'pan_left', 'zoom_in', 'zoom_out')[index % 4]
    return 'zoom_in'


def entrance_position(animation, start, duration, width, height, category=False):
    offset = f'pow(1-min(max((t-{start})/{duration},0),1),3)'
    if animation in ('slide_left', 'slide_right'):
        distance = (110 if category else 64) * width / WIDTH
        return f'{-distance if animation == "slide_left" else distance}*{offset}', '0'
    if animation in ('slide', 'slide_down'):
        distance = 36 * height / HEIGHT
        return '0', f'{-distance if animation == "slide_down" else distance}*{offset}'
    return '0', '0'


def story_command(ffmpeg, images, text, output, spec, audio=None, preview=False):
    width, height = output_size(spec, preview)
    fps = spec.fps
    profile = MOTION_PROFILES[spec.motion_style]
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-filter_complex_threads', '1']
    for path in [*images, *[path for _, path in text]]:
        cmd += ['-threads', '1', '-framerate', str(fps), '-i', str(path)]
    if audio: cmd += ['-protocol_whitelist', 'file,pipe', '-i', str(audio)]
    filters = []
    scenes = timeline(spec)
    text_animation = 'none' if not spec.motion else spec.text_animation
    if text_animation == 'auto': text_animation = 'slide_left' if spec.motion_style == 'bulletin' else 'fade'
    scene_text_inputs = {int(name.removeprefix('scene_text')): len(images) + j
                         for j, (name, _) in enumerate(text) if name.startswith('scene_text')}
    # Repeated photos share a decoded input; each cycle has its own animation.
    for photo_index in range(len(images)):
        uses = [i for i, scene in enumerate(scenes) if scene['photo_index'] == photo_index]
        if uses:
            filters.append(f'[{photo_index}:v]split={len(uses)}' + ''.join(f'[input{i}]' for i in uses))
            if photo_index in scene_text_inputs:
                filters.append(f'[{scene_text_inputs[photo_index]}:v]split={len(uses)}' + ''.join(f'[textinput{i}]' for i in uses))
    for i, scene in enumerate(scenes):
        move = movement_for(spec, scene['photo'], i)
        cycle_frames = scene['hold'] if spec.slide_seconds is not None else scene['frames']
        progress = f'min(on/{max(1, cycle_frames - 1)},1)'
        eased = (f'1-pow(1-min(({progress})/0.6,1),3)' if spec.motion_style == 'bulletin'
                 else f'(1-cos(PI*({progress})))/2')
        treatment = scene['photo'].framing if scene['photo'].framing != 'inherit' else spec.treatment
        amount = profile['fit_amount'] if treatment == 'fit' else profile['fill_amount']
        zoom = {'zoom_in': f'1+{amount}*({eased})', 'zoom_out': f'1+{amount}*(1-({eased}))'}.get(move, str(1 + amount))
        x = f'(iw-iw/zoom)*({eased})' if move == 'pan_right' else f'(iw-iw/zoom)*(1-({eased}))' if move == 'pan_left' else '(iw-iw/zoom)/2'
        motion = f"zoompan=z='{zoom}':x='{x}':y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps}" if move != 'still' else 'null'
        size = (width * 2, height * 2) if move != 'still' else (width, height)
        fade = ',fade=t=in:st=0:d=0.4' if spec.motion and spec.slideshow_effect == 'fade' else ''
        custom_text = scene['photo_index'] in scene_text_inputs
        photo_label = f'photo{i}' if custom_text else f'p{i}'
        filters.append(f'[input{i}]scale={size[0]}:{size[1]},loop=loop=-1:size=1:start=0,trim=end_frame={scene["frames"]},setpts=N/{fps}/TB,{motion}{fade},setsar=1,settb=1/{fps},format=yuv444p[{photo_label}]')
        if custom_text:
            entrance = profile['entrance']
            start = text_cue(spec, 'headline', scenes)
            text_fade = f',fade=t=in:st={start}:d={entrance}:alpha=1' if text_animation != 'none' else ''
            filters.append(f'[textinput{i}]scale={width}:{height},format=rgba,loop=loop=-1:size=1:start=0,trim=end_frame={scene["frames"]},setpts=N/{fps}/TB{text_fade}[scenetext{i}]')
            x, y = entrance_position(text_animation, start, entrance, width, height)
            filters.append(f"[{photo_label}][scenetext{i}]overlay=x='{x}':y='{y}':shortest=1:eval=frame[p{i}]")
    current = 'p0'
    if len(scenes) > 1:
        if scenes[0]['transition']:
            for i in range(1, len(scenes)):
                label = f'x{i}'
                overlap = min(scenes[i]['transition'], scenes[i]['hold'])
                filters.append(f'[{current}][p{i}]xfade=transition=fade:duration={overlap / fps}:offset={scenes[i]["start"] / fps}[{label}]')
                current = label
        else:
            filters.append(''.join(f'[p{i}]' for i in range(len(scenes))) + f'concat=n={len(scenes)}:v=1:a=0[sequence]')
            current = 'sequence'
    for j, (name, _) in enumerate(text):
        if name.startswith('scene_text'): continue
        index, label = len(images) + j, f'o{j}'
        start = text_cue(spec, name, scenes)
        entrance = profile['entrance']
        animated = start is not None and text_animation != 'none'
        layer_filter = f'[{index}:v]scale={width}:{height},format=rgba,loop=loop=-1:size=1:start=0,trim=end_frame={spec.duration * fps},setpts=N/{fps}/TB'
        if animated: layer_filter += f',fade=t=in:st={start}:d={entrance}:alpha=1'
        filters.append(layer_filter + f'[{label}]')
        x, y = '0', '0'
        if animated:
            x, y = entrance_position(text_animation, start, entrance, width, height, name == 'category')
        next_label = f'c{j}'
        filters.append(f"[{current}][{label}]overlay=x='{x}':y='{y}':shortest=1:eval=frame[{next_label}]")
        current = next_label
    if spec.show_progress:
        strip_height = max(5, round(height * 0.008))
        accent_color = spec.accent.replace('#', '0x')
        pos = getattr(spec, 'progress_position', 'top')
        prog_y = '0' if pos == 'top' else 'H-h'
        filters.append(f'color=c=0x000000@0.55:s={width}x{strip_height}:r={fps}:d={spec.duration}[track]')
        filters.append(f'color=c={accent_color}:s={width}x{strip_height}:r={fps}:d={spec.duration}[strip]')
        filters.append(f'[{current}][track]overlay=0:{prog_y}:shortest=1[wt]')
        filters.append(f"[wt][strip]overlay=x='-w+w*min(t/{spec.duration},1)':y={prog_y}:shortest=1[progress]")
        current = 'progress'
    filters.append(f'[{current}]format=yuv420p[v]')
    cmd += ['-filter_complex', ';'.join(filters), '-map', '[v]']
    if audio:
        audio_filter = 'apad' if spec.narration_enabled else f'volume={spec.volume},apad,afade=t=in:d=0.5,afade=t=out:st={spec.duration - 1}:d=1'
        cmd += ['-map', f'{len(images) + len(text)}:a:0', '-af', audio_filter, '-c:a', 'aac', '-b:a', '128k']
    else: cmd += ['-an']
    return cmd + ['-t', str(spec.duration), '-r', str(fps), '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '24' if preview else '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-progress', 'pipe:1', str(output)]
