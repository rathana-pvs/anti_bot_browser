"""Daily Brief layout; prepared layers are shared by posters and Remotion."""
import json
import os
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageOps
from .composition import fit_text, draw_lines, accent_ink
from .framing import crop_image, transformed_photo
from .story import photos_for, timeline
from .layout import output_size


def runtime_dir():
    configured = os.environ.get('REEL_REMOTION_DIR')
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[3] / 'tools' / 'news-reel'


def runtime_status():
    root = runtime_dir()
    if not shutil.which('node'):
        return False, 'Install Node.js 22+ and prepare the Daily Brief runtime.'
    if not all((root / name).is_file() for name in ('app-render.mjs', 'app-bundle/index.html', 'browser.json', 'node_modules/@remotion/renderer/package.json')):
        return False, 'Prepare Daily Brief: in tools/news-reel run npm ci, then npm run prepare:app.'
    try:
        browser = Path(json.loads((root / 'browser.json').read_text())['executablePath'])
        if not browser.is_file():
            raise ValueError('Missing browser')
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'Run npm run prepare:app again to restore the Daily Brief browser.'
    return True, ''


def require_flexible_runtime():
    try:
        data=json.loads((runtime_dir()/'app-runtime.json').read_text())
        if data.get('flexible_scenes')!=1:raise ValueError()
    except (OSError,ValueError,TypeError):
        raise ValueError('Prepare flexible scenes: in tools/news-reel run npm run prepare:app.') from None


def validate(spec):
    from .flexible_scene import enabled
    if spec.aspect_ratio != '9:16' and not (enabled(spec) and spec.aspect_ratio=='4:5'):
        raise ValueError('Daily Brief uses a 9:16 vertical canvas. Choose 9:16 or another layout.')
    if spec.version != 2:
        raise ValueError('Enable story motion before selecting Daily Brief.')
    if not spec.source:
        raise ValueError('Add source attribution before using Daily Brief.')


def layers(spec, photo, path, logo=None):
    validate(spec)
    from .flexible_scene import enabled, layers as flexible_layers
    if enabled(spec):return flexible_layers(spec,photo,path,logo)
    size = (1080, 1920)
    base = Image.new('RGBA', size, '#0b171e')
    image_layer = Image.new('RGBA', size)
    text = Image.new('RGBA', size)
    draw = ImageDraw.Draw(base)
    # Preserve crop, fit/fill, focal point, zoom and offsets inside the card.
    with Image.open(path) as raw:
        image = crop_image(raw.convert('RGB'), photo.crop)
    treatment = photo.framing if photo.framing != 'inherit' else spec.treatment
    factor = (min if treatment == 'fit' else max)(912/image.width, 690/image.height) * photo.zoom
    w, h = image.width*factor, image.height*factor
    x = (912-w)*(0.5 if treatment == 'fit' else photo.focal_x) + photo.offset_x*912
    y = (690-h)*(0.5 if treatment == 'fit' else photo.focal_y) + photo.offset_y*690
    card = transformed_photo(image, (x, y, w, h), (912, 690))
    image_layer.alpha_composite(card, (72, 250))
    card.close()
    image.close()
    draw.rounded_rectangle((72, 250, 984, 940), radius=24, fill='#172b33')
    brand_x = 72
    if logo:
        with Image.open(logo) as raw:
            mark = ImageOps.contain(raw.convert('RGBA'), (64, 64))
        base.alpha_composite(mark, (72, 114))
        brand_x = 152
    def line(canvas, value, xy, width, preferred, minimum, count=1, color='white', bold=True):
        if not value:
            return xy[1]
        lines, font_size = fit_text(value, width, count, preferred, minimum, bold)
        return draw_lines(ImageDraw.Draw(canvas), xy, lines, font_size, color, bold)
    line(base, spec.brand, (brand_x, 162), 590-(brand_x-72), 28, 20)
    if spec.category:
        draw.rounded_rectangle((690, 118, 984, 174), radius=4, fill=spec.accent)
        line(base, spec.category, (704, 154), 266, 22, 16, color=accent_ink(spec.accent))
    draw.line((72, 202, 984, 202), fill='#3a4b52', width=2)
    # Supporting copy reserves its own bounded area; overflow fails explicitly.
    next_line = line(text, photo.headline if photo.headline is not None else spec.headline,
                     (72, 1110), 912, 78, 48, 3)
    accent_y = min(next_line + 20, 1430)
    ImageDraw.Draw(text).rectangle((72, accent_y, 140, accent_y + 5), fill=spec.accent)
    if spec.show_subtitle:
        line(text, spec.supporting, (72, accent_y + 65), 912, 42, 28, 3, '#c2cdd3', False)
    draw.line((72, 1690, 984, 1690), fill='#3a4b52', width=2)
    line(base, spec.source, (72, 1740), 912, 24, 20, 2, '#c2cdd3', False)
    return base, image_layer, text


def poster(spec, paths, logo=None, photo_index=0):
    photos = photos_for(spec)
    if photo_index < 0 or photo_index >= len(photos):
        raise ValueError('Choose an available photo.')
    prepared=layers(spec,photos[photo_index],paths[photo_index],logo)
    combined=prepared[0].copy()
    for layer in prepared[1:]:combined.alpha_composite(layer)
    for layer in prepared:layer.close()
    from .layout import canvas_size
    return combined.convert('RGB').resize((540,canvas_size(spec)[1]//2),Image.Resampling.LANCZOS), []


def prepare(spec, paths, logo, directory, preview=False, ffmpeg='ffmpeg'):
    validate(spec)
    ready, reason = runtime_status()
    if not ready:
        raise ValueError(reason)
    from .flexible_scene import enabled
    if enabled(spec):return prepare_flexible(spec,paths,logo,directory,preview,ffmpeg)
    entries = []
    size = output_size(spec, preview)
    for index, (photo, path) in enumerate(zip(photos_for(spec), paths)):
        prepared = layers(spec, photo, path, logo)
        names = []
        for label, image in zip(('base', 'photo', 'text'), prepared):
            name = f'daily-{index}-{label}.png'
            image.resize(size, Image.Resampling.LANCZOS).save(directory / name)
            image.close()
            names.append(name)
        entries.append(names)
    payload = dict(width=size[0], height=size[1], fps=spec.fps, durationInFrames=spec.duration*spec.fps,
                   motion=spec.motion, accent=spec.accent, images=entries,
                   scenes=[dict(fromFrame=s['start'], frames=s['hold'], imageIndex=s['photo_index']) for s in timeline(spec)])
    manifest = directory / 'daily-brief.json'
    manifest.write_text(json.dumps(payload))
    return [shutil.which('node'), str(runtime_dir() / 'app-render.mjs'), str(manifest), str(directory / 'remotion.mp4'), ffmpeg]


def prepare_flexible(spec,paths,logo,directory,preview,ffmpeg):
    from .flexible_scene import layers, prepare_photo, save_sprites, movement_for
    require_flexible_runtime()
    entries=[];photo_entries=[];sprite_entries=[];movements=[]
    size=output_size(spec,preview)
    for index,(photo,path) in enumerate(zip(photos_for(spec),paths)):
        sprites=[];prepared=layers(spec,photo,path,logo,sprites);names=[]
        for layer_index,picture in enumerate(prepared):
            name=f'daily-flex-{index}-{layer_index}.png'
            picture.resize(size,Image.Resampling.LANCZOS).save(directory/name);picture.close();names.append(name)
        entries.append(names);sprite_entries.append(save_sprites(sprites,spec,directory,index,preview))
        photo_entries.append(prepare_photo(spec,photo,path,directory,index,preview));movements.append(movement_for(spec,photo))
    transition=round(.45*spec.fps) if spec.motion and spec.transition=='fade' else 0
    scenes=timeline(spec)
    payload=dict(template='daily_brief',flexibleScene=True,width=size[0],height=size[1],fps=spec.fps,
                 durationInFrames=spec.duration*spec.fps,motion=spec.motion,accent=spec.accent,images=entries,
                 photoLayers=photo_entries,textSprites=sprite_entries,textAnimation=spec.text_animation,
                 movements=movements,motionIntensity=spec.motion_intensity,showProgress=spec.show_progress,
                 progressTrack='#34383f' if spec.lens_theme=='dark' else '#d6d3cb',transitionFrames=transition,
                 scenes=[dict(fromFrame=s['start'],hold=s['hold'],frames=s['hold']+(transition if i<len(scenes)-1 else 0),imageIndex=s['photo_index']) for i,s in enumerate(scenes)])
    manifest=directory/'daily-brief.json';manifest.write_text(json.dumps(payload))
    return [shutil.which('node'),str(runtime_dir()/'app-render.mjs'),str(manifest),str(directory/'remotion.mp4'),ffmpeg]


def finish_command(ffmpeg, video, output, spec, audio=None):
    command = [ffmpeg, '-v', 'error', '-nostdin', '-i', str(video)]
    if audio:
        command += ['-i', str(audio)]
    command += ['-map', '0:v:0', '-c:v', 'copy']
    if audio:
        command += ['-map', '1:a:0', '-af',
                    f'volume={1 if spec.narration_enabled else spec.volume},apad,afade=t=out:st={max(0,spec.duration-1)}:d=1',
                    '-c:a', 'aac', '-b:a', '128k']
    else:
        command += ['-an']
    return command + ['-t', str(spec.duration), '-movflags', '+faststart', '-y', str(output)]
