import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .news_ai import EditorialDraft
from .photo_timing import photo_seconds


class CropRect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    left: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    top: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    right: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    bottom: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def usable_area(self):
        if self.right - self.left < .05 - 1e-9 or self.bottom - self.top < .05 - 1e-9:
            raise ValueError('Keep at least 5% of the image width and height when cropping.')
        return self


class PhotoFraming(BaseModel):
    model_config = ConfigDict(extra='forbid')
    focal_x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    focal_y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    framing: Literal['inherit', 'fit', 'fill'] = 'inherit'
    zoom: float = Field(default=1, ge=0.25, le=3, allow_inf_nan=False)
    offset_x: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    offset_y: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)


class Photo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    headline: str | None = Field(default=None, max_length=240)
    caption: str | None = Field(default=None, max_length=200)
    duration_seconds: int | None = Field(default=None, ge=3, le=10, strict=True)
    image_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    movement: Literal['auto', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'still'] = 'auto'
    focal_x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    focal_y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    framing: Literal['inherit', 'fit', 'fill'] = 'inherit'
    zoom: float = Field(default=1, ge=0.25, le=3, allow_inf_nan=False)
    offset_x: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    offset_y: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    crop: CropRect | None = None
    framing_by_ratio: dict[Literal['9:16', '4:5', '1:1'], PhotoFraming] = Field(default_factory=dict)

    @field_validator('headline', 'caption')
    @classmethod
    def headline_text(cls, value):
        if value is not None and any(ord(c) < 32 and c != '\n' for c in value):
            raise ValueError('Unsupported control character')
        return value.strip() if value is not None else None


class Composition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1, 2, 3] = 1
    layout_revision: Literal[1, 2] = 1
    lens_theme: Literal['light', 'dark'] = 'light'
    photo_layout: Literal['template', 'full_bleed', 'adjustable'] = 'template'
    photo_panel_end: float | None = Field(default=None,ge=.4,le=.72,allow_inf_nan=False)
    motion_intensity: Literal['subtle', 'balanced', 'dramatic'] = 'balanced'
    show_progress: bool = True
    progress_position: Literal['top', 'bottom'] = 'top'
    photos: list[Photo] = Field(default_factory=list, max_length=5)
    motion_style: Literal['bulletin', 'politics', 'story'] = 'bulletin'
    text_animation: Literal['auto', 'slide', 'slide_down', 'slide_left', 'slide_right', 'fade', 'none'] = 'auto'
    auto_timing: bool = False
    slide_seconds: Literal[3, 5, 7, 10] | None = None
    slideshow_effect: Literal['auto', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'fade', 'still'] = 'auto'
    transition: Literal['fade', 'cut'] = 'fade'
    show_subtitle: bool = True
    aspect_ratio: Literal['9:16', '4:5', '1:1'] = '9:16'
    resolution: Literal[720, 1080] = 1080
    fps: Literal[24, 30, 60] = 30
    template: Literal['bbc', 'economist', 'reuters', 'ap_wire', 'lens_report', 'daily_brief', 'newsroom', 'editorial', 'brief'] = 'bbc'
    news_design: Literal['classic', 'clean'] = 'classic'
    headline_emphasis: str = Field(default='', max_length=60)
    headline_size: Literal['auto', 'small', 'medium', 'large', 'xlarge'] = 'auto'
    subtitle_size: Literal['auto', 'small', 'medium', 'large'] = 'auto'
    image_id: str | None = None
    logo_id: str | None = None
    audio_id: str | None = None
    narration_enabled: bool = False
    narration_text: str = Field(default='', max_length=1500)
    narration_voice: Literal['en-US-GuyNeural', 'en-US-JennyNeural'] = 'en-US-GuyNeural'
    narration_rate: Literal['-10%', '+0%', '+10%'] = '+0%'
    narration_id: str | None = None
    narration_duration: float | None = Field(default=None, gt=0, le=600, allow_inf_nan=False)
    audio_preset: Literal['none', 'breaking', 'headlines', 'politics', 'urgent_wire', 'alert_desk', 'live_update', 'developing_story', 'major_headline', 'washington_watch'] = 'none'
    treatment: Literal['fit', 'fill'] = 'fit'
    headline: str = Field(default='', max_length=240)
    supporting: str = Field(default='', max_length=200)
    category: str = Field(default='', max_length=32)
    brand: str = Field(default='', max_length=48)
    source: str = Field(default='', max_length=90)
    accent: str = '#ef4444'
    duration: int = Field(default=10, ge=3, le=60, strict=True)
    motion: bool = True
    focal_x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    focal_y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    volume: float = Field(default=0.2, ge=0, le=0.7, allow_inf_nan=False)

    @model_validator(mode='after')
    def scene_consistency(self):
        if (self.template == 'lens_report') != (self.version == 3):
            raise ValueError('Lens Report uses composition version 3; other layouts use version 1 or 2.')
        if self.template == 'lens_report' and self.photo_layout != 'template' and self.layout_revision != 2:
            raise ValueError('Flexible Lens Report requires layout revision 2.')
        if self.template == 'lens_report' and self.aspect_ratio not in ('9:16', '4:5'):
            raise ValueError('Lens Report supports 9:16 and 4:5.')
        if self.auto_timing and self.version < 2:
            raise ValueError('Enable story motion before using Auto timing.')
        if not self.auto_timing and self.duration not in (5, 10, 15, 20, 25, 30, 45, 60):
            raise ValueError('Choose an available video duration.')
        if self.auto_timing and not self.photos and self.image_id:
            self.photos = [Photo(image_id=self.image_id, focal_x=self.focal_x, focal_y=self.focal_y)]
        if self.auto_timing and self.photos:
            self.duration = sum(photo_seconds(self))
        if self.photos:
            if self.version < 2:
                raise ValueError('Enable story motion before adding photos.')
            if self.image_id != self.photos[0].image_id:
                raise ValueError('The first photo must match the cover image.')
            if not self.auto_timing and self.slide_seconds is not None and self.duration < len(self.photos) * self.slide_seconds:
                raise ValueError('Choose enough video time to show every photo once.')
            if not self.auto_timing and self.slide_seconds is None and self.duration < len(self.photos) * 3:
                raise ValueError('Allow at least three seconds per photo. Choose a longer duration.')
        return self

    @field_validator('image_id', 'logo_id', 'audio_id', 'narration_id')
    @classmethod
    def asset_id(cls, value):
        if value is not None and not re.fullmatch(r'[a-f0-9]{32}', value):
            raise ValueError('Invalid asset ID')
        return value

    @field_validator('accent')
    @classmethod
    def color(cls, value):
        if not re.fullmatch(r'#[a-fA-F0-9]{6}', value):
            raise ValueError('Choose a six-digit accent color')
        return value.lower()

    @field_validator('headline', 'supporting', 'category', 'brand', 'source', 'headline_emphasis', 'narration_text')
    @classmethod
    def text(cls, value):
        if any(ord(c) < 32 and c != '\n' for c in value):
            raise ValueError('Unsupported control character')
        return value.strip()


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    composition: Composition
    editorial: EditorialDraft | None = None


class RenderInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[a-f0-9-]{32,36}$')
    composition: Composition
    kind: Literal['final', 'preview'] = 'final'
    post_caption: str = Field(default='', max_length=8000)
