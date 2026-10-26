"""Text layout shared by posters and videos: bundled English fonts and wrapping."""
from functools import lru_cache
from pathlib import Path
import threading

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, features

FONTS = Path(__file__).parent / 'fonts'
_measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))

# Each look picks faces by name. Noto covers every character the studio accepts; a display face that
# lacks one hands the whole line to Noto of matching weight, so a line never mixes faces.
FACES = {
    'bold': 'NotoSans-Bold.ttf',
    'regular': 'NotoSans-Regular.ttf',
    'display': 'Anton-Regular.ttf',                  # condensed poster face: Breaking, Hot
    'condensed': 'BarlowCondensed-Bold.ttf',         # broadcast face: Politics, brand, labels
    'condensed-semi': 'BarlowCondensed-SemiBold.ttf',
    'serif': 'SourceSerif4-Bold.ttf',                # Editorial headline
    'serif-italic': 'SourceSerif4-It.ttf',           # Editorial deck
}
LIGHT_FACES = ('regular', 'serif-italic')


def face_name(face):
    """Callers may still pass bold=True/False for the Noto faces."""
    return 'bold' if face is True else 'regular' if face is False else face


@lru_cache(maxsize=16)
def font_data(face=True):
    path = FONTS / FACES[face_name(face)]
    try:
        with TTFont(path) as font:
            cmap = set(font.getBestCmap())
    except OSError as exc:
        raise ValueError('Bundled fonts are missing. Run Settings → Setup and repair.') from exc
    return path, cmap


def font_at(size, face=True):
    # Posters and the video render draw at the same time; FreeType faces are not shared between threads.
    return _font(str(font_data(face)[0]), size, threading.get_ident())


@lru_cache(maxsize=256)
def _font(path, size, thread):
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)


def covers(text, face):
    cmap = font_data(face)[1]
    return all(ord(c) in cmap or c in ' \n' for c in text)


def resolve(text, face=True):
    """The face that will draw this text: the one asked for, or Noto when it lacks a character."""
    face = face_name(face)
    if face in ('bold', 'regular') or covers(text, face):
        return face
    return 'regular' if face in LIGHT_FACES else 'bold'


def runs(text, size, face=True):
    """The text as drawable (text, font) pieces; English only, so a single run."""
    if not features.check_feature('raqm'):
        raise ValueError('Text layout support is missing. Run Settings → Setup and repair.')
    clean = ''.join(c for c in text if c not in ('​', '‌', '‍'))
    face = resolve(clean, face)
    cmap = font_data(face)[1]
    for char in clean:
        if ord(char) not in cmap:
            raise ValueError(f'The font cannot display {char!r}. Remove it from the text.')
    return [(clean, font_at(size, face))] if clean else []


def metrics(size, face=True):
    """(ascent, descent) in pixels of the face that will draw at this size."""
    return font_at(size, face_name(face)).getmetrics()


def text_width(text, size, face=True):
    return sum(_measure.textlength(piece, font=font) for piece, font in runs(text, size, face))


def wrap(text, size, width, max_lines, face=True):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split(' '):
            candidate = (line + ' ' + word).strip()
            if text_width(candidate, size, face) <= width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                if text_width(word, size, face) > width:
                    raise ValueError('A word is too wide for the video. Shorten the text.')
                line = word
        lines.append(line)
    if len(lines) > max_lines:
        raise ValueError(f'Text needs more than {max_lines} lines. Shorten it.')
    return lines


def fit_text(text, width, max_lines, preferred, minimum, face=True):
    """Largest size from preferred down to minimum at which the text fits."""
    error = None
    for size in range(preferred, minimum - 1, -2):
        try:
            return wrap(text, size, width, max_lines, face), size
        except ValueError as exc:
            error = exc
    raise error or ValueError('Text does not fit')


def draw_line(draw, xy, text, size, fill='white', face=True, **kwargs):
    """Draw one line with baseline anchor; returns its width."""
    x, y = xy
    for piece, font in runs(text, size, face):
        draw.text((x, y), piece, font=font, fill=fill, anchor='ls', **kwargs)
        x += draw.textlength(piece, font=font)
    return x - xy[0]


def serif_ok(text):
    """The italic serif face covers Latin text only; anything else falls back to the sans face."""
    return covers(text, 'serif-italic')
