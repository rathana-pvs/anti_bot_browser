"""One 1080p layout engine for posters, motion previews, and exports."""
from functools import lru_cache
from pathlib import Path
import re

from fontTools.ttLib import TTFont
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps, features

from .models import Composition
from .layout import canvas_size, output_size, layout_y, type_scale

WIDTH, HEIGHT = 1080, 1920
FONTS = Path(__file__).parent / 'fonts'


@lru_cache(maxsize=16)
def font_data(khmer=False, bold=True):
    name = ('NotoSansKhmer' if khmer else 'NotoSans') + ('-Bold.ttf' if bold else '-Regular.ttf')
    path = FONTS / name
    try:
        with TTFont(path) as font:
            cmap = set(font.getBestCmap())
    except OSError as exc:
        raise ValueError('Bundled fonts are missing. Run Settings → Setup and repair.') from exc
    return path, cmap


@lru_cache(maxsize=128)
def font_at(size, khmer=False, bold=True):
    return ImageFont.truetype(str(font_data(khmer, bold)[0]), size,
                              layout_engine=ImageFont.Layout.RAQM)


def runs(text, size, bold=True):
    if not features.check_feature('raqm'):
        raise ValueError('Text layout support is missing. Run Settings → Setup and repair.')
    groups = []
    for char in text:
        if char in ('\u200b', '\u200c', '\u200d'):
            if groups:
                groups[-1][0] += char
            continue
        khmer = '\u1780' <= char <= '\u17ff'
        if ord(char) not in font_data(khmer, bold)[1]:
            raise ValueError(f'The selected font cannot display {char!r}. Remove it or use an image containing that text.')
        if groups and groups[-1][1] == khmer:
            groups[-1][0] += char
        else:
            groups.append([char, khmer])
    return [(text, font_at(size, khmer, bold)) for text, khmer in groups]


def text_width(text, size, bold=True):
    draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    return sum(draw.textlength(t, font=f) for t, f in runs(text, size, bold))


def wrap(text, size, width, max_lines, bold=True):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split(' '):
            candidate = (line + ' ' + word).strip()
            if text_width(candidate, size, bold) <= width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                if text_width(word, size, bold) > width:
                    raise ValueError('Text is too wide. Add a manual line break or shorten the headline.')
                line = word
        lines.append(line)
    if len(lines) > max_lines:
        raise ValueError(f'Text needs more than {max_lines} lines. Shorten it or add suitable line breaks.')
    return lines


def fit_text(text, width, max_lines, preferred, minimum, bold=True):
    last_error = None
    for size in range(preferred, minimum - 1, -2):
        try:
            return wrap(text, size, width, max_lines, bold), size
        except ValueError as exc:
            last_error = exc
    raise last_error or ValueError('Text does not fit')


def draw_lines(draw, xy, lines, size, fill='white', bold=True):
    x, y = xy
    for line in lines:
        cursor = x
        for text, font in runs(line, size, bold):
            draw.text((cursor, y), text, font=font, fill=fill, anchor='ls')
            cursor += draw.textlength(text, font=font)
        y += round(size * 1.55)
    return y


def balanced_headline(text, size, width=908):
    lines = wrap(text, size, width, 3)
    if len(lines) < 2 or '\n' in text:
        return lines
    words = text.split()
    target = text_width(' '.join(words), size) / len(lines)

    @lru_cache(None)
    def arrange(start, remaining):
        if not remaining:
            return (0, []) if start == len(words) else (float('inf'), [])
        best = (float('inf'), [])
        for end in range(start + 1, len(words) - remaining + 2):
            line = ' '.join(words[start:end])
            length = text_width(line, size)
            if length > width:
                break
            cost, tail = arrange(end, remaining - 1)
            score = (length - target) ** 2 + cost
            if score < best[0]:
                best = (score, [line, *tail])
        return best
    return arrange(0, len(lines))[1] or lines


def accent_ink(accent):
    r, g, b = ImageColor.getrgb(accent)
    return '#111827' if .2126 * r + .7152 * g + .0722 * b > 160 else 'white'


@lru_cache(maxsize=32)
def serif_font_data():
    path = FONTS / 'SourceSerif4-It.ttf'
    try:
        with TTFont(path) as font:
            cmap = set(font.getBestCmap())
    except OSError:
        return None, set()
    return path, cmap


def can_render_serif(text):
    _, cmap = serif_font_data()
    return bool(cmap) and all(ord(c) in cmap or c in '\n\r\t ' for c in text)


def draw_headline(draw, xy, lines, size, spec):
    use_serif = spec.template in ('economist', 'editorial') and can_render_serif(' '.join(lines))
    serif_font = (
        ImageFont.truetype(str(serif_font_data()[0]), size, layout_engine=ImageFont.Layout.RAQM)
        if use_serif else None
    )
    if spec.news_design != 'clean' or not spec.headline_emphasis:
        if serif_font:
            x, y = xy
            for line in lines:
                draw.text((x, y), line, font=serif_font, fill='white', anchor='ls')
                y += round(size * 1.55)
            return y
        return draw_lines(draw, xy, lines, size)
    joined = ' '.join(lines)
    match = re.search(re.escape(' '.join(spec.headline_emphasis.split())), joined, re.IGNORECASE)
    if not match:
        if serif_font:
            x, y = xy
            for line in lines:
                draw.text((x, y), line, font=serif_font, fill='white', anchor='ls')
                y += round(size * 1.55)
            return y
        return draw_lines(draw, xy, lines, size)
    # A light tint remains legible on the dark headline panel for any brand color.
    accent = tuple(round(c * .55 + 255 * .45) for c in ImageColor.getrgb(spec.accent))
    x, y = xy
    position = 0
    for line in lines:
        cursor = x
        lo, hi = max(0, match.start() - position), min(len(line), match.end() - position)
        pieces = [(line, 'white')] if hi <= lo else [(line[:lo], 'white'), (line[lo:hi], accent), (line[hi:], 'white')]
        for piece, color in pieces:
            if piece:
                if serif_font:
                    draw.text((cursor, y), piece, font=serif_font, fill=color, anchor='ls')
                    cursor += draw.textlength(piece, font=serif_font)
                else:
                    draw_lines(draw, (cursor, y), [piece], size, color)
                    cursor += text_width(piece, size)
        y += round(size * 1.55)
        position += len(line) + 1
    return y


def news_brand(spec, logo_path=None):
    """One restrained masthead and source treatment for both render paths."""
    WIDTH, HEIGHT = canvas_size(spec)
    Y = lambda value: layout_y(spec, value)
    layer = Image.new('RGBA', (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(layer)
    if spec.brand or logo_path:
        for y in range(Y(230)):
            draw.line((0, y, WIDTH, y), fill=(5, 8, 13, round(190 * (1 - y / Y(230)))))
        brand_x = 64
        if logo_path:
            with Image.open(logo_path) as raw:
                logo = ImageOps.contain(raw.convert('RGBA'), (Y(72), Y(72)), Image.Resampling.LANCZOS)
            layer.alpha_composite(logo, (64, Y(78)))
            brand_x = 156
        if spec.brand:
            lines, size = fit_text(spec.brand, 1016 - brand_x, 1, round(32 * type_scale(spec)), round(26 * type_scale(spec)))
            draw_lines(draw, (brand_x, Y(127)), lines, size)
        draw.line((64, Y(180), 1016, Y(180)), fill=(255, 255, 255, 55), width=2)
        draw.line((64, Y(180), 164, Y(180)), fill=spec.accent, width=4)
    if spec.source:
        lines, size = fit_text(spec.source, 950, 1, 26, 22, False)
        draw_lines(draw, (64, layout_y(spec, 1842)), lines, size, '#c4cbd5', False)
    return layer


def fit_news_text(spec):
    """Fit the headline and subtitle as one block inside the shared text area."""
    start = layout_y(spec, 1430 if spec.category else 1390)
    supporting = spec.supporting if spec.show_subtitle else ''
    scale = type_scale(spec)

    headline_map = {
        'small': (60, 48),
        'medium': (70, 50),
        'large': (80, 54),
        'xlarge': (90, 60),
    }
    if getattr(spec, 'headline_size', 'auto') in headline_map:
        pref, floor_h = headline_map[spec.headline_size]
        preferred = round(pref * scale)
        min_headline = round(floor_h * scale)
    else:
        preferred = (76 if spec.show_subtitle else 84) if spec.news_design == 'clean' else (70 if spec.show_subtitle else 80)
        preferred = round(preferred * scale)
        min_headline = round(50 * scale)

    subtitle_map = {
        'small': (34, 26),
        'medium': (40, 28),
        'large': (48, 32),
    }
    if getattr(spec, 'subtitle_size', 'auto') in subtitle_map:
        sub_pref, sub_min = subtitle_map[spec.subtitle_size]
        sub_start = round(sub_pref * scale)
        sub_floor = round(sub_min * scale)
    else:
        sub_start = round(44 * scale)
        sub_floor = round(28 * scale)

    last_error = None
    for headline_size in range(preferred, min_headline - 1, -2):
        try:
            headline_lines, _ = fit_text(spec.headline, 908, 3, headline_size, headline_size) if spec.headline else ([], headline_size)
            if spec.news_design == 'clean' and spec.headline:
                headline_lines = balanced_headline(spec.headline, headline_size)
        except ValueError as error:
            last_error = error
            continue
        max_sub = 3 if len(headline_lines) <= 2 else 2
        for subtitle_size in range(sub_start, sub_floor - 1, -2):
            try:
                subtitle_lines, _ = fit_text(supporting, 908, max_sub, subtitle_size, subtitle_size, False) if supporting else ([], subtitle_size)
            except ValueError as error:
                last_error = error
                continue
            end = start + len(headline_lines) * round(headline_size * 1.55)
            if subtitle_lines:
                end += 10 + len(subtitle_lines) * round(subtitle_size * 1.55)
            if end <= layout_y(spec, 1800):
                return headline_lines, headline_size, subtitle_lines, subtitle_size
    raise ValueError('Video text does not fit at a readable size. Shorten the headline or subtitle, or turn subtitles off.') from last_error


def cover(image, focal_x=0.5, focal_y=0.5, size=(WIDTH, HEIGHT)):
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS,
                        centering=(focal_x, focal_y)).convert('RGBA')


def make_layers(spec: Composition, image_path: Path, logo_path: Path | None = None):
    """Return background, stable image/brand, and headline layers plus warnings."""
    WIDTH, HEIGHT = canvas_size(spec)
    Y = lambda value: layout_y(spec, value)
    with Image.open(image_path) as source:
        image = source.convert('RGB')
    background = cover(image, spec.focal_x, spec.focal_y, (WIDTH, HEIGHT))
    overlay = Image.new('RGBA', (WIDTH, HEIGHT))
    text_layer = Image.new('RGBA', (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(overlay)
    text_draw = ImageDraw.Draw(text_layer)
    accent = ImageColor.getrgb(spec.accent)
    has_text = bool(spec.headline or (spec.show_subtitle and spec.supporting) or spec.category)
    warnings = []

    if spec.treatment == 'fit':
        background = background.resize((270, 480)).filter(ImageFilter.GaussianBlur(18)).resize(
            (WIDTH, HEIGHT), Image.Resampling.BICUBIC)
        background = Image.alpha_composite(background, Image.new('RGBA', background.size, (9, 12, 18, 150)))
        region_bottom = Y(1230 if has_text else 1740)
        region = (952, region_bottom - Y(210))
        fitted = ImageOps.contain(image, region, Image.Resampling.LANCZOS)
        x, y = (WIDTH - fitted.width) // 2, Y(210) + (region[1] - fitted.height) // 2
        draw.rounded_rectangle((x - 9, y - 9, x + fitted.width + 9, y + fitted.height + 9),
                               radius=12, fill=(255, 255, 255, 24))
        overlay.paste(fitted, (x, y))
        if fitted.height < 350:
            warnings.append('This wide image may be difficult to read on a phone. Check the finished video.')
        if spec.template in ('editorial', 'economist'):
            background = Image.new('RGBA', (WIDTH, HEIGHT), '#0e1622')

    if spec.news_design == 'classic' and (spec.brand or logo_path):
        shade = Image.new('RGBA', (WIDTH, HEIGHT))
        top = ImageDraw.Draw(shade)
        for y in range(Y(200)):
            top.line((0, y, WIDTH, y), fill=(5, 8, 13, round(170 * (1 - y / Y(200)))))
        overlay = Image.alpha_composite(overlay, shade)
        draw = ImageDraw.Draw(overlay)

    if spec.source and not has_text:
        for y in range(Y(1740), HEIGHT):
            text_draw.line((0, y, WIDTH, y), fill=(5, 8, 13, round((y - Y(1740)) / Y(180) * 210)))

    if has_text:
        # The text panel has identical geometry in poster and encoded frames.
        if spec.template in ('editorial', 'economist'):
            text_draw.rectangle((0, Y(1250), WIDTH, HEIGHT), fill=(13, 19, 29, 255))
            text_draw.rectangle((0, Y(1250), WIDTH, Y(1262)), fill=accent)
        elif spec.template == 'ap_wire':
            gradient = Image.new('RGBA', (WIDTH, HEIGHT))
            g = ImageDraw.Draw(gradient)
            for y in range(Y(1100), HEIGHT):
                opacity = min(245, round((y - Y(1100)) / Y(380) * 245))
                g.line((0, y, WIDTH, y), fill=(8, 10, 14, opacity))
            text_layer = Image.alpha_composite(text_layer, gradient)
            text_draw = ImageDraw.Draw(text_layer)
        elif spec.template in ('reuters', 'brief'):
            gradient = Image.new('RGBA', (WIDTH, HEIGHT))
            g = ImageDraw.Draw(gradient)
            for y in range(Y(1100), HEIGHT):
                opacity = min(240, round((y - Y(1100)) / Y(400) * 240))
                g.line((0, y, WIDTH, y), fill=(9, 13, 19, opacity))
            text_layer = Image.alpha_composite(text_layer, gradient)
            text_draw = ImageDraw.Draw(text_layer)
        else:
            gradient = Image.new('RGBA', (WIDTH, HEIGHT))
            g = ImageDraw.Draw(gradient)
            for y in range(Y(1120), HEIGHT):
                opacity = min(240, round((y - Y(1120)) / Y(400) * 240))
                g.line((0, y, WIDTH, y), fill=(7, 10, 16, opacity))
            text_layer = Image.alpha_composite(text_layer, gradient)
            text_draw = ImageDraw.Draw(text_layer)

        y = 1350
        if spec.category:
            lines, size = fit_text(spec.category, 870, 1, round(30 * type_scale(spec)), round(24 * type_scale(spec)))
            pill_width = round(text_width(lines[0], size)) + 36
            if spec.template == 'ap_wire':
                text_draw.rounded_rectangle((64, Y(1286), 64 + pill_width, Y(1341)), radius=4, fill=(217, 35, 42, 255))
                draw_lines(text_draw, (82, Y(1324)), lines, size, '#fef08a')
            elif spec.template in ('reuters', 'brief'):
                text_draw.rounded_rectangle((64, Y(1286), 64 + pill_width, Y(1341)), radius=4, fill=(20, 26, 36, 240), outline='#ff8000', width=2)
                draw_lines(text_draw, (82, Y(1324)), lines, size, '#ff8000')
            elif spec.template in ('economist', 'editorial'):
                text_draw.rounded_rectangle((64, Y(1286), 64 + pill_width, Y(1341)), radius=4, fill=accent)
                draw_lines(text_draw, (82, Y(1324)), lines, size, 'white')
            else:
                text_draw.rounded_rectangle((64, Y(1286), 64 + pill_width, Y(1341)), radius=6, fill=accent)
                draw_lines(text_draw, (82, Y(1324)), lines, size, accent_ink(spec.accent) if spec.news_design == 'clean' else 'white')
            y = Y(1430)
        else:
            y = Y(1390)
        accent_bar = ImageColor.getrgb('#ff8000') if spec.template in ('reuters', 'brief') else accent
        text_draw.rounded_rectangle((64, y - Y(48), 71, y + Y(30)), radius=3, fill=accent_bar)
        headline_lines, headline_size, subtitle_lines, subtitle_size = fit_news_text(spec)
        if headline_lines:
            y = draw_headline(text_draw, (92, y), headline_lines, headline_size, spec)
        if subtitle_lines:
            y = draw_lines(text_draw, (92, y + 10), subtitle_lines, subtitle_size, '#d5dbe4', False)

    if spec.news_design == 'classic':
        # Brand/source live above the headline layer, and remain visible in Brief.
        brand_x = 64
        if logo_path:
            with Image.open(logo_path) as raw:
                logo = ImageOps.contain(raw.convert('RGBA'), (Y(82), Y(82)), Image.Resampling.LANCZOS)
            overlay.alpha_composite(logo, (64, Y(78)))
            brand_x = 165
        if spec.brand:
            lines, size = fit_text(spec.brand, 900 - (brand_x - 64), 1, round(32 * type_scale(spec)), round(26 * type_scale(spec)))
            draw_lines(draw, (brand_x, Y(131)), lines, size)
        if spec.brand or logo_path:
            draw.line((64, Y(181), 1016, Y(181)), fill=(*accent, 210), width=3)
        if spec.source:
            lines, size = fit_text(spec.source, 950, 1, 26, 22, False)
            draw_lines(text_draw, (64, layout_y(spec, 1842)), lines, size, '#c4cbd5', False)
    if spec.news_design == 'clean':
        identity = news_brand(spec, logo_path)
        overlay.alpha_composite(identity.crop((0, 0, WIDTH, 230)), (0, 0))
        text_layer.alpha_composite(identity.crop((0, Y(1740), WIDTH, HEIGHT)), (0, Y(1740)))
    return background, overlay, text_layer, warnings


def save_layers(layers, directory):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, layer in zip(('background', 'overlay', 'text'), layers[:3]):
        path = directory / f'{name}.png'
        layer.save(path)
        paths.append(path)
    return paths


def poster(layers, spec=None):
    combined = Image.alpha_composite(Image.alpha_composite(layers[0], layers[1]), layers[2])
    if spec and getattr(spec, 'show_progress', False):
        WIDTH, HEIGHT = canvas_size(spec)
        prog_h = max(6, layout_y(spec, 10))
        prog_y = 0 if getattr(spec, 'progress_position', 'top') == 'top' else (HEIGHT - prog_h)
        accent_rgb = ImageColor.getrgb(spec.accent)
        draw = ImageDraw.Draw(combined)
        draw.rectangle((0, prog_y, WIDTH, prog_y + prog_h), fill=(0, 0, 0, 140))
        draw.rectangle((0, prog_y, round(WIDTH * 0.35), prog_y + prog_h), fill=(*accent_rgb, 240))
    return combined.convert('RGB').resize((540, combined.height // 2), Image.Resampling.LANCZOS)


def encode_command(ffmpeg, paths, output, spec, audio_path=None, preview=False):
    fps = spec.fps
    WIDTH, HEIGHT = canvas_size(spec)
    width, height = output_size(spec, preview)
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
           '-filter_complex_threads', '1']
    for path in paths:
        cmd += ['-threads', '1', '-loop', '1', '-framerate', str(fps), '-i', str(path)]
    if audio_path:
        cmd += ['-protocol_whitelist', 'file,pipe', '-i', str(audio_path)]
    count = spec.duration * fps - 1
    background = (f"[0:v]zoompan=z='1+0.035*on/{count}':"
                  f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s={WIDTH}x{HEIGHT}:fps={fps}[b]"
                  if spec.motion else '[0:v]null[b]')
    text = '[2:v]fade=t=in:st=0.3:d=0.45:alpha=1[t]' if spec.template in ('brief', 'reuters') and spec.motion else '[2:v]null[t]'
    size = f',scale={width}:{height}' if (width, height) != (WIDTH, HEIGHT) else ''
    filters = [background, text, '[b][1:v]overlay=0:0:shortest=1[c]', f'[c][t]overlay=0:0:shortest=1{size}[comp]']
    current = 'comp'
    if spec.show_progress:
        strip_h = max(5, round(height * 0.008))
        accent_color = spec.accent.replace('#', '0x')
        pos = getattr(spec, 'progress_position', 'top')
        prog_y = '0' if pos == 'top' else 'H-h'
        filters.append(f'color=c=0x000000@0.55:s={width}x{strip_h}:r={fps}:d={spec.duration}[track]')
        filters.append(f'color=c={accent_color}:s={width}x{strip_h}:r={fps}:d={spec.duration}[strip]')
        filters.append(f'[{current}][track]overlay=0:{prog_y}:shortest=1[wt]')
        filters.append(f"[wt][strip]overlay=x='-w+w*min(t/{spec.duration},1)':y={prog_y}:shortest=1[progress]")
        current = 'progress'
    filters.append(f'[{current}]setsar=1,format=yuv420p[v]')
    cmd += ['-filter_complex', ';'.join(filters), '-map', '[v]']
    if audio_path:
        audio_filter = 'apad' if spec.narration_enabled else f'volume={spec.volume},apad,afade=t=in:d=0.5,afade=t=out:st={spec.duration - 1}:d=1'
        cmd += ['-map', '3:a:0', '-af', audio_filter,
                '-c:a', 'aac', '-b:a', '128k']
    else:
        cmd += ['-an']
    return cmd + ['-t', str(spec.duration), '-r', str(fps), '-c:v', 'libx264', '-threads', '2',
                  '-preset', 'veryfast', '-crf', '24' if preview else '20', '-pix_fmt', 'yuv420p',
                  '-movflags', '+faststart', '-progress', 'pipe:1', str(output)]
