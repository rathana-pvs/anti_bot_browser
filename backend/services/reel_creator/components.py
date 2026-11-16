"""The independent parts an AI Reel Studio scene is built from, and the recipes that combine them.

Every part is listed once here with when to use it. The composition schema, the composition renderer, the quality
checker and the AI composer all read these lists, so adding a part means: draw it, test it, list it here.
"""
from dataclasses import asdict, dataclass, field
from typing import Literal, get_args

LayoutId = Literal['news_strap', 'big_headline', 'photo_panel', 'light_card', 'wire_report', 'magazine', 'pop_collage']
CameraId = Literal['zoom_in', 'zoom_out', 'still']
TransitionId = Literal['crossfade', 'cut', 'slide']
AnimationId = Literal['fade', 'rise', 'left', 'pop', 'wipe', 'none']
TextStyleId = Literal['bold', 'regular', 'display', 'condensed', 'condensed-semi', 'serif', 'serif-italic']
Pacing = Literal['calm', 'normal', 'fast']
KINDS = frozenset({'story', 'stat', 'quote', 'end'})


@dataclass(frozen=True)
class Part:
    id: str
    kind: str                          # layout | camera | transition | animation | text_style
    label: str
    use_when: str
    avoid_when: str = ''
    applies_to: frozenset = KINDS      # scene kinds it suits
    mood: tuple = ()
    needs_photo: bool = False
    loud: bool = False                 # draws attention; the quality checker allows one per scene
    look: str = ''                     # layouts: the renderer layout that draws it

    def public(self):
        value = asdict(self)
        value.update(applies_to=sorted(self.applies_to), mood=list(self.mood))
        return value


PARTS = (
    # Layouts: where the photo and the text sit in the frame.
    Part('news_strap', 'layout', 'News strap', 'Breaking or developing news over a full photo, with a ticker.', 'Calm explainers.',
         mood=('urgent',), look='breaking'),
    Part('big_headline', 'layout', 'Big headline', 'A short, strong headline over a photo; trending stories.', 'Long headlines.',
         mood=('energetic',), look='hot'),
    Part('photo_panel', 'layout', 'Photo and panel', 'Photo on top, text on a panel below: politics and analysis.', mood=('serious',), look='politics'),
    Part('light_card', 'layout', 'Light card', 'A photo card on a light page, like a news site: neutral updates.', mood=('calm', 'explainer'), look='clean'),
    Part('wire_report', 'layout', 'Wire report', 'A straight factual block over a photo.', mood=('serious',), look='wire'),
    Part('magazine', 'layout', 'Magazine', 'Headline on top, photo across the bottom: explainers and analysis.', mood=('calm', 'explainer'), look='editorial'),
    Part('pop_collage', 'layout', 'Pop collage', 'One clear person in a bold collage: entertainment and trends.', 'Tragedy, conflict, politics.',
         mood=('energetic',), needs_photo=True, loud=True, look='pop_art'),
    # Cameras: how the photo moves.
    Part('zoom_in', 'camera', 'Push in', 'Building tension or focus on one subject.', mood=('urgent', 'serious')),
    Part('zoom_out', 'camera', 'Pull out', 'Revealing context; the end of a story.', mood=('calm', 'explainer')),
    Part('still', 'camera', 'Still', 'Photos with details that motion would crop.'),
    # Transitions: how a scene replaces the one before.
    Part('crossfade', 'transition', 'Crossfade', 'The default between scenes.', mood=('calm', 'serious')),
    Part('cut', 'transition', 'Cut', 'Fast, urgent pacing; a sharp change of subject.', 'Calm explainers.', mood=('urgent', 'energetic')),
    Part('slide', 'transition', 'Slide', 'Moving to the next fact in a sequence.', mood=('energetic', 'explainer')),
    # Text animations: how a headline, detail or figure arrives.
    Part('fade', 'animation', 'Fade', 'Calm, serious or sad stories; works everywhere.', mood=('calm', 'serious')),
    Part('rise', 'animation', 'Rise', 'The default for headlines and detail lines.', mood=('calm', 'explainer')),
    Part('left', 'animation', 'Slide in', 'A new development, a list of facts.', mood=('energetic', 'explainer')),
    Part('pop', 'animation', 'Pop', 'A big figure or a short, punchy hook.', 'Tragedy, deaths or injuries.', mood=('energetic',), loud=True),
    Part('wipe', 'animation', 'Wipe', 'Revealing a headline for emphasis; quotes.', mood=('serious', 'explainer')),
    Part('none', 'animation', 'No animation', 'Text that must be readable from the first frame.', 'Use rarely.'),
    # Text styles: the typeface of a role.
    Part('bold', 'text_style', 'Bold sans', 'Clear, modern headlines.'),
    Part('regular', 'text_style', 'Regular sans', 'Detail lines and captions.'),
    Part('display', 'text_style', 'Display', 'Loud, urgent headlines.', mood=('urgent', 'energetic')),
    Part('condensed', 'text_style', 'Condensed', 'Long headlines in a narrow space; politics.', mood=('serious',)),
    Part('condensed-semi', 'text_style', 'Condensed semibold', 'Labels and tags.'),
    Part('serif', 'text_style', 'Serif', 'Analysis and quotes.', mood=('calm', 'serious')),
    Part('serif-italic', 'text_style', 'Serif italic', 'Supporting lines under a serif headline.', mood=('calm',)),
)
BY_ID = {(p.kind, p.id): p for p in PARTS}
LITERALS = {'layout': LayoutId, 'camera': CameraId, 'transition': TransitionId, 'animation': AnimationId, 'text_style': TextStyleId}


def parts(kind, scene_kind=None, has_photo=True):
    """The parts of one kind a scene can use."""
    return [p for p in PARTS if p.kind == kind and (scene_kind is None or scene_kind in p.applies_to) and (has_photo or not p.needs_photo)]


def part(kind, id):
    return BY_ID[(kind, id)]


def layout_look(layout):
    return part('layout', layout).look


@dataclass(frozen=True)
class Recipe:
    """A starting combination. The AI or the operator may change any part of any scene afterwards."""
    id: str
    label: str
    use_when: str
    layouts: dict                        # scene kind -> layout
    cameras: tuple = ('zoom_in', 'zoom_out')  # used in turn, scene by scene
    transition: str = 'crossfade'
    pacing: str = 'normal'
    urgent: bool = False                 # only when the operator asks for it
    look: str = 'clean'                  # the look whose tag, accent and music it starts from
    animations: dict = field(default_factory=dict)  # role -> animation

    def layout_for(self, kind):
        return self.layouts.get(kind) or self.layouts['story']

    def public(self):
        return asdict(self)


def _same(layout):
    return {'story': layout}


RECIPES = (
    Recipe('breaking', 'Breaking', 'Urgent, developing news, only when the operator asks for it.', _same('news_strap'),
           transition='cut', pacing='fast', urgent=True, look='breaking'),
    Recipe('hot', 'Hot', 'Trending or viral stories, only when the operator asks for it.', _same('big_headline'),
           transition='slide', pacing='fast', urgent=True, look='hot'),
    Recipe('politics', 'Politics', 'Political news and analysis.', _same('photo_panel'), look='politics'),
    Recipe('clean', 'Clean', 'Neutral updates; the safe default.', _same('light_card'), look='clean'),
    Recipe('wire', 'Wire', 'A straight factual report.', _same('wire_report'), look='wire'),
    Recipe('editorial', 'Editorial', 'Explainers and analysis.', _same('magazine'), pacing='calm', look='editorial'),
    Recipe('pop_art', 'Pop Art', 'Entertainment or trending news about one clear person, only when the operator asks for it.',
           _same('pop_collage'), urgent=True, look='pop_art'),
)
RECIPE_BY_ID = {r.id: r for r in RECIPES}
LOOK_LAYOUT = {r.look: r.layouts['story'] for r in RECIPES}  # each current look and the layout that draws it


def catalog():
    """Everything the studio and the AI can choose from."""
    return dict(parts=[p.public() for p in PARTS], recipes=[r.public() for r in RECIPES])


def literal_values(kind):
    return set(get_args(LITERALS[kind]))
