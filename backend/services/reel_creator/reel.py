"""The reel document: a list of scenes plus a look. Simple mode fills it in; advanced mode adds styles over the look."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .audio_presets import PRESETS

# Defaults per look; users may override the tag and accent only.
LOOKS = {
    'breaking': dict(name='Breaking', tag='BREAKING NEWS', accent='#e11d2e', music='breaking'),
    'hot': dict(name='Hot', tag='HOT NEWS', accent='#ff9f0a', music='live_update'),
    'politics': dict(name='Politics', tag='POLITICS', accent='#e8c07a', music='washington_watch'),
    'clean': dict(name='Clean', tag='NEWS', accent='#1d6fe8', music='headlines'),
    'wire': dict(name='Wire', tag='LATEST', accent='#f2780c', music='urgent_wire'),
    'pop_art': dict(name='Pop Art', tag='SPOTLIGHT', accent='#ccff00', music='headlines'),
    'editorial': dict(name='Editorial', tag='ANALYSIS', accent='#d7261e', music='major_headline'),
}
MAX_SCENES = 8
RATIOS = {'9:16': 1920, '4:5': 1350}  # design height at a 1080 design width
MAX_SECONDS = 90
Look = Literal['breaking', 'hot', 'politics', 'clean', 'wire', 'editorial', 'pop_art']
Motion = Literal['auto', 'zoom_in', 'zoom_out', 'none']  # auto alternates push-in and pull-out by scene
Ratio = Literal['9:16', '4:5']
SceneKind = Literal['story', 'stat', 'quote', 'end']
# Advanced mode: elements a look draws, by role. A style overrides the look for one role; unset fields keep the look's.
Role = Literal['headline', 'detail', 'lead', 'label', 'brand', 'logo', 'ticker']
Face = Literal['bold', 'regular', 'display', 'condensed', 'condensed-semi', 'serif', 'serif-italic']
Anim = Literal['fade', 'rise', 'left', 'pop', 'wipe', 'none']
Transition = Literal['fade', 'cut', 'slide']
HEX = r'#[a-f0-9]{6}'


def hex_color(value):
    if value is not None and not re.fullmatch(HEX, value.lower()):
        raise ValueError('Choose a six-digit color')
    return value.lower() if value else None


def highlight_span(text, phrase):
    """(start, end) of `phrase` as whole words in `text` with its spacing collapsed, ignoring case; or None."""
    text, phrase = ' '.join(text.split()), ' '.join(phrase.split())
    if not phrase:
        return None
    match = re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.IGNORECASE)
    return match.span() if match else None


class ElementStyle(BaseModel):
    """One role's overrides. None keeps the look's own value, so an empty style draws exactly the simple-mode reel."""
    model_config = ConfigDict(extra='forbid')
    font: Face | None = None
    scale: float | None = Field(default=None, ge=.7, le=1.3, allow_inf_nan=False)  # times the look's preferred size; text still shrinks to fit
    color: str | None = None
    background: str | None = None  # a box behind each line of text, or the fill of a label or ticker
    animation: Anim | None = None
    delay: float | None = Field(default=None, ge=0, le=3, allow_inf_nan=False)  # seconds added to the look's entrance time
    duration: float | None = Field(default=None, ge=.1, le=2, allow_inf_nan=False)
    hidden: bool | None = None

    @field_validator('color', 'background')
    @classmethod
    def color_value(cls, value):
        return hex_color(value)

    def over(self, base):
        """This style laid over `base`: set fields win."""
        return base.model_copy(update=self.model_dump(exclude_none=True)) if base else self


Styles = dict[Role, ElementStyle]


def compact(styles):
    """Only the fields a style sets: an unset field must not read as an override when merged in the app."""
    return {role: style.model_dump(exclude_none=True) for role, style in styles.items()}


class Scene(BaseModel):
    model_config = ConfigDict(extra='forbid')
    headline: str = Field(default='', max_length=120)
    detail: str = Field(default='', max_length=160)
    image_id: str | None = None
    focal_x: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    focal_y: float = Field(default=.4, ge=0, le=1, allow_inf_nan=False)
    seconds: float | None = Field(default=None, ge=2, le=20, allow_inf_nan=False)  # None = timed by reading length
    fit: Literal['cover', 'blur'] = 'cover'  # blur: the whole photo over a blurred copy of itself, nothing cropped away
    # story: headline and detail. stat: a big figure above a short label (the headline). quote: the headline
    # is the speaker's words, credited to `attribution`. end: the closing card (a question, or where the full story is).
    kind: SceneKind = 'story'
    stat: str = Field(default='', max_length=24)
    attribution: str = Field(default='', max_length=60)
    highlight: str = Field(default='', max_length=40)  # words of the headline drawn in the accent color
    # Advanced mode. Unset values follow the reel and the look.
    style: Styles = Field(default_factory=dict)  # this scene only; laid over the reel's styles
    motion: Literal['zoom_in', 'zoom_out', 'none'] | None = None
    transition: Transition | None = None  # how this scene replaces the one before it; None = crossfade
    shade: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)  # times the look's darkening behind text
    backdrop: str | None = None  # color behind a scene with no photo; None = the accent-tinted dark gradient
    # Where the text block sits in the safe area, 0 = highest, 1 = lowest; None = where the look puts it. Breaking and Wire only.
    text_position: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @field_validator('backdrop')
    @classmethod
    def backdrop_color(cls, value):
        return hex_color(value)

    @field_serializer('style')
    def compact_style(self, value):
        return compact(value)

    def style_for(self, reel_styles, role):
        """The merged style of one role here, or None when neither the reel nor this scene sets one."""
        base, own = reel_styles.get(role), self.style.get(role)
        return own.over(base) if own else base

    @field_validator('headline', 'detail')
    @classmethod
    def clean_text(cls, value):
        if any(ord(c) < 32 and c != '\n' for c in value):
            raise ValueError('Unsupported control character')
        return value.strip()

    @field_validator('stat', 'attribution', 'highlight')
    @classmethod
    def clean_line(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Unsupported control character')
        return ' '.join(value.split())

    @property
    def lead(self):
        """The large element above the headline, or '' for a plain story scene."""
        if self.kind == 'stat':
            return self.stat
        if self.kind == 'quote':
            return '“'
        if self.kind == 'end':
            return '?' if self.headline.rstrip().endswith('?') else '→'  # the comment button sits to the right
        return ''

    @property
    def detail_text(self):
        """A quote is credited on the detail line, before any further detail."""
        credit = '— ' + self.attribution if self.kind == 'quote' and self.attribution else ''
        return '\n'.join(filter(None, [credit, self.detail]))

    @field_validator('image_id')
    @classmethod
    def asset_id(cls, value):
        if value is not None and not re.fullmatch(r'[a-f0-9]{32}', value):
            raise ValueError('Invalid asset ID')
        return value

    def length(self):
        """Seconds on screen: enough time to read, within a comfortable range."""
        if self.seconds is not None:
            return self.seconds
        words = len(self.headline.split()) + len(self.detail.split()) + len(self.attribution.split()) * (self.kind == 'quote') + (self.kind == 'stat' and bool(self.stat))
        return round(min(8.0, max(3.5, 2.6 + .3 * words)), 1)


class Reel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[4] = 4
    look: Look = 'breaking'
    tag: str | None = Field(default=None, max_length=24)  # None = the look's own label; '' hides it
    accent: str | None = None  # None = the look's own color
    scenes: list[Scene] = Field(default_factory=lambda: [Scene()], min_length=1, max_length=MAX_SCENES)
    brand: str = Field(default='', max_length=48)
    logo_id: str | None = None
    source: str = Field(default='', max_length=90)
    dateline: str = Field(default='', max_length=32)  # shown by Wire, Editorial and the Breaking ticker
    ratio: Ratio = '9:16'
    motion: Motion = 'auto'
    show_progress: bool = True
    music: str = 'auto'  # auto = the look's own track | off | a bundled track id | custom
    music_id: str | None = None  # the uploaded audio asset when music is 'custom'
    style: Styles = Field(default_factory=dict)  # advanced mode: every scene, unless a scene sets its own

    @field_serializer('style')
    def compact_style(self, value):
        return compact(value)

    @field_validator('logo_id', 'music_id')
    @classmethod
    def asset_id(cls, value):
        return Scene.asset_id(value)

    @field_validator('look', mode='before')
    @classmethod
    def renamed_look(cls, value):
        return 'politics' if value == 'story' else value  # drafts saved before the rename

    @field_validator('music')
    @classmethod
    def known_music(cls, value):
        if value not in ('auto', 'off', 'custom', *PRESETS):
            raise ValueError('Choose one of the available music options.')
        return value

    @field_validator('accent')
    @classmethod
    def color(cls, value):
        if value is not None and not re.fullmatch(r'#[a-fA-F0-9]{6}', value):
            raise ValueError('Choose a six-digit accent color')
        return value.lower() if value else value

    @field_validator('tag', 'brand', 'source', 'dateline')
    @classmethod
    def clean_text(cls, value):
        if value is None:
            return value
        if any(ord(c) < 32 for c in value):
            raise ValueError('Unsupported control character')
        return value.strip()

    @model_validator(mode='after')
    def within_limit(self):
        if (self.music == 'custom') != (self.music_id is not None):
            raise ValueError('Choose a music file, or pick one of the built-in tracks.')
        if self.duration > MAX_SECONDS:
            raise ValueError(f'A reel can run up to {MAX_SECONDS} seconds. Remove a scene or shorten the text.')
        return self

    @property
    def duration(self):
        return round(sum(scene.length() for scene in self.scenes), 1)

    @property
    def tag_text(self):
        return LOOKS[self.look]['tag'] if self.tag is None else self.tag

    @property
    def accent_color(self):
        return self.accent or LOOKS[self.look]['accent']

    @property
    def music_preset(self):
        """Bundled track id, or None for silence or an uploaded file."""
        if self.music == 'auto':
            return LOOKS[self.look]['music']
        return None if self.music in ('off', 'custom') else self.music

    @property
    def design_height(self):
        return RATIOS[self.ratio]

    def size(self, width):
        """Pixel size at this width; H.264 needs an even height."""
        return width, round(self.design_height * width / 1080 / 2) * 2

    def image_ids(self):
        return [s.image_id for s in self.scenes if s.image_id]


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    reel: Reel
    caption: str = Field(default='', max_length=8000)
    goal: Literal['engage', 'readmore'] = 'engage'
    comment: str = Field(default='', max_length=2000)  # first comment: the article link for read-more reels
    hooks: list[str] = Field(default_factory=list, max_length=4)
    source_text: str = Field(default='', max_length=120000)
    source_url: str = Field(default='', max_length=2048)
    source_title: str = Field(default='', max_length=300)  # kept so later writing reuses the article without fetching it again
    source_publisher: str = Field(default='', max_length=100)


class RenderInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[a-f0-9-]{32,36}$')
    reel: Reel
    kind: Literal['final', 'preview'] = 'final'
    caption: str = Field(default='', max_length=8000)
