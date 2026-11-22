"""The AI Reel Studio document: scenes built from independent parts (components.py), drawn by the reel renderer.

Each scene names its own layout, camera, transition and per-role text style and animation. The renderer draws every
layout a reel uses with its own plan, and each scene with the plan of its layout; a reel that uses one layout is drawn
exactly as the matching look in Reel Studio.
"""
from collections import defaultdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator, model_validator

from .audio_presets import PRESETS
from .components import AnimationId, CameraId, EmphasisId, ExitId, LayoutId, LOOK_LAYOUT, Pacing, RECIPE_BY_ID, TextStyleId, TransitionId, layout_look
from .reel import LOOKS, MAX_SCENES, MAX_SECONDS, RATIOS, DraftInput, ElementStyle, Ratio, Reel, RenderInput, Role, Scene, SceneKind, hex_color

TRANSITION_VALUE = {'crossfade': None, 'cut': 'cut', 'slide': 'slide'}    # what a Reel scene can store
CAMERA_VALUE = {'zoom_in': 'zoom_in', 'zoom_out': 'zoom_out', 'still': 'none'}
REEL_ANIMATIONS = ('fade', 'rise', 'left', 'pop', 'wipe', 'none')
PACE = {'calm': 1.2, 'normal': 1.0, 'fast': .8}


class PartStyle(BaseModel):
    """One role's text style and animation. Unset values keep the layout's own."""
    model_config = ConfigDict(extra='forbid')
    text_style: TextStyleId | None = None
    animation: AnimationId | None = None
    scale: float | None = Field(default=None, ge=.7, le=1.3, allow_inf_nan=False)
    color: str | None = None
    background: str | None = None
    hidden: bool | None = None

    @field_validator('color', 'background')
    @classmethod
    def color_value(cls, value):
        return hex_color(value)

    def element_style(self):
        animation = self.animation if self.animation in REEL_ANIMATIONS else None  # the others go to the plan as motions
        values = dict(font=self.text_style, animation=animation, scale=self.scale, color=self.color,
                      background=self.background, hidden=self.hidden)
        return ElementStyle(**{k: v for k, v in values.items() if v is not None})


Styles = dict[Role, PartStyle]


class Background(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_id: str | None = None
    fit: Literal['cover', 'blur'] = 'cover'
    focal_x: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    focal_y: float = Field(default=.4, ge=0, le=1, allow_inf_nan=False)
    zoom: float = Field(default=1, ge=1, le=2.5, allow_inf_nan=False)
    color: str | None = None  # behind a scene with no photo
    shade: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)

    @field_validator('color')
    @classmethod
    def color_value(cls, value):
        return hex_color(value)

    @field_validator('image_id')
    @classmethod
    def asset_id(cls, value):
        return Scene.asset_id(value)


VOICE_DELAY = .25   # narration starts this long after its scene
VOICE_TAIL = .45    # and the scene runs this long after it ends
SAY_LIMIT = 280     # characters a scene's own narration may have: about 18 seconds spoken
CAPTION_SIZES = {'small': 52, 'medium': 64, 'large': 80}
QUALITY_WIDTH = {'sd': 480, 'hd': 720, 'fhd': 1080}  # the finished video's width; the height follows the ratio
STING_GAP = .15     # between the voice and the intro sound after it
END_FADE = 1.0      # the reel's sound fades out over its last second; an end sound finishes before it


class Sting(BaseModel):
    """An intro sound from My music, played before the voice starts, after it ends, or both. It plays for `seconds`
    (the first part of a longer track, faded out at the cut)."""
    model_config = ConfigDict(extra='forbid')
    asset_id: str
    seconds: float = Field(gt=0, le=8, allow_inf_nan=False)
    at: Literal['start', 'end', 'both'] = 'both'

    @field_validator('asset_id')
    @classmethod
    def asset(cls, value):
        return Scene.asset_id(value)


class VoiceClip(BaseModel):
    """A scene's narration: the audio asset, what it says, and when each word is spoken (seconds from its start)."""
    model_config = ConfigDict(extra='forbid')
    asset_id: str
    text: str = Field(max_length=600)
    seconds: float = Field(gt=0, le=19, allow_inf_nan=False)
    words: list[tuple[str, float, float]] = Field(default_factory=list, max_length=200)

    @field_validator('asset_id')
    @classmethod
    def asset(cls, value):
        return Scene.asset_id(value)


class CScene(BaseModel):
    model_config = ConfigDict(extra='forbid')
    layout: LayoutId = 'light_card'
    kind: SceneKind = 'story'
    headline: str = Field(default='', max_length=120)
    detail: str = Field(default='', max_length=160)
    stat: str = Field(default='', max_length=24)
    attribution: str = Field(default='', max_length=60)
    highlight: str = Field(default='', max_length=40)
    credit: str = Field(default='', max_length=60)
    background: Background = Field(default_factory=Background)
    camera: CameraId | None = None          # None: the recipe's turn of cameras
    transition: TransitionId | None = None  # how this scene replaces the one before; None = crossfade
    emphasis: EmphasisId | None = None      # what the text does once it is in
    exit: ExitId | None = None              # how the text leaves; None = it fades with the scene
    styles: Styles = Field(default_factory=dict)
    seconds: float | None = Field(default=None, ge=2, le=20, allow_inf_nan=False)
    text_position: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    photo_size: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    locked: bool | None = None
    voice: VoiceClip | None = None
    hide_detail: bool | None = None  # the detail stays off the screen; the voice still reads it
    say: str = Field(default='', max_length=SAY_LIMIT)  # what the voice says instead of the screen text; '' reads the screen text

    @property
    def image_id(self):
        return self.background.image_id

    def timed_seconds(self, lead=VOICE_DELAY, tail=None):
        """The scene's length: its own, or long enough to read, and never shorter than its narration, which starts `lead`
        seconds in and is followed by `tail` seconds (an intro sound after it, say)."""
        if not self.voice and not tail:
            return self.seconds
        reading = self.seconds or Scene(headline=self.headline, detail=self.detail, kind=self.kind, stat=self.stat,
                                        attribution=self.attribution).length()
        spoken = lead + self.voice.seconds if self.voice else .3
        return round(min(20, max(reading, spoken + (VOICE_TAIL if tail is None else tail))), 2)

    def to_scene(self, camera=None, lead=VOICE_DELAY, tail=None):
        """This scene as the renderer's Scene; validation of the text is the Scene's own."""
        bg = self.background
        return Scene(headline=self.headline, detail='' if self.hide_detail else self.detail, kind=self.kind, stat=self.stat, attribution=self.attribution,
                     highlight=self.highlight, credit=self.credit, image_id=bg.image_id, fit=bg.fit, focal_x=bg.focal_x,
                     focal_y=bg.focal_y, zoom=bg.zoom, backdrop=bg.color, shade=bg.shade,
                     motion=CAMERA_VALUE.get(camera or self.camera) if (camera or self.camera) else None,
                     transition=TRANSITION_VALUE.get(self.transition) if self.transition else None,
                     style={role: s.element_style() for role, s in self.styles.items()}, seconds=self.timed_seconds(lead, tail),
                     text_position=self.text_position, photo_size=self.photo_size)


class Theme(BaseModel):
    model_config = ConfigDict(extra='forbid')
    accent: str | None = None
    tag: str | None = Field(default=None, max_length=24)
    live: bool = False

    @field_validator('accent')
    @classmethod
    def accent_value(cls, value):
        return hex_color(value.lower()) if value else value


class Composition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[5] = 5
    recipe: str | None = None
    pacing: Pacing = 'normal'
    ratio: Ratio = '9:16'
    theme: Theme = Field(default_factory=Theme)
    scenes: list[CScene] = Field(default_factory=lambda: [CScene()], min_length=1, max_length=MAX_SCENES)
    styles: Styles = Field(default_factory=dict)  # every scene, unless a scene sets its own
    brand: str = Field(default='', max_length=48)
    logo_id: str | None = None
    source: str = Field(default='', max_length=90)
    dateline: str = Field(default='', max_length=32)
    show_progress: bool = True
    music: str = 'auto'
    music_id: str | None = None
    captions: Literal['off', 'voice'] = 'off'  # word-by-word captions that follow the narration
    caption_size: Literal['small', 'medium', 'large'] = 'medium'
    caption_words: int = Field(default=4, ge=2, le=6)  # at most this many words on screen at once
    sting: Sting | None = None  # a short intro sound around the narration
    quality: Literal['sd', 'hd', 'fhd'] = 'fhd'  # the finished video's resolution: 480, 720 or 1080 wide
    camera_waits: bool = True  # the camera holds still while each scene's text arrives (Reel Studio reels move at once)
    text_drift: bool = True    # every scene's text floats slowly once it is in, under its emphasis
    animate_opening: bool = True  # the first scene's text arrives like the others' (Reel Studio opens on the finished card)

    @field_validator('recipe')
    @classmethod
    def known_recipe(cls, value):
        if value is not None and value not in RECIPE_BY_ID:
            raise ValueError('Choose one of the available recipes.')
        return value

    @field_validator('music')
    @classmethod
    def known_music(cls, value):
        if value not in ('auto', 'off', 'custom', *PRESETS):
            raise ValueError('Choose one of the available music options.')
        return value

    @field_validator('logo_id', 'music_id')
    @classmethod
    def asset_id(cls, value):
        return Scene.asset_id(value)

    @model_validator(mode='after')
    def within_limit(self):
        if (self.music == 'custom') != (self.music_id is not None):
            raise ValueError('Choose a music file, or pick one of the built-in tracks.')
        if self.duration > MAX_SECONDS:
            raise ValueError(f'A reel can run up to {MAX_SECONDS} seconds. Remove a scene or shorten the text.')
        return self

    # What the render service reads from any document.
    @property
    def look(self):
        """The look whose tag, accent and music this reel starts from."""
        if self.recipe:
            return RECIPE_BY_ID[self.recipe].look
        look = layout_look(self.scenes[0].layout)
        return look if look in LOOKS else 'clean'

    @property
    def duration(self):
        return round(sum(self.scene_for(i).length() for i in range(len(self.scenes))), 1)

    def voice_lead(self, index):
        """How long into its scene the narration starts: after the intro sound, on the first scene."""
        sting = self.sting
        return VOICE_DELAY + (sting.seconds if sting and index == 0 and sting.at in ('start', 'both') else 0)

    def voice_tail(self, index):
        """What follows the narration in its scene: on the last scene, room for the intro sound before the reel fades."""
        sting = self.sting
        if sting and index == len(self.scenes) - 1 and sting.at in ('end', 'both'):
            return STING_GAP + sting.seconds + END_FADE
        return None

    def scene_for(self, index, camera=None):
        """Scene `index` as the renderer's Scene, timed around its narration and the intro sound."""
        return self.scenes[index].to_scene(camera, self.voice_lead(index), self.voice_tail(index))

    def sting_tracks(self):
        """(asset id, start in seconds) of each time the intro sound plays."""
        sting = self.sting
        if not sting:
            return []
        out = [(sting.asset_id, 0.)] if sting.at in ('start', 'both') else []
        if sting.at in ('end', 'both'):
            last = len(self.scenes) - 1
            start = sum(self.scene_for(i).length() for i in range(last))
            voice = self.scenes[last].voice
            out.append((sting.asset_id, round(start + (self.voice_lead(last) + voice.seconds + STING_GAP if voice else .3), 3)))
        return out

    def audio_tracks(self):
        """(asset id, start, length or None for all of it) of every clip laid over the music: the narration, then the
        intro sounds, which play only for their chosen length."""
        cut = self.sting.seconds if self.sting else None
        return [(a, t, None) for a, t in self.voice_tracks()] + [(a, t, cut) for a, t in self.sting_tracks()]

    @property
    def music_preset(self):
        if self.music == 'auto':
            recipe = RECIPE_BY_ID.get(self.recipe or '')
            return (recipe.music if recipe and recipe.music else None) or LOOKS[self.look]['music']
        return None if self.music in ('off', 'custom') else self.music

    def image_ids(self):
        return [s.image_id for s in self.scenes if s.image_id]

    def size(self, width):
        """Pixel size at this width, as a Reel's: H.264 needs an even height."""
        return width, round(RATIOS[self.ratio] * width / 1080 / 2) * 2

    def voice_tracks(self):
        """(asset id, start in seconds) of each scene's narration on the reel's timeline."""
        out, start = [], 0.
        for index, scene in enumerate(self.scenes):
            if scene.voice:
                out.append((scene.voice.asset_id, round(start + self.voice_lead(index), 3)))
            start += self.scene_for(index).length()
        return out

    def cameras(self):
        """Each scene's camera: its own, or the recipe's cameras in turn."""
        turn = RECIPE_BY_ID[self.recipe].cameras if self.recipe else ('zoom_in', 'zoom_out')
        return [s.camera or turn[i % len(turn)] for i, s in enumerate(self.scenes)]

    def plan_cameras(self):
        return {i: c for i, c in enumerate(self.cameras()) if c not in CAMERA_VALUE}

    def reel_styles(self):
        """Every scene's role styles: the recipe's fonts, colours and entrances, with the reel's own on top, field by field."""
        recipe = RECIPE_BY_ID.get(self.recipe or '')
        base = {role: dict(values) for role, values in (recipe.styles if recipe else {}).items()}
        for role, animation in (recipe.animations if recipe else {}).items():
            base.setdefault(role, {})['animation'] = animation
        for role, own in self.styles.items():
            base[role] = {**base.get(role, {}), **own.model_dump(exclude_none=True)}
        return {role: PartStyle(**values) for role, values in base.items()}

    def animation_for(self, scene, role):
        """The role's entrance in this scene: its own, else the reel's or recipe's where that suits the scene's kind."""
        from .components import part
        own, base = scene.styles.get(role), self.reel_styles().get(role)
        if own and own.animation:
            return own.animation
        animation = base.animation if base else None
        return animation if animation and scene.kind in part('animation', animation).applies_to else None

    def plan_emphases(self):
        return {i: s.emphasis for i, s in enumerate(self.scenes) if s.emphasis}

    def plan_exits(self):
        return {i: s.exit for i, s in enumerate(self.scenes) if s.exit and s.exit != 'fade'}

    def plan_transitions(self):
        return {i: s.transition for i, s in enumerate(self.scenes) if s.transition and s.transition not in TRANSITION_VALUE}

    def plan_motions(self):
        """(scene, role) -> an entrance a Reel cannot store; a scene's own style wins over the reel's."""
        motions, reel = {}, self.reel_styles()
        for index, scene in enumerate(self.scenes):
            for role in {**reel, **scene.styles}:
                animation = self.animation_for(scene, role)
                if animation and animation not in REEL_ANIMATIONS:
                    motions[(index, role)] = animation
        return motions

    def reel_for(self, layout=None):
        """The renderer's Reel for the scenes drawn with `layout` (all scenes when None); other scenes keep their length, empty."""
        cameras = self.cameras()
        scenes = []
        for index, scene in enumerate(self.scenes):
            drawn = self.scene_for(index, cameras[index])
            if layout is None or scene.layout == layout:
                scenes.append(drawn)
            else:  # a stand-in of the same length, so the timelines line up; another plan draws this scene
                scenes.append(Scene(seconds=drawn.length(), transition=drawn.transition))
        # The accent and music follow the recipe whatever the layout; the plan's layout decides the drawing.
        # AI Studio draws no section label (POLITICS, NEWS…) unless the reel came from Reel Studio with one.
        recipe = RECIPE_BY_ID.get(self.recipe or '')
        return Reel(look=self.look, tag=self.theme.tag or '', accent=self.theme.accent or (recipe and recipe.accent),
                    live=self.theme.live, scenes=scenes,
                    brand=self.brand, logo_id=self.logo_id, source=self.source, dateline=self.dateline, ratio=self.ratio,
                    motion='auto', show_progress=self.show_progress, music=self.music, music_id=self.music_id,
                    style={role: s.element_style() for role, s in self.reel_styles().items()})


def from_reel(reel: Reel) -> Composition:
    """A Reel Studio reel as a composition: every scene in the layout of its look, nothing else changed."""
    layout = LOOK_LAYOUT[reel.look]

    def styles(values):
        return {role: PartStyle(text_style=s.font, animation=s.animation, scale=s.scale, color=s.color, background=s.background,
                                hidden=s.hidden) for role, s in values.items()}
    camera_of = {'zoom_in': 'zoom_in', 'zoom_out': 'zoom_out', 'none': 'still'}
    reel_camera = None if reel.motion == 'auto' else camera_of[reel.motion]
    scenes = []
    for index, s in enumerate(reel.scenes):
        auto = 'zoom_out' if index % 2 else 'zoom_in'  # what Reel Studio's auto motion does
        scenes.append(CScene(layout=layout, kind=s.kind, headline=s.headline, detail=s.detail, stat=s.stat, attribution=s.attribution,
                             highlight=s.highlight, credit=s.credit,
                             background=Background(image_id=s.image_id, fit=s.fit, focal_x=s.focal_x, focal_y=s.focal_y, zoom=s.zoom,
                                                   color=s.backdrop, shade=s.shade),
                             camera=camera_of[s.motion] if s.motion else reel_camera or auto,
                             transition={None: None, 'fade': None, 'cut': 'cut', 'slide': 'slide'}[s.transition],
                             styles=styles(s.style), seconds=s.seconds, text_position=s.text_position, photo_size=s.photo_size,
                             locked=s.locked))
    return Composition(recipe=reel.look, ratio=reel.ratio, theme=Theme(accent=reel.accent, tag=reel.tag_text, live=reel.live), scenes=scenes,
                       styles=styles(reel.style), brand=reel.brand, logo_id=reel.logo_id, source=reel.source, dateline=reel.dateline,
                       show_progress=reel.show_progress, music=reel.music, music_id=reel.music_id, camera_waits=False,
                       text_drift=False, animate_opening=False)


class CompositionPlan:
    """Draws a composition: one renderer Plan per layout it uses; each scene comes from the plan of its own layout."""

    def __init__(self, composition: Composition, width, images, logo=None, fps=30):
        from .renderer import Plan
        self.composition = composition
        extra = dict(cameras=composition.plan_cameras(), motions=composition.plan_motions(), transitions=composition.plan_transitions(),
                     pace=PACE[composition.pacing], emphases=composition.plan_emphases(), exits=composition.plan_exits(),
                     camera_waits=composition.camera_waits, text_drift=composition.text_drift,
                     animate_opening=composition.animate_opening)
        layouts = list(dict.fromkeys(s.layout for s in composition.scenes))
        if len(layouts) == 1:
            plans = {layouts[0]: Plan(composition.reel_for(), width, images, logo, fps, layout=layout_look(layouts[0]), **extra)}
        else:
            plans = {}
            for layout in layouts:  # stand-in scenes have no text, so a fitting error always names a scene of this layout
                plans[layout] = Plan(composition.reel_for(layout), width, images, logo, fps, layout=layout_look(layout), **extra)
        self.scene_plans = [plans[s.layout] for s in composition.scenes]
        for plan in plans.values():
            plan.background_of = self.background
        self.ref = self.scene_plans[0]
        self.W, self.H, self.fps = self.ref.W, self.ref.H, fps
        self.lengths, self.starts, self.duration = self.ref.lengths, self.ref.starts, self.ref.duration
        self._chunk_cache, self._bands, self._caption_tiles = {}, {}, {}

    def background(self, index, local):
        return self.scene_plans[index].background(index, local)

    def locate(self, t):
        return self.ref.locate(t)

    def frame(self, t):
        """As Plan.frame: the opening frame is a settled later frame, drawn by the plan of the scene it falls in."""
        from .renderer import OPENING_COVER_SECONDS
        opening = 0 <= t < 1 / self.fps
        if opening:
            t = OPENING_COVER_SECONDS if self.duration > OPENING_COVER_SECONDS else self.settled(0)
        index = self.locate(t)[0]
        image = self.scene_plans[index]._timeline_frame(t)
        if self.composition.captions == 'voice' and not opening:
            self._caption(image, index, t - self.starts[index] - self.composition.voice_lead(index))
        return image

    # ---- captions ------------------------------------------------------------
    def _chunks(self, index):
        if index not in self._chunk_cache:
            from .voice_timing import caption_chunks
            voice = self.composition.scenes[index].voice
            self._chunk_cache[index] = caption_chunks([tuple(w) for w in voice.words], self.composition.caption_words) if voice else []
        return self._chunk_cache[index]

    def _band(self, index):
        """The middle of the widest free stretch of frame between the scene's text and the bottom of the safe area."""
        if index not in self._bands:
            plan = self.scene_plans[index]
            top, bottom = (plan.inset + 150) / plan.DH, plan.limit / plan.DH
            taken = sorted((b['y'], b['y'] + b['h']) for b in plan.layout(index)['boxes'] if b['role'] in ('headline', 'detail', 'lead', 'label'))
            gaps, at = [], top
            for a, b in taken:
                if a > at:
                    gaps.append((at, min(a, bottom)))
                at = max(at, b)
            gaps.append((at, bottom))
            best = max(gaps, key=lambda g: g[1] - g[0])
            self._bands[index] = (best[0] + best[1]) / 2 if best[1] - best[0] > .07 else bottom - .04
        return self._bands[index]

    def _caption(self, image, index, local):
        chunks = self._chunks(index)
        current = next(((i, c) for i, c in enumerate(chunks) if c[1] <= local < c[2] + .15), None)
        if not current:
            return
        number, (words, _, _) = current
        spoken = max(0, sum(1 for w in self.composition.scenes[index].voice.words if w[1] <= local) - sum(len(c[0]) for c in chunks[:number]) - 1)
        spoken = min(spoken, len(words) - 1)
        key = (index, number, spoken)
        if key not in self._caption_tiles:
            plan = self.scene_plans[index]
            text = ' '.join(words)
            start = len(' '.join(words[:spoken])) + (1 if spoken else 0)
            size = plan.px(CAPTION_SIZES[self.composition.caption_size])
            tile, _ = plan._text(text, size, (255, 255, 255, 255), 'bold', mark=(start, start + len(words[spoken])), stroke=plan.px(5))
            if tile.width > plan.W - plan.px(80):  # long words: shrink to the safe width
                tile = tile.resize((plan.W - plan.px(80), round(tile.height * (plan.W - plan.px(80)) / tile.width)))
            self._caption_tiles[key] = tile
        tile = self._caption_tiles[key]
        x = (self.W - tile.width) // 2
        y = round(self._band(index) * self.H - tile.height / 2)
        image.paste(tile, (x, y), tile)

    def settled(self, index):
        return self.scene_plans[index].settled(index)

    def layout(self, index):
        return self.scene_plans[index].layout(index)


def group_by_layout(composition):
    groups = defaultdict(list)
    for index, scene in enumerate(composition.scenes):
        groups[scene.layout].append(index)
    return dict(groups)


def _version(value):
    version = value.get('version') if isinstance(value, dict) else getattr(value, 'version', 4)
    return 'composition' if version == 5 else 'reel'


# A request body that is either kind of document, told apart by its version; a Reel's own errors stay its own.
AnyDocument = Annotated[Annotated[Reel, Tag('reel')] | Annotated[Composition, Tag('composition')], Discriminator(_version)]


class DocumentRenderInput(RenderInput):
    reel: AnyDocument


class DocumentDraftInput(DraftInput):
    """A Reel Studio reel or an AI Reel Studio composition; the store keeps either."""
    reel: AnyDocument


def parse_document(value):
    """A stored reel or composition, by its version."""
    return Composition(**value) if isinstance(value, dict) and value.get('version') == 5 else Reel(**value)


def plan_for(document, width, images, logo=None, fps=30):
    from .renderer import Plan
    return CompositionPlan(document, width, images, logo, fps) if isinstance(document, Composition) else Plan(document, width, images, logo, fps)
