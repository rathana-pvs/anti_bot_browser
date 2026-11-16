"""The AI Reel Studio document: scenes built from independent parts (components.py), drawn by the reel renderer.

Each scene names its own layout, camera, transition and per-role text style and animation. The renderer draws every
layout a reel uses with its own plan, and each scene with the plan of its layout; a reel that uses one layout is drawn
exactly as the matching look in Reel Studio.
"""
from collections import defaultdict
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator, model_validator

from .audio_presets import PRESETS
from .components import AnimationId, CameraId, LayoutId, LOOK_LAYOUT, Pacing, RECIPE_BY_ID, TextStyleId, TransitionId, layout_look
from .reel import LOOKS, MAX_SCENES, MAX_SECONDS, ElementStyle, Ratio, Reel, RenderInput, Role, Scene, SceneKind, hex_color

TRANSITION_VALUE = {'crossfade': None, 'cut': 'cut', 'slide': 'slide'}
CAMERA_VALUE = {'zoom_in': 'zoom_in', 'zoom_out': 'zoom_out', 'still': 'none'}


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
        values = dict(font=self.text_style, animation=self.animation, scale=self.scale, color=self.color,
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
    styles: Styles = Field(default_factory=dict)
    seconds: float | None = Field(default=None, ge=2, le=20, allow_inf_nan=False)
    text_position: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    photo_size: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    locked: bool | None = None

    @property
    def image_id(self):
        return self.background.image_id

    def to_scene(self, camera=None):
        """This scene as the renderer's Scene; validation of the text is the Scene's own."""
        bg = self.background
        return Scene(headline=self.headline, detail=self.detail, kind=self.kind, stat=self.stat, attribution=self.attribution,
                     highlight=self.highlight, credit=self.credit, image_id=bg.image_id, fit=bg.fit, focal_x=bg.focal_x,
                     focal_y=bg.focal_y, zoom=bg.zoom, backdrop=bg.color, shade=bg.shade,
                     motion=CAMERA_VALUE[camera or self.camera] if (camera or self.camera) else None,
                     transition=TRANSITION_VALUE[self.transition] if self.transition else None,
                     style={role: s.element_style() for role, s in self.styles.items()}, seconds=self.seconds,
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
        return RECIPE_BY_ID[self.recipe].look if self.recipe else layout_look(self.scenes[0].layout)

    @property
    def duration(self):
        return round(sum(s.to_scene().length() for s in self.scenes), 1)

    @property
    def music_preset(self):
        if self.music == 'auto':
            return LOOKS[self.look]['music']
        return None if self.music in ('off', 'custom') else self.music

    def image_ids(self):
        return [s.image_id for s in self.scenes if s.image_id]

    def cameras(self):
        """Each scene's camera: its own, or the recipe's cameras in turn."""
        turn = RECIPE_BY_ID[self.recipe].cameras if self.recipe else ('zoom_in', 'zoom_out')
        return [s.camera or turn[i % len(turn)] for i, s in enumerate(self.scenes)]

    def reel_for(self, layout=None):
        """The renderer's Reel for the scenes drawn with `layout` (all scenes when None); other scenes keep their length, empty."""
        cameras = self.cameras()
        scenes = []
        for index, scene in enumerate(self.scenes):
            drawn = scene.to_scene(cameras[index])
            if layout is None or scene.layout == layout:
                scenes.append(drawn)
            else:  # a stand-in of the same length, so the timelines line up; another plan draws this scene
                scenes.append(Scene(seconds=drawn.length(), transition=drawn.transition))
        # The tag, accent and music follow the recipe whatever the layout; the plan's layout decides the drawing.
        return Reel(look=self.look, tag=self.theme.tag, accent=self.theme.accent, live=self.theme.live, scenes=scenes,
                    brand=self.brand, logo_id=self.logo_id, source=self.source, dateline=self.dateline, ratio=self.ratio,
                    motion='auto', show_progress=self.show_progress, music=self.music, music_id=self.music_id,
                    style={role: s.element_style() for role, s in self.styles.items()})


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
    return Composition(recipe=reel.look, ratio=reel.ratio, theme=Theme(accent=reel.accent, tag=reel.tag, live=reel.live), scenes=scenes,
                       styles=styles(reel.style), brand=reel.brand, logo_id=reel.logo_id, source=reel.source, dateline=reel.dateline,
                       show_progress=reel.show_progress, music=reel.music, music_id=reel.music_id)


class CompositionPlan:
    """Draws a composition: one renderer Plan per layout it uses; each scene comes from the plan of its own layout."""

    def __init__(self, composition: Composition, width, images, logo=None, fps=30):
        from .renderer import Plan
        self.composition = composition
        layouts = list(dict.fromkeys(s.layout for s in composition.scenes))
        if len(layouts) == 1:
            plans = {layouts[0]: Plan(composition.reel_for(), width, images, logo, fps, layout=layout_look(layouts[0]))}
        else:
            plans = {}
            for layout in layouts:
                try:
                    plans[layout] = Plan(composition.reel_for(layout), width, images, logo, fps, layout=layout_look(layout))
                except ValueError as exc:  # stand-in scenes have no text, so any error names a scene of this layout
                    raise ValueError(str(exc)) from None
        self.scene_plans = [plans[s.layout] for s in composition.scenes]
        for plan in plans.values():
            plan.background_of = self.background
        self.ref = self.scene_plans[0]
        self.W, self.H, self.fps = self.ref.W, self.ref.H, fps
        self.lengths, self.starts, self.duration = self.ref.lengths, self.ref.starts, self.ref.duration

    def background(self, index, local):
        return self.scene_plans[index].background(index, local)

    def locate(self, t):
        return self.ref.locate(t)

    def frame(self, t):
        """As Plan.frame: the opening frame is a settled later frame, drawn by the plan of the scene it falls in."""
        from .renderer import OPENING_COVER_SECONDS
        if 0 <= t < 1 / self.fps:
            t = OPENING_COVER_SECONDS if self.duration > OPENING_COVER_SECONDS else self.settled(0)
        return self.scene_plans[self.locate(t)[0]]._timeline_frame(t)

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


def parse_document(value):
    """A stored reel or composition, by its version."""
    return Composition(**value) if isinstance(value, dict) and value.get('version') == 5 else Reel(**value)


def plan_for(document, width, images, logo=None, fps=30):
    from .renderer import Plan
    return CompositionPlan(document, width, images, logo, fps) if isinstance(document, Composition) else Plan(document, width, images, logo, fps)
