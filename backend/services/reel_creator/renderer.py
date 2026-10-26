"""Frame renderer for news reels. Posters and videos come from the same Plan.frame()."""
import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .reel import Reel, highlight_span
from .acceleration import resize_photo
from .typeset import covers, draw_line, font_at, metrics, resolve, runs, text_width, wrap

DESIGN_W = 1080
OPENING_COVER_HOLD = .6  # 18 readable opening frames at 30 fps, inside the first scene
OPENING_COVER_SECONDS = 3.
CROSSFADE = .35      # seconds of background blend between scenes
SCENE_FADE = .25     # seconds text fades out before a scene ends
OVERSCAN = 1.18      # background is rendered larger than the canvas so it can zoom
INK, MUTED = (17, 24, 39), (75, 85, 99)
PAGE, PANEL = (244, 246, 250), (12, 14, 20)  # clean page, politics text panel
PAPER = (250, 246, 238)                        # editorial page
LIGHT_PAGE = ('clean', 'editorial')            # looks that sit on a light page: dark type, light progress track
WHITE = (255, 255, 255, 255)
LEFT = 64            # text starts here on every look
RIGHT_REELS = 150    # Reels lays its like, comment and share buttons over the right edge of 9:16 video
STAT_SIZES = (330, 280, 236, 196, 160, 130)  # design px tried for a stat figure, largest first
MARK_SIZES = (210, 176, 148, 124)            # the quote mark and the end card's ? or →
LEAD_GAP = 14

# Type per look, in design px at 1080 wide: (face, preferred size, smallest size). Headline and detail
# shrink together until the block fits; the smallest sizes stay readable on a phone held at arm's length.
TYPE = {
    'pop_art': dict(head=('bold', 86, 50), detail=('regular', 40, 34), label='bold', head_pitch=1.12),
    'breaking': dict(head=('display', 108, 60), detail=('regular', 46, 34), label='condensed', head_pitch=1.1),
    'hot': dict(head=('display', 140, 64), detail=('bold', 46, 34), label='condensed', head_pitch=1.06),
    'politics': dict(head=('condensed', 112, 62), detail=('regular', 46, 34), label='condensed-semi', head_pitch=1.06),
    'clean': dict(head=('bold', 86, 50), detail=('regular', 44, 34), label='bold', head_pitch=1.2),
    'wire': dict(head=('bold', 84, 50), detail=('regular', 44, 34), label='condensed-semi', head_pitch=1.18),
    'editorial': dict(head=('serif', 96, 54), detail=('serif-italic', 50, 36), label='condensed-semi', head_pitch=1.12),
}


def clamp(value, low=0., high=1.):
    return max(low, min(high, value))


def ease_out(p):
    return 1 - (1 - clamp(p)) ** 3


def ease_back(p):
    p = clamp(p)
    return 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


@dataclass
class Element:
    tile: Image.Image            # RGBA
    x: int
    y: int
    start: float = 0
    dur: float = .45
    anim: str = 'fade'           # fade | rise | left | pop | wipe | blink | marquee | none
    fades_out: bool = True       # scene elements fade before the scene ends
    shine: bool = False          # periodic light sweep across the tile
    period: int = 0              # marquee: width of one repeat, in pixels


class Plan:
    """Layout for one reel at one output width. Built once, then sampled by time."""

    def __init__(self, reel: Reel, width: int, images: dict, logo=None, fps=30):
        self.reel, self.fps = reel, fps
        self.W = width
        self.s = width / DESIGN_W
        self.DH = reel.design_height
        self.H = reel.size(width)[1]
        portrait = reel.ratio == '9:16'
        # Reels cover the bottom with captions and buttons; a feed video has far less in the way.
        self.inset = 150 if portrait else 70     # top of the logo row
        self.limit = self.DH - (380 if portrait else 100)  # text must end above this line
        self.right = RIGHT_REELS if portrait else LEFT     # text keeps clear of the Reels button column
        self.text_w = DESIGN_W - LEFT - self.right
        self.type = TYPE[reel.look]
        self.ink = INK + (255,) if reel.look in LIGHT_PAGE else WHITE
        self.images, self.logo_path = images, logo
        self.lengths = [scene.length() for scene in reel.scenes]
        self.starts = [sum(self.lengths[:i]) for i in range(len(self.lengths))]
        self.duration = sum(self.lengths)
        self.accent = rgb(reel.accent_color)
        self._bases, self._masks = {}, {}
        default, self.page = self._frame()
        # Looks with a page or panel size the photo to the text each scene needs.
        self.scene_elements, self.rects = [], []
        for index, scene in enumerate(reel.scenes):
            self._scene_rect, self._scene, self._lead_plan = default, scene, None
            try:
                self.scene_elements.append(self._layout(scene))
            except ValueError as exc:
                raise ValueError(f'Scene {index + 1}: {exc}') from None
            self.rects.append(self._scene_rect)
        self.rect = self.rects[0]
        self.mask = self._mask(self.rect)
        self.global_elements = self._identity()

    def px(self, value):
        return round(value * self.s)

    # ---- timeline -------------------------------------------------------
    def locate(self, t):
        t = clamp(t, 0, self.duration)
        index = 0
        for i, start in enumerate(self.starts):
            if start <= t:
                index = i
        return index, min(t - self.starts[index], self.lengths[index])

    def settled(self, index):
        """A time when this scene's text has finished animating in."""
        return self.starts[index] + min(1.9, self.lengths[index] - SCENE_FADE - .05)

    def frame(self, t):
        if 0 <= t < 1 / self.fps:
            # Put an actual later video frame into the MP4's opening, not just
            # its separate JPEG poster. Replace only frame zero; keep the rest
            # of the timeline intact. Short reels use their settled first card.
            sample = OPENING_COVER_SECONDS if self.duration > OPENING_COVER_SECONDS else self.settled(0)
            return self._timeline_frame(sample)
        return self._timeline_frame(t)

    def _timeline_frame(self, t):
        index, local = self.locate(t)
        length = self.lengths[index]
        clock = t
        if index == 0:
            # Start with the complete first card, hold it briefly, then continue
            # smoothly. Keep the existing scene boundary and total video length.
            local = max(0., local - OPENING_COVER_HOLD) * length / (length - OPENING_COVER_HOLD)
            clock = local
        image = self.background(index, local)
        if index + 1 < len(self.lengths) and local > length - CROSSFADE:
            blend = ease_out((local - (length - CROSSFADE)) / CROSSFADE)
            image = Image.blend(image, self.background(index + 1, 0), blend)
        for element in self.scene_elements[index]:
            self._draw(image, element, local, local, length, entrance_complete=index == 0)
        for element in self.global_elements:
            self._draw(image, element, clock, clock, None, entrance_complete=index == 0)
        if self.reel.show_progress:
            self._progress(image, clock, index, local)
        return image

    # ---- background -----------------------------------------------------
    def _frame(self):
        """Where the photo sits by default: (x, y, w, h, radius) in pixels, and the color around it."""
        px, look = self.px, self.reel.look
        if look == 'pop_art':
            rect, page = (0, 0, DESIGN_W, round(self.DH * .62), 0), (17, 17, 17)
        elif look == 'politics':   # photo on top, text panel below
            rect, page = (0, 0, DESIGN_W, round(self.DH * .52), 0), PANEL
        elif look == 'clean':  # rounded photo card on a white page
            rect, page = (48, self.inset + 140, 984, round(self.DH * .41), 32), PAGE
        elif look == 'editorial':  # headline on top, photo across the bottom
            top = round(self.DH * (.46 if self.reel.ratio == '9:16' else .52))
            rect, page = (0, top, DESIGN_W, self.DH - top, 0), PAPER
        else:                 # full bleed
            rect, page = (0, 0, DESIGN_W, self.DH, 0), None
        return tuple(px(v) for v in rect), page

    def _mask(self, rect):
        if not rect[4] or not rect[2] or not rect[3]:
            return None
        if rect not in self._masks:
            mask = Image.new('L', (rect[2], rect[3]), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, rect[2] - 1, rect[3] - 1), rect[4], fill=255)
            self._masks[rect] = mask
        return self._masks[rect]

    def background(self, index, local):
        scene, rect = self.reel.scenes[index], self.rects[index]
        if not rect[2] or not rect[3]:
            return Image.new('RGB', (self.W, self.H), self.page or PANEL)
        base = self._base(index)
        x0, y0, rw, rh, _ = rect
        progress = clamp(local / self.lengths[index])
        motion = self.reel.motion
        if motion == 'none':
            push = .5
        else:  # simple zoom: in, out, or alternating by scene
            push = 1 - progress if motion == 'zoom_out' or (motion == 'auto' and index % 2) else progress
        view_w = rw * (1.14 - .12 * push)
        view_h = view_w * rh / rw
        fx, fy = (.5, .5) if scene.fit == 'blur' and scene.image_id else (scene.focal_x, scene.focal_y)
        x = clamp(fx * base.width - view_w / 2, 0, base.width - view_w)
        y = clamp(fy * base.height - view_h / 2, 0, base.height - view_h)
        photo = resize_photo(base, (rw, rh), (x, y, x + view_w, y + view_h))
        if self.page is None:
            return photo
        canvas = Image.new('RGB', (self.W, self.H), self.page)
        canvas.paste(photo, (x0, y0), self._mask(rect))
        return canvas

    def _base(self, index):
        if index in self._bases:
            return self._bases[index]
        scene, rw, rh = self.reel.scenes[index], self.rects[index][2], self.rects[index][3]
        size = (round(rw * OVERSCAN), round(rh * OVERSCAN))
        path = self.images.get(scene.image_id) if scene.image_id else None
        if path:
            with Image.open(path) as source:
                photo = source.convert('RGBA' if self.reel.look == 'pop_art' else 'RGB')
            if self.reel.look == 'pop_art':
                base = self._collage_photo(photo, size, scene)
            elif scene.fit == 'blur':
                base = self._blur_fill(photo, size)
            else:
                base = ImageOps.fit(photo, size, Image.Resampling.LANCZOS, centering=(scene.focal_x, scene.focal_y))
        elif self.reel.look == 'pop_art':
            base = self._collage_photo(None, size, scene)
        else:
            base = self._plain_background(size)
        base = self._scrim(base)
        if len(self._bases) >= 3:  # long reels would otherwise hold every scene in memory
            self._bases.pop(next(iter(self._bases)))
        self._bases[index] = base
        return base

    def _collage_photo(self, photo, size, scene):
        """Retro pattern and halo; preserve uploaded cutouts without guessing a subject mask."""
        w, h = size
        base = Image.new('RGB', size, '#a28bd4')
        draw = ImageDraw.Draw(base)
        step = max(8, round(w / 10))
        for y in range(-step, h + step, step):
            for x in range(-step, w + step, step):
                draw.ellipse((x, y, x + step, y + step), fill='#747d3d')
        radius = w * .36
        cx, cy = w * .53, h * .39
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=self.accent)
        if photo is None:
            return base
        alpha = photo.getchannel('A')
        gray = ImageOps.grayscale(photo).convert('RGBA')
        gray.putalpha(alpha)
        if alpha.getextrema()[0] < 255:
            bounds = alpha.getbbox()
            if bounds is None:
                return base
            front = ImageOps.contain(gray.crop(bounds), (round(w * .9), round(h * .93)), Image.Resampling.LANCZOS)
            x, y = (w - front.width) // 2, h - front.height
        else:
            # Keep a visible patterned margin around photographs that have no subject mask.
            front = (ImageOps.contain(gray, (round(w * .84), round(h * .83)), Image.Resampling.LANCZOS)
                     if scene.fit == 'blur' else ImageOps.fit(gray, (round(w * .84), round(h * .83)),
                         Image.Resampling.LANCZOS, centering=(scene.focal_x, scene.focal_y)))
            x, y = (w - front.width) // 2, h - front.height
        mask = Image.new('L', size)
        mask.paste(front.getchannel('A'), (x, y))
        spread = max(3, round(w * .008)) | 1
        outline = mask.filter(ImageFilter.MaxFilter(spread))
        base.paste((255, 255, 255), (0, 0, w, h), outline)
        base.paste(front, (x, y), front.getchannel('A'))
        return base

    def _blur_fill(self, photo, size):
        """The whole photo, uncropped, over a soft enlarged copy of itself."""
        w, h = size
        small = ImageOps.fit(photo, (max(1, w // 10), max(1, h // 10)), Image.Resampling.BILINEAR)
        backdrop = small.filter(ImageFilter.GaussianBlur(3)).resize(size, Image.Resampling.BICUBIC)
        backdrop = Image.blend(backdrop, Image.new('RGB', size, (0, 0, 0)), .35)
        # Sized to the frame at full zoom-in, so no edge of the photo is lost while it moves.
        full_bleed = self.page is None
        front = ImageOps.contain(photo, (round(w / OVERSCAN), round(h / OVERSCAN * (.9 if full_bleed else 1))), Image.Resampling.LANCZOS)
        x = (w - front.width) // 2
        center = h * (.36 if full_bleed else .5)  # above the text on full-bleed looks
        y = round(clamp(center - front.height / 2, 0, h - front.height))
        shadow = Image.new('L', size, 0)
        spread = max(2, round(w * .02))
        ImageDraw.Draw(shadow).rectangle((x - spread, y - spread // 2, x + front.width + spread, y + front.height + spread * 2), fill=150)
        shadow = shadow.filter(ImageFilter.GaussianBlur(spread))
        backdrop = Image.composite(Image.new('RGB', size, (0, 0, 0)), backdrop, shadow)
        backdrop.paste(front, (x, y))
        return backdrop

    def _plain_background(self, size):
        column = Image.new('RGB', (1, size[1]))
        column.putdata([(round(14 - 9 * y / size[1]), round(22 - 14 * y / size[1]), round(38 - 24 * y / size[1]))
                        for y in range(size[1])])
        base = column.resize(size)
        glow = Image.new('L', size, 0)
        ImageDraw.Draw(glow).ellipse((size[0] * .1, -size[1] * .08, size[0] * .9, size[1] * .32), fill=70)
        glow = glow.filter(ImageFilter.GaussianBlur(size[0] * .12))
        return Image.composite(Image.new('RGB', size, self.accent), base, glow)

    def _scrim(self, base):
        """Darken where text sits so it stays readable on any photo. Clean needs none."""
        spec = {'breaking': ((.40, .78, .86), (.2, .55), (6, 8, 14)),
                'hot': ((.60, .92, .78), (.5, .72), (6, 8, 14)),          # both ends: headline on top, detail below
                'wire': ((.45, .86, .9), (.2, .5), (6, 8, 14)),
                'politics': ((.74, 1.0, 1.0), (.25, .5), PANEL)}.get(self.reel.look)  # photo melts into the panel
        if not spec:
            return base
        (start, end, strength), (reach, top_strength), color = spec
        height = base.height
        bottom = Image.new('L', (1, height))
        bottom.putdata([round(255 * strength * clamp((y / height - start) / (end - start))) for y in range(height)])
        top = Image.new('L', (1, height))
        top.putdata([round(255 * top_strength * clamp(1 - y / (height * reach))) for y in range(height)])
        mask = ImageChops.lighter(bottom, top).resize(base.size)
        return Image.composite(Image.new('RGB', base.size, color), base, mask)

    # ---- element drawing ------------------------------------------------
    def _draw(self, image, element, local, clock, scene_length, entrance_complete=False):
        if not entrance_complete and local < element.start:
            return
        p = 1 if entrance_complete or not element.dur else (local - element.start) / element.dur
        tile, x, y, alpha = element.tile, element.x, element.y, 1.
        match element.anim:
            case 'fade':
                alpha = ease_out(p)
            case 'rise':
                alpha, y = ease_out(p), y + round((1 - ease_out(p)) * self.px(36))
            case 'left':
                x -= round((1 - ease_out(p)) * (x + tile.width))
            case 'pop':
                k = .45 + .55 * ease_back(p)
                size = (max(1, round(tile.width * k)), max(1, round(tile.height * k)))
                x, y = x + (tile.width - size[0]) // 2, y + (tile.height - size[1]) // 2
                tile, alpha = tile.resize(size, Image.Resampling.BILINEAR), clamp(p * 4)
            case 'wipe':
                width = round(tile.width * ease_out(p))
                if width < 1:
                    return
                tile = tile.crop((0, 0, width, tile.height))
            case 'marquee':
                x -= round((clock * self.px(110)) % element.period)
            case 'blink':
                alpha = .3 + .7 * (.5 + .5 * math.sin(2 * math.pi * 1.3 * clock))
        if scene_length is not None and element.fades_out:
            alpha *= clamp((scene_length - local) / SCENE_FADE)
        if alpha <= 0:
            return
        if element.shine and (phase := (clock % 5) / .8) < 1:
            tile = self._shine(tile, phase)
        if alpha < 1:
            tile = tile.copy()
            tile.putalpha(tile.getchannel('A').point([round(i * alpha) for i in range(256)]))
        image.paste(tile, (x, y), tile)

    @staticmethod
    def _shine(tile, phase):
        band = Image.new('L', tile.size, 0)
        w, h = tile.size
        left = -.35 * w + phase * 1.7 * w
        ImageDraw.Draw(band).polygon([(left, 0), (left + .28 * w, 0), (left + .28 * w - .3 * h, h), (left - .3 * h, h)], fill=150)
        light = Image.new('RGBA', tile.size, (255, 255, 255, 255))
        light.putalpha(ImageChops.multiply(band, tile.getchannel('A')))
        return Image.alpha_composite(tile, light)

    def _progress(self, image, t, index, local):
        draw = ImageDraw.Draw(image, 'RGBA')
        accent, light = self.accent + (255,), self.reel.look in LIGHT_PAGE
        if self.reel.look in ('hot', 'politics'):  # segmented: one bar per scene
            count, gap, height = len(self.lengths), self.px(8), max(3, self.px(7))
            y = self.px(46 if self.reel.ratio == '9:16' else 22)
            width = (self.W - 2 * self.px(40) - gap * (count - 1)) / count
            for i in range(count):
                x0 = self.px(40) + i * (width + gap)
                draw.rounded_rectangle((x0, y, x0 + width, y + height), height // 2, fill=(255, 255, 255, 80))
                fill = 1 if i < index else clamp(local / self.lengths[i]) if i == index else 0
                if fill > 0:
                    draw.rounded_rectangle((x0, y, x0 + max(height, width * fill), y + height), height // 2,
                                           fill=WHITE if self.reel.look == 'politics' else accent)
        else:  # one continuous line
            height = max(4, self.px(12))
            draw.rectangle((0, 0, self.W, height), fill=(0, 0, 0, 40 if light else 120))
            draw.rectangle((0, 0, round(self.W * clamp(t / self.duration)), height), fill=accent)

    # ---- tiles ----------------------------------------------------------
    def _text(self, text, size, color, face=True, stroke=0, shadow=True, spacing=0, mark=None):
        """One line of text as a transparent tile; returns (tile, baseline offset).

        mark is a (start, end) span drawn in the accent color, or on an accent marker where color alone would not read.
        """
        pad = self.px(8) + stroke
        size = max(1, round(size))
        face = resolve(text, face)
        ascent, descent = metrics(size, face)
        width = math.ceil(text_width(text, size, face)) + spacing * len(text) + 2 * pad
        base = pad + ascent
        tile = Image.new('RGBA', (width, base + descent + pad + self.px(3)))
        draw = ImageDraw.Draw(tile)
        if spacing and text.isascii():
            x = pad
            for char in text:
                if shadow:
                    draw_line(draw, (x, base + self.px(3)), char, size, (0, 0, 0, 130), face)
                x += draw_line(draw, (x, base), char, size, color, face) + spacing
            return tile, base
        if shadow and not stroke:
            draw_line(draw, (pad, base + self.px(3)), text, size, (0, 0, 0, 130), face)
        if not mark:
            draw_line(draw, (pad, base), text, size, color, face, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            return tile, base
        accent = self.accent + (255,)
        marker = self._marker()
        start, end = mark
        for left, right, marked in ((0, start, False), (start, end, True), (end, len(text), False)):
            piece = text[left:right]
            if not piece.strip():
                continue
            x = pad + text_width(text[:left], size, face)
            if marked and marker:
                cap = -font_at(size, face).getbbox('H', anchor='ls')[1]
                pad_x = self.px(10)
                draw.rounded_rectangle((x - pad_x, base - cap - self.px(10), x + text_width(piece, size, face) + pad_x,
                                        base + self.px(12)), self.px(6), fill=accent)
            fill = (self._ink_on(self.accent) if marker else accent) if marked else color
            draw_line(draw, (x, base), piece, size, fill, face, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        return tile, base

    def _marker(self):
        """Breaking sets highlights on an accent block, like a broadcast strap; light pages do too when the accent is pale."""
        r, g, b = self.accent
        return self.reel.look == 'breaking' or (self.reel.look in LIGHT_PAGE and .2126 * r + .7152 * g + .0722 * b > 150)

    def _box(self, size, color, radius=0):
        tile = Image.new('RGBA', size)
        ImageDraw.Draw(tile).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=color)
        return tile

    def _stack(self, lines, size, color, x, top, start, step=.12, face=True, pitch=1.3, anim='rise', marks=None, **style):
        """Left-aligned lines from `top` (line-box top). Returns elements and the y below the last line."""
        elements, step_px = [], round(size * pitch)
        for k, line in enumerate(lines):
            tile, base = self._text(line, size, color, face, mark=marks[k] if marks else None, **style)
            baseline = top + k * step_px + round(size * 1.0)
            elements.append(Element(tile, x - self.px(8) - style.get('stroke', 0), baseline - base,
                                    start + k * step, .45, anim))
        return elements, top + len(lines) * step_px

    def _headline(self, lines, size, color, x, top, start, **style):
        """The scene's lead (figure or mark) if it has one, then the headline with its highlighted words."""
        out = []
        if self._lead_plan:
            mark, face, lead_size, height, rise = self._lead_plan
            lead_style = {key: value for key, value in style.items() if key in ('stroke', 'shadow')}
            tile, base = self._text(mark, lead_size, self.accent + (255,), face, **lead_style)
            out.append(Element(tile, x - self.px(8) - lead_style.get('stroke', 0), top - (base - rise),
                               max(0, start - .1), .5, 'pop'))
            top += height + (self.px(LEAD_GAP) if lines else 0)
        elements, bottom = self._stack(lines, size, color, x, top, start, face=self.type['head'][0],
                                       pitch=self.type['head_pitch'], marks=self._highlights(lines), **style)
        return out + elements, bottom

    def _details(self, lines, size, color, x, top, start, step=.1, pitch=1.38):
        return self._stack(lines, size, color, x, top, start, step=step, face=self.type['detail'][0],
                           pitch=pitch, anim='fade', shadow=False)

    # ---- looks ----------------------------------------------------------
    def _layout(self, scene):
        return getattr(self, '_' + self.reel.look)(scene)

    def _fit(self, headline, detail, width, avail, head=None, detail_type=None, det_pitch=1.38, gap=16, lead=True, max_head_lines=5):
        """Largest type, headline and detail shrinking together, whose block fits `avail` design px.

        head/detail_type are (face, preferred, smallest) design sizes, by default the look's own. With `lead`,
        the scene's large figure or mark sits above the headline and is counted in the block; it shrinks first.
        Returns pixel-space (head_lines, head_size, detail_lines, detail_size, height); sizes are 0 for absent text.
        """
        px = self.px
        (hface, h0, h1), (dface, d0, d1) = head or self.type['head'], detail_type or self.type['detail']
        mark = self._scene.lead if lead else ''
        if not headline and not detail and not mark:
            return [], 0, [], 0, 0
        runs(headline, 10, hface)  # a character no font has is its own error, not 'too long'
        runs(detail, 10, dface)
        steps = max(1, (max(h0 - h1, d0 - d1) + 1) // 2)
        sizes = STAT_SIZES if self._scene.kind == 'stat' else MARK_SIZES
        for lead_size in (sizes if mark else (0,)):
            plan = self._lead_plan_for(mark, lead_size, width) if mark else None
            if mark and not plan:
                continue
            lead_h = plan[3] + (px(LEAD_GAP) if headline or detail else 0) if plan else 0
            for k in range(steps + 1):
                size, detail_size = round(h0 - (h0 - h1) * k / steps), round(d0 - (d0 - d1) * k / steps)
                try:
                    hl = wrap(headline, px(size), px(width), max_head_lines, hface) if headline else []
                    dl = wrap(detail, px(detail_size), px(width), 4, dface) if detail else []
                except ValueError:
                    continue
                height = lead_h + (len(hl) * round(px(size) * self.type['head_pitch'])
                                   + ((px(gap) if hl else 0) + len(dl) * round(px(detail_size) * det_pitch) if dl else 0))
                if height <= px(avail):
                    if lead:
                        self._lead_plan = plan
                    return hl, px(size), dl, px(detail_size), height
        if mark and not any(self._lead_plan_for(mark, size, width) for size in sizes):
            raise ValueError('the figure is too wide. Shorten it.')
        raise ValueError('the text is too long for this layout. Shorten it.')

    def _lead_plan_for(self, mark, size, width):
        """(text, face, pixel size, glyph height, glyph top above the baseline) if the mark fits `width` design px."""
        head = self.type['head'][0]
        face = 'serif' if mark == '“' else head if self._scene.kind == 'stat' else head if covers(mark, head) else 'serif'
        face = resolve(mark, face)
        if text_width(mark, self.px(size), face) > self.px(width):
            return None
        _, top, _, bottom = font_at(self.px(size), face).getbbox(mark, anchor='ls')
        return mark, face, self.px(size), bottom - top, -top

    def _highlights(self, lines):
        """Per line, the (start, end) span of the scene's highlight, if the headline contains it."""
        span = highlight_span(' '.join(lines), self._scene.highlight)
        marks, offset = [], 0
        for line in lines:
            start, end = (max(span[0], offset) - offset, min(span[1], offset + len(line)) - offset) if span else (0, 0)
            marks.append((start, end) if start < end else None)
            offset += len(line) + 1
        return marks

    def _label(self, text, size, fg, bg, radius, height, pad, face=None):
        """A filled label (pill, strap or chip) holding one line of text."""
        tile, base = self._text(text, size, fg, face or self.type['label'], shadow=False)
        box = self._box((tile.width + 2 * self.px(pad) - 2 * self.px(8), self.px(height)), bg, self.px(radius))
        # Center the letters, not the font's line box: caps sit on the baseline.
        box.alpha_composite(tile, (self.px(pad) - self.px(8), (box.height + round(size * .7)) // 2 - base))
        return box

    def _ink_on(self, color):
        r, g, b = color
        return (12, 12, 12, 255) if .2126 * r + .7152 * g + .0722 * b > 150 else WHITE

    def _breaking(self, scene):
        """Lower-third news strap over a full-bleed photo; a ticker runs along the bottom edge."""
        px, accent = self.px, self.accent + (255,)
        zone = self.limit - 80                                   # the ticker takes the last 80
        tag = self.reel.tag_text
        top_limit = self.inset + 150 + (84 if tag else 0)
        hl, hs, dl, ds, block = self._fit(scene.headline, scene.detail_text, self.text_w, zone - top_limit - 60, gap=14)
        if not block:
            return []
        height = px(30) + block + px(30)
        top = px(zone) - height
        out = [Element(self._box((self.W, height), (10, 12, 18, 232)), 0, top, .05, .5, 'rise'),
               Element(self._box((px(16), height), accent), 0, top, .05, .5, 'rise')]
        if tag:
            bar = self._label(tag, px(46), WHITE, accent, 0, 84, 66)
            out.append(Element(bar, 0, top - px(84), 0, .4, 'left', fades_out=False))
            out.append(Element(self._box((px(20), px(20)), WHITE, px(10)), px(30), top - px(84) + px(32), .4, .3, 'blink', fades_out=False))
        lines, y = self._headline(hl, hs, WHITE, px(LEFT), top + px(30), .2)
        out += lines
        if dl:
            details, y = self._details(dl, ds, (220, 225, 233, 255), px(LEFT), y + px(14), .3 + len(hl) * .12)
            out += details
        return out

    def _hot(self, scene):
        """Huge headline in the top half, the photo in the middle, the detail on a sticker below."""
        px, accent = self.px, self.accent + (255,)
        out, y = [], self.inset + 130
        if self.reel.tag_text:
            pill = self._label(self.reel.tag_text, px(42), self._ink_on(self.accent), accent, 14, 72, 24)
            out.append(Element(pill, px(LEFT), px(y), 0, .5, 'pop'))
            if self.reel.source:
                tile, base = self._text(self.reel.source.upper(), px(32), WHITE, 'condensed-semi', spacing=px(2))
                out.append(Element(tile, px(LEFT) + pill.width + px(16), px(y) + (px(72) + round(px(32) * .7)) // 2 - base, .3, .5, 'fade'))
            y += 72 + 34
        reserve = 0
        if scene.detail_text:  # the sticker claims the bottom first; the headline gets what is left
            face, d0, d1 = self.type['detail']
            _, _, dl, ds, block = self._fit('', scene.detail_text, self.text_w - 52, 400, detail_type=(face, d0, d1), det_pitch=1.32, lead=False)
            pad, widest = px(26), max(text_width(line, ds, face) for line in dl)
            sticker = self._box((round(widest) + 2 * pad, block + 2 * px(22)), accent, px(18))
            for k, line in enumerate(dl):
                tile, base = self._text(line, ds, self._ink_on(self.accent), face, shadow=False)
                sticker.alpha_composite(tile, (pad - px(8), px(22) + k * round(ds * 1.32) + round(ds) - base))
            out.append(Element(sticker, px(LEFT), px(self.limit) - sticker.height, .6, .5, 'rise'))
            reserve = sticker.height / self.s + 44
        hl, hs, _, _, block = self._fit(scene.headline, '', self.text_w, self.limit - y - reserve)
        if block:
            lines, bottom = self._headline(hl, hs, WHITE, px(LEFT), px(y), .15, stroke=max(2, px(5)), shadow=False)
            out += lines
        if hl:
            widest = max(text_width(line, hs, self.type['head'][0]) for line in hl)
            out.append(Element(self._box((min(px(self.text_w), round(widest * .6) + px(40)), px(14)), accent, px(7)),
                               px(LEFT), bottom + px(10), .15 + len(hl) * .12, .5, 'wipe'))
        return out

    def _politics(self, scene):
        """Photo on top, a solid text panel below: label, headline, detail. The photo takes what the text leaves."""
        px, accent = self.px, self.accent + (255,)
        label = '  |  '.join(filter(None, [self.reel.tag_text, self.reel.source.upper()]))
        header = 104 if label else 56
        low, high = round(self.DH * .40), round(self.DH * .58)
        hl, hs, dl, ds, block = self._fit(scene.headline, scene.detail_text, self.text_w, self.limit - low - header,
                                          det_pitch=1.4, gap=24)
        if not block:
            return []
        split = max(low, min(high, round(self.limit - header - block / self.s - 24)))
        self._scene_rect = (0, 0, self.W, px(split), 0)
        top = split + header
        out = []
        if label:
            out.append(Element(self._box((px(72), px(5)), accent), px(LEFT + 8), px(split + 44), 0, .9, 'wipe'))
            tile, base = self._text(label, px(32), accent, self.type['label'], spacing=px(4), shadow=False)
            out.append(Element(tile, px(LEFT), px(split + 86) - base, .1, .9, 'fade'))
        lines, y = self._headline(hl, hs, WHITE, px(LEFT), px(top), .3, step=.2, shadow=False)
        out += lines
        if dl:
            details, y = self._details(dl, ds, (226, 230, 236, 255), px(LEFT), y + px(24), .5 + len(hl) * .2, step=.15, pitch=1.4)
            out += details
        return out

    def _bare_end(self, scene):
        """An end card with no photo: page looks show the page itself rather than an empty photo frame."""
        return scene.kind == 'end' and not scene.image_id

    def _clean(self, scene):
        """A white page: rounded photo card, then the headline and detail in dark type. The card grows when the text is short."""
        px, accent = self.px, self.accent + (255,)
        card_top = self.inset + 140
        source = self.reel.source
        below = 76 + (60 if source else 0)
        low, high = round(self.DH * .30), round(self.DH * .50)
        hl, hs, dl, ds, block = self._fit(scene.headline, scene.detail_text, self.text_w,
                                          self.limit - card_top - low - below)
        if not block:
            return []
        card_h = max(low, min(high, round(self.limit - card_top - below - block / self.s)))
        self._scene_rect = (px(48), px(card_top), px(984), 0 if self._bare_end(scene) else px(card_h), px(32))
        # A bare end card has no photo card; its text sits in the middle of the page instead.
        card_end = card_top + (round((self.limit - card_top - below - block / self.s) / 2) if self._bare_end(scene) else card_h)
        out = []
        if self.reel.tag_text:
            chip = self._label(self.reel.tag_text, px(30), self._ink_on(self.accent), accent, 12, 58, 20)
            out.append(Element(chip, px(LEFT), px(card_end - 29), .2, .5, 'pop'))
        lines, y = self._headline(hl, hs, INK + (255,), px(LEFT), px(card_end + 66), .3, shadow=False)
        out += lines
        if dl:
            details, y = self._details(dl, ds, MUTED + (255,), px(LEFT), y + px(14), .3 + len(hl) * .12 + .1, pitch=1.4)
            out += details
        if source:
            tile, base = self._text(source.upper(), px(28), accent, 'condensed-semi', shadow=False, spacing=px(2))
            out.append(Element(tile, px(LEFT - 8), y + px(20), .8, .5, 'fade'))
        return out

    def _one_line(self, text, width, pref, floor, face=True):
        """One line at the largest size that fits `width` design px; shortened with an ellipsis if even the smallest cannot."""
        for size in range(pref, floor - 1, -2):
            if text_width(text, self.px(size), face) <= self.px(width):
                return text, self.px(size)
        size, shown = self.px(floor), text
        while len(shown) > 1 and text_width(shown.rstrip() + '…', size, face) > self.px(width):
            shown = shown[:-1]
        return shown.rstrip() + '…', size

    def _wire(self, scene):
        """Wire-service report: a rule, a kicker line with source and date, then a plain headline."""
        px, accent = self.px, self.accent + (255,)
        hl, hs, dl, ds, block = self._fit(scene.headline, scene.detail_text, self.text_w,
                                          self.limit - (self.inset + 150) - 100, gap=18)
        if not block:
            return []
        top = px(self.limit) - (px(26 + 62) + block)
        out = [Element(self._box((px(self.text_w), px(6)), accent, px(3)), px(LEFT), top, .05, .6, 'wipe')]
        if self.reel.tag_text:
            tag, base = self._text(self.reel.tag_text, px(42), accent, 'condensed', spacing=px(3))
            out.append(Element(tag, px(LEFT - 8), top + px(26 + 34) - base, .15, .5, 'fade'))
        credit = '  |  '.join(filter(None, [self.reel.source.upper(), self.reel.dateline.upper()]))
        if credit:
            tile, base = self._text(credit, px(34), (255, 255, 255, 230), self.type['label'], spacing=px(1))
            out.append(Element(tile, px(DESIGN_W - self.right + 8) - tile.width, top + px(26 + 34) - base, .25, .5, 'fade'))
        lines, y = self._headline(hl, hs, WHITE, px(LEFT), top + px(26 + 62), .25)
        out += lines
        if dl:
            details, y = self._details(dl, ds, (232, 236, 242, 255), px(LEFT), y + px(18), .35 + len(hl) * .12)
            out += details
        return out

    def _editorial(self, scene):
        """Magazine page: red tab and kicker, headline, a hairline and an italic serif deck; the photo fills the rest."""
        px, accent = self.px, self.accent + (255,)
        top = self.inset + 130
        kicker = '  |  '.join(filter(None, [self.reel.tag_text, self.reel.dateline.upper()]))
        text_top = top + 80
        low, high = round(self.DH * .42), round(self.DH * .66)
        hl, hs, dl, ds, block = self._fit(scene.headline, scene.detail_text, self.text_w, high - 56 - text_top,
                                          det_pitch=1.4, gap=46)
        if not block:
            return []
        photo_top = self.DH if self._bare_end(scene) else max(low, min(high, round(text_top + block / self.s + 56)))
        self._scene_rect = (0, px(photo_top), self.W, self.H - px(photo_top), 0)
        out = [Element(self._box((px(56), px(34)), accent), px(LEFT), px(top), 0, .4, 'wipe', fades_out=False)]
        if kicker:
            tile, base = self._text(kicker, px(32), accent, self.type['label'], spacing=px(4), shadow=False)
            out.append(Element(tile, px(LEFT + 56 + 10), px(top + 30) - base, .1, .5, 'fade', fades_out=False))
        lines, y = self._headline(hl, hs, INK + (255,), px(LEFT), px(text_top), .2, shadow=False)
        out += lines
        if dl:
            out.append(Element(self._box((px(self.text_w), px(2)), (17, 24, 39, 70)), px(LEFT), y + px(16), .3 + len(hl) * .1, .5, 'wipe'))
            details, y = self._details(dl, ds, MUTED + (255,), px(LEFT), y + px(36), .4 + len(hl) * .1, pitch=1.4)
            out += details
        return out

    def _pop_art(self, scene):
        """Centered uppercase headline on a black panel, with no author/footer credit."""
        px = self.px
        low, high = round(self.DH * .43), round(self.DH * .62)
        hl, hs, dl, ds, block = self._fit(scene.headline.upper(), scene.detail_text, self.text_w,
                                          self.limit - low - 56, gap=24, max_head_lines=6)
        split = max(low, min(high, round(self.limit - block / self.s - 72)))
        self._scene_rect = (0, 0, self.W, px(split), 0)
        out, y = [], px(split + 40)
        if self._lead_plan:
            mark, face, size, height, rise = self._lead_plan
            tile, base = self._text(mark, size, self.accent + (255,), face, shadow=False)
            out.append(Element(tile, (px(DESIGN_W - self.right + LEFT) - tile.width) // 2,
                               y - (base - rise), .1, .5, 'pop'))
            y += height + px(LEAD_GAP)
        marks = self._highlights(hl)
        for index, line in enumerate(hl):
            tile, base = self._text(line, hs, WHITE, 'bold', shadow=False, mark=marks[index])
            out.append(Element(tile, (px(DESIGN_W - self.right + LEFT) - tile.width) // 2,
                               y + hs - base, .2 + index * .12, .45, 'rise'))
            y += round(hs * self.type['head_pitch'])
        if dl:
            y += px(24)
            for index, line in enumerate(dl):
                tile, base = self._text(line, ds, WHITE, 'regular', shadow=False)
                out.append(Element(tile, (px(DESIGN_W - self.right + LEFT) - tile.width) // 2,
                                   y + ds - base, .5 + index * .1, .45, 'fade'))
                y += round(ds * 1.38)
        return out

    def _identity(self):
        """Logo, brand name and (for Breaking) the live badge and ticker: present for the whole reel."""
        px, out, x = self.px, [], 56
        if self.reel.look == 'pop_art':
            label = self.reel.tag if self.reel.tag is not None else self.reel.brand or self.reel.tag_text
            if label:
                text, size = self._one_line(label, self.text_w - 60, 64, 30, 'bold')
                tile = self._label(text, size, WHITE, (86, 108, 11, 235), 28, size / self.s + 34, 36, face='bold')
                out.append(Element(tile, (self.W - tile.width) // 2, px(self.inset),
                                   0, .5, 'fade', fades_out=False))
            return out
        right = DESIGN_W - 56
        badge = None
        if self.reel.look == 'breaking':
            badge = self._label('LIVE', px(36), WHITE, self.accent + (255,), 10, 56, 46, face='condensed')
            right -= badge.width / self.s + 24            # the brand must never run under the badge
        if self.logo_path:
            with Image.open(self.logo_path) as raw:
                logo = ImageOps.contain(raw.convert('RGBA'), (px(104), px(104)), Image.Resampling.LANCZOS)
            out.append(Element(logo, px(x), px(self.inset), 0, .7, 'pop', fades_out=False, shine=True))
            x += 104 + 22
        if self.reel.brand:  # the whole width that is left, on a single line
            text, size = self._one_line(self.reel.brand, right - x, 56, 30, 'condensed')
            tile, base = self._text(text, size, self.ink, 'condensed', shadow=self.reel.look not in LIGHT_PAGE)
            middle = px(self.inset) + (px(104) if self.logo_path else px(44)) // 2
            out.append(Element(tile, px(x - 8), middle + round(size * .36) - base, .15, .5, 'rise', fades_out=False))
        if badge:
            bx, by = self.W - px(56) - badge.width, px(self.inset + 24)
            out.append(Element(badge, bx, by, .3, .4, 'pop', fades_out=False))
            out.append(Element(self._box((px(16), px(16)), WHITE, px(8)), bx + px(17), by + px(20), .7, .3, 'blink', fades_out=False))
            out.append(self._ticker())
        return out

    def _ticker(self):
        """A bar along the bottom edge of the text zone with the source and label scrolling past."""
        px, size = self.px, self.px(36)
        words = ' | '.join(filter(None, [self.reel.source.upper(), self.reel.dateline.upper(), self.reel.brand.upper(), self.reel.tag_text])) or 'NEWS'
        unit = words + '        '
        period = math.ceil(text_width(unit, size, 'condensed-semi'))
        bar = self._box((self.W + period, px(80)), self.accent + (255,))
        draw = ImageDraw.Draw(bar)
        for k in range(math.ceil((self.W + period) / period)):
            draw_line(draw, (k * period + px(30), px(80) // 2 + round(size * .36)), unit, size, WHITE, 'condensed-semi')
        return Element(bar, 0, px(self.limit - 80), 0, .5, 'marquee', fades_out=False, period=period)
