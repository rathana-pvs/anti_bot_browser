"""The independent parts an AI Reel Studio scene is built from, and the recipes that combine them.

Every part is listed once here with when to use it. The composition schema, the composition renderer, the quality
checker and the AI composer all read these lists, so adding a part means: draw it, test it, list it here.
"""
from dataclasses import asdict, dataclass, field
from typing import Literal, get_args

LayoutId = Literal['news_strap', 'big_headline', 'photo_panel', 'light_card', 'wire_report', 'magazine', 'pop_collage',
                   'big_number', 'quote_card', 'centered_card', 'split', 'open_photo', 'paper', 'ruled_photo']
CameraId = Literal['zoom_in', 'zoom_out', 'still', 'pan_left', 'pan_right', 'pan_up', 'push_face']
TransitionId = Literal['crossfade', 'cut', 'slide', 'wipe', 'zoom_through']
AnimationId = Literal['fade', 'rise', 'left', 'pop', 'wipe', 'none', 'words', 'typewriter', 'count_up', 'punch_in']
EmphasisId = Literal['highlight_sweep', 'underline', 'word_pop', 'pulse', 'drift']
ExitId = Literal['fade', 'fade_up', 'slide_out', 'wipe_out']
TextStyleId = Literal['bold', 'regular', 'display', 'condensed', 'condensed-semi', 'serif', 'serif-italic']
Pacing = Literal['calm', 'normal', 'fast']
KINDS = frozenset({'story', 'stat', 'quote', 'end'})


@dataclass(frozen=True)
class Part:
    id: str
    kind: str                          # layout | camera | transition | animation | emphasis | exit | text_style
    label: str
    use_when: str
    avoid_when: str = ''
    applies_to: frozenset = KINDS      # scene kinds it suits
    mood: tuple = ()
    needs_photo: bool = False
    loud: bool = False                 # draws attention; the quality checker allows one per scene
    look: str = ''                     # layouts: the renderer layout that draws it
    text_moves: bool = False           # layouts: the text block floats over the photo, so a scene can move it up or down

    def public(self):
        value = asdict(self)
        value.update(applies_to=sorted(self.applies_to), mood=list(self.mood))
        return value


PARTS = (
    # Layouts: where the photo and the text sit in the frame.
    Part('news_strap', 'layout', 'News strap', 'Breaking or developing news over a full photo, with a ticker.', 'Calm explainers.',
         mood=('urgent',), look='breaking', text_moves=True),
    Part('big_headline', 'layout', 'Big headline', 'A short, strong headline over a photo; trending stories.', 'Long headlines.',
         mood=('energetic',), look='hot', text_moves=True),
    Part('photo_panel', 'layout', 'Photo and panel', 'Photo on top, text on a panel below: politics and analysis.', mood=('serious',), look='politics'),
    Part('light_card', 'layout', 'Light card', 'A photo card on a light page, like a news site: neutral updates.', mood=('calm', 'explainer'), look='clean'),
    Part('wire_report', 'layout', 'Wire report', 'A straight factual block over a photo.', mood=('serious',), look='wire', text_moves=True),
    Part('magazine', 'layout', 'Magazine', 'Headline on top, photo across the bottom: explainers and analysis.', mood=('calm', 'explainer'), look='editorial'),
    Part('pop_collage', 'layout', 'Pop collage', 'One clear person in a bold collage: entertainment and trends.', 'Tragedy, conflict, politics.',
         mood=('energetic',), needs_photo=True, loud=True, look='pop_art'),
    Part('big_number', 'layout', 'Big number', 'One figure the story turns on, as large as the frame allows.', 'Scenes without a figure.',
         applies_to=frozenset({'stat'}), mood=('energetic', 'explainer'), look='big_number'),
    Part('quote_card', 'layout', 'Quote card', 'Someone\'s exact words, large, with the speaker underneath.', 'Reported speech.',
         applies_to=frozenset({'quote'}), mood=('serious', 'calm'), look='quote_card'),
    Part('open_photo', 'layout', 'Open photo', 'A strong photo with the headline low on it, no card: most stories.',
         'Busy photos where text would not read (use centred card).', mood=('calm', 'serious', 'explainer', 'energetic'), look='open_photo', text_moves=True),
    Part('paper', 'layout', 'Paper page', 'Serious or analytical stories: the photo above, a serif headline printed on a paper page.',
         'Urgent or playful stories.', mood=('serious', 'calm', 'explainer'), look='paper'),
    Part('ruled_photo', 'layout', 'Photo with accent bar', 'A straight report: the text low on the photo, a bright bar beside it.',
         mood=('serious', 'explainer', 'urgent'), look='ruled_photo', text_moves=True),
    Part('centered_card', 'layout', 'Centred card', 'A key fact on a card over a strong photo; also good for end cards.',
         mood=('calm', 'explainer'), look='centered_card'),
    Part('split', 'layout', 'Split', 'Photo on top, a bold headline on an accent panel: a punchy hook or a new development.',
         'Long headlines.', mood=('energetic',), look='split'),
    # Cameras: how the photo moves.
    Part('zoom_in', 'camera', 'Push in', 'Building tension or focus on one subject.', mood=('urgent', 'serious')),
    Part('zoom_out', 'camera', 'Pull out', 'Revealing context; the end of a story.', mood=('calm', 'explainer')),
    Part('still', 'camera', 'Still', 'Photos with details that motion would crop.'),
    Part('pan_left', 'camera', 'Pan left', 'Wide photos: a crowd, a skyline, a scene with things to see.', mood=('explainer', 'calm')),
    Part('pan_right', 'camera', 'Pan right', 'Wide photos, the other way: vary it with pan left.', mood=('explainer', 'calm')),
    Part('pan_up', 'camera', 'Tilt up', 'Tall subjects: buildings, a person standing, a reveal.', mood=('serious',)),
    Part('push_face', 'camera', 'Push to face', 'A strong push toward the person in the photo, for a quote or the hook.', 'Photos without a face.',
         mood=('urgent', 'serious'), loud=True),
    # Transitions: how a scene replaces the one before.
    Part('crossfade', 'transition', 'Crossfade', 'The default between scenes.', mood=('calm', 'serious')),
    Part('cut', 'transition', 'Cut', 'Fast, urgent pacing; a sharp change of subject.', 'Calm explainers.', mood=('urgent', 'energetic')),
    Part('slide', 'transition', 'Slide', 'Moving to the next fact in a sequence.', mood=('energetic', 'explainer')),
    Part('wipe', 'transition', 'Wipe', 'A clean change of topic, edged in the accent colour.', mood=('explainer', 'serious')),
    Part('zoom_through', 'transition', 'Zoom through', 'High energy: into the next big moment.', 'Calm or sad stories.', mood=('energetic', 'urgent'), loud=True),
    # Text animations: how a headline, detail or figure arrives.
    Part('fade', 'animation', 'Fade', 'Calm, serious or sad stories; works everywhere.', mood=('calm', 'serious')),
    Part('rise', 'animation', 'Rise', 'The default for headlines and detail lines.', mood=('calm', 'explainer')),
    Part('left', 'animation', 'Slide in', 'A new development, a list of facts.', mood=('energetic', 'explainer')),
    Part('pop', 'animation', 'Pop', 'A big figure or a short, punchy hook.', 'Tragedy, deaths or injuries.', mood=('energetic',), loud=True),
    Part('wipe', 'animation', 'Wipe', 'Revealing a headline for emphasis; quotes.', mood=('serious', 'explainer')),
    Part('none', 'animation', 'No animation', 'Text that must be readable from the first frame.', 'Use rarely.'),
    Part('words', 'animation', 'Word by word', 'Headlines that build as they are read; the hook.', mood=('energetic', 'explainer')),
    Part('typewriter', 'animation', 'Typewriter', 'Quotes and statements, letter by letter.', 'Long text.', applies_to=frozenset({'quote', 'story', 'end'}),
         mood=('serious',)),
    Part('count_up', 'animation', 'Count up', 'A figure that climbs from zero to its value. For the big figure only.', 'Figures in words, scores.',
         applies_to=frozenset({'stat'}), mood=('energetic', 'explainer')),
    Part('punch_in', 'animation', 'Punch in', 'A short, strong hook that lands hard.', 'Sad news; more than one per reel.',
         mood=('energetic', 'urgent'), loud=True),
    # Emphasis: what the text does once it is in, while the photo moves. One per scene.
    Part('highlight_sweep', 'emphasis', 'Highlight sweep', 'A marker sweeps behind the key words, a second after the headline lands.',
         'Headlines with no highlighted words (becomes an underline).', mood=('explainer', 'energetic', 'serious')),
    Part('underline', 'emphasis', 'Underline', 'A line draws itself under the headline: steady, for any story.', mood=('calm', 'serious', 'explainer')),
    Part('word_pop', 'emphasis', 'Word pop', 'The highlighted words swell once for a beat: the hook, a surprising fact.',
         'Sad news; headlines with no highlighted words.', mood=('energetic',), loud=True),
    Part('pulse', 'emphasis', 'Pulse', 'The big figure beats twice so it sticks.', 'Deaths or injuries.', applies_to=frozenset({'stat'}),
         mood=('energetic', 'explainer')),
    Part('drift', 'emphasis', 'Drift', 'The text floats slowly against the camera, for depth. Quiet; suits any scene.', mood=('calm', 'serious')),
    # Exits: how the text leaves just before the next scene.
    Part('fade', 'exit', 'Fade', 'The default: the text fades with the scene.'),
    Part('fade_up', 'exit', 'Float up', 'The text lifts away: a soft handover to the next point.', mood=('calm', 'explainer')),
    Part('slide_out', 'exit', 'Slide out', 'The text slides off to the left: a brisk move to the next fact.', 'Calm or sad stories.',
         mood=('energetic', 'urgent')),
    Part('wipe_out', 'exit', 'Wipe out', 'The text is cleared away left to right, like a page turned.', mood=('serious', 'explainer')),
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
LITERALS = {'layout': LayoutId, 'camera': CameraId, 'transition': TransitionId, 'animation': AnimationId, 'emphasis': EmphasisId,
            'exit': ExitId, 'text_style': TextStyleId}


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
    studio: str = 'reel'                 # reel: one of Reel Studio's looks; ai: AI Studio's own
    accent: str | None = None            # its own accent colour, tag and music instead of the look's
    tag: str | None = None
    music: str | None = None
    styles: dict = field(default_factory=dict)      # role -> PartStyle fields: its fonts, text colours and line boxes
    emphasis: dict = field(default_factory=dict)    # scene kind -> what the text does once it is in
    exit: str | None = None
    mood: tuple = ()

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
# AI Studio's own recipes: built from the newer parts, each with its own type, colour, motion and music.
CARDS = {'stat': 'big_number', 'quote': 'quote_card', 'end': 'centered_card'}
RECIPES += (
    Recipe('spotlight', 'Spotlight', 'The safe default: any story, told clearly over strong photos.', {'story': 'centered_card', **CARDS},
           cameras=('zoom_in', 'pan_right', 'zoom_out'), studio='ai', accent='#38bdf8', tag='NEWS', music='headlines',
           emphasis={'story': 'highlight_sweep', 'stat': 'pulse', 'quote': 'underline'}, exit='fade_up', mood=('calm', 'explainer'),
           animations={'headline': 'rise'}),
    Recipe('boxed', 'Boxed', 'Social-first updates: headline lines on white boxes over the photo, brisk and modern.',
           {'story': 'open_photo', **CARDS}, cameras=('zoom_in', 'pan_left'), transition='slide', pacing='fast', studio='ai',
           accent='#e11d48', tag='UPDATE', music='live_update', exit='slide_out', mood=('energetic',),
           styles={'headline': dict(text_style='condensed', color='#0b0b0f', background='#ffffff')},
           emphasis={'story': 'word_pop', 'stat': 'pulse', 'quote': 'underline'}, animations={'headline': 'left'}),
    Recipe('night_desk', 'Night desk', 'Serious news and analysis: serif type and gold on dark, slow and steady.',
           {'story': 'open_photo', **CARDS}, cameras=('pan_left', 'zoom_out', 'still'), transition='wipe', pacing='calm', studio='ai',
           accent='#e0b354', tag='ANALYSIS', music='washington_watch', exit='wipe_out', mood=('serious',),
           styles={'headline': dict(text_style='serif'), 'detail': dict(text_style='serif-italic')},
           emphasis={'story': 'underline', 'stat': 'underline', 'quote': 'drift'}, animations={'headline': 'wipe'}),
    Recipe('explainer', 'Explainer', 'Numbers and how-it-works stories: photo above, a teal panel for the point.',
           {'story': 'split', **CARDS}, cameras=('zoom_in', 'zoom_out'), transition='slide', studio='ai', accent='#14b8a6',
           tag='EXPLAINED', music='headlines', exit='slide_out', mood=('explainer',),
           emphasis={'story': 'highlight_sweep', 'stat': 'pulse', 'quote': 'underline'}, animations={'headline': 'words', 'lead': 'count_up'}),
    Recipe('pulse', 'Pulse', 'Urgent, live developments, only when the operator asks for it: red boxes, hard cuts.',
           {'story': 'open_photo', **CARDS}, cameras=('zoom_in', 'push_face'), transition='cut', pacing='fast', urgent=True, studio='ai',
           accent='#ffd60a', tag='LIVE', music='urgent_wire', exit='slide_out', mood=('urgent',),
           styles={'headline': dict(text_style='display', color='#ffffff', background='#e5172f')},
           emphasis={'story': 'word_pop', 'stat': 'pulse', 'quote': 'underline'}, animations={'headline': 'punch_in'}),
    # From how leading news outlets post: their patterns, not their identity (no names, logos, fonts or colours of theirs).
    Recipe('broadsheet', 'Broadsheet', 'Analysis and big stories told like a quality paper: serif print on a paper page.',
           {'story': 'paper', 'stat': 'paper', 'quote': 'paper', 'end': 'paper'}, cameras=('zoom_in', 'still'), pacing='calm', studio='ai', accent='#c8283c', tag='ANALYSIS',
           music='major_headline', exit='fade_up', mood=('serious', 'explainer'),
           emphasis={'story': 'underline', 'stat': 'pulse', 'quote': 'underline'}, animations={'headline': 'rise'}),
    Recipe('captioned', 'Captioned', 'Sound-off social video: white text on dark boxes, line by line, readable on any photo.',
           {'story': 'open_photo', **CARDS}, cameras=('zoom_in', 'pan_right', 'zoom_out'), studio='ai', accent='#ff5a4e', tag='NEWS',
           music='headlines', exit='fade', mood=('calm', 'serious', 'explainer'),
           styles={'headline': dict(text_style='bold', color='#ffffff', background='#101114')},
           emphasis={'story': 'highlight_sweep', 'stat': 'pulse', 'quote': 'drift'}, animations={'headline': 'words'}),
    Recipe('dispatch', 'Dispatch', 'Straight factual reports: the fact low on the photo with a bright accent bar, crisp and quick.',
           {'story': 'ruled_photo', **CARDS}, cameras=('pan_left', 'zoom_in'), transition='wipe', studio='ai', accent='#ff8a1f',
           tag='REPORT', music='alert_desk', exit='wipe_out', mood=('serious', 'urgent', 'explainer'),
           styles={'headline': dict(text_style='condensed'), 'detail': dict(text_style='regular')},
           emphasis={'story': 'highlight_sweep', 'stat': 'pulse', 'quote': 'underline'}, animations={'headline': 'left'}),
    Recipe('gentle', 'Gentle', 'Human, sad or sensitive stories: quiet serif cards, still photos, no flourish.',
           {'story': 'centered_card', **CARDS}, cameras=('still', 'zoom_out'), pacing='calm', studio='ai', accent='#a5b4cb',
           tag='STORY', music='developing_story', exit='fade', mood=('calm', 'serious'),
           styles={'headline': dict(text_style='serif'), 'detail': dict(text_style='regular')},
           emphasis={'story': 'drift', 'stat': 'drift', 'quote': 'drift'}, animations={'headline': 'fade', 'detail': 'fade'}),
)
RECIPE_BY_ID = {r.id: r for r in RECIPES}
AI_RECIPES = tuple(r for r in RECIPES if r.studio == 'ai')
DEFAULT_RECIPE = 'spotlight'
LOOK_LAYOUT = {r.look: r.layouts['story'] for r in RECIPES if r.studio == 'reel'}  # each Reel Studio look and the layout that draws it


def catalog():
    """Everything the studio and the AI can choose from."""
    return dict(parts=[p.public() for p in PARTS], recipes=[r.public() for r in RECIPES])


def literal_values(kind):
    return set(get_args(LITERALS[kind]))
