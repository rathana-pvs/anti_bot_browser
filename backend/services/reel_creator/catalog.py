"""Everything the renderer can do that the AI director may choose, with when to use it.

One entry per preset. The schema (reel.py), the director's prompt and choices, and the Studio read this list, so a new
renderer preset needs its code, a test and an entry here; a parity test fails when the two disagree.
"""
from dataclasses import asdict, dataclass, field
from typing import get_args

from .reel import LOOKS, Anim, Look, SceneKind, Transition

ALL_KINDS = frozenset(get_args(SceneKind))


@dataclass(frozen=True)
class Preset:
    id: str
    group: str                          # look | scene_kind | entrance | motion | transition | fit
    label: str
    use_when: str
    avoid_when: str = ''
    applies_to: frozenset = ALL_KINDS   # scene kinds it can be used on
    looks: frozenset | None = None      # looks it works with; None = every look
    mood: tuple = field(default_factory=tuple)

    def public(self):
        value = asdict(self)
        value.update(applies_to=sorted(self.applies_to), looks=sorted(self.looks) if self.looks else None, mood=list(self.mood))
        return value


CATALOG = (
    # Looks: the whole reel's design. Urgency looks are the operator's call (writer.py keeps the same rule).
    Preset('breaking', 'look', LOOKS['breaking']['name'], 'Urgent, developing news, and only when the operator asks for it.',
           'Anything not urgent or still unconfirmed.', mood=('urgent',)),
    Preset('hot', 'look', LOOKS['hot']['name'], 'Trending or viral stories, and only when the operator asks for it.', 'Serious or sad news.', mood=('energetic',)),
    Preset('politics', 'look', LOOKS['politics']['name'], 'Political news and analysis.', mood=('serious',)),
    Preset('clean', 'look', LOOKS['clean']['name'], 'Neutral updates; the safe default.', mood=('calm', 'explainer')),
    Preset('wire', 'look', LOOKS['wire']['name'], 'A straight factual report.', mood=('serious',)),
    Preset('editorial', 'look', LOOKS['editorial']['name'], 'Explainers and analysis.', mood=('calm', 'explainer')),
    Preset('pop_art', 'look', LOOKS['pop_art']['name'], 'Trending or entertainment news about one clear person.', 'Tragedy, conflict or politics.', mood=('energetic',)),
    # Scene kinds: what one scene is.
    Preset('story', 'scene_kind', 'Story', 'One key fact as a headline and a supporting line. Scene 1 is always a story (the hook).'),
    Preset('stat', 'scene_kind', 'Figure', 'The point is one specific number the article gives.', 'There is no figure in the article.'),
    Preset('quote', 'scene_kind', 'Quote', 'The article quotes someone directly, inside quotation marks.', 'Reported speech; nobody is quoted.'),
    Preset('end', 'scene_kind', 'End card', 'The last scene: the open question, or where to read the full story.', 'Anywhere but the end.'),
    # Entrances: how the headline, detail or big figure arrives. Timing stays the look's own.
    Preset('fade', 'entrance', 'Fade', 'Calm, serious or sad stories; works everywhere.', mood=('calm', 'serious')),
    Preset('rise', 'entrance', 'Rise', 'The default for headlines and detail lines.', mood=('calm', 'explainer')),
    Preset('left', 'entrance', 'Slide in', 'Moving a story along, a new development, a list of facts.', mood=('energetic', 'explainer')),
    Preset('pop', 'entrance', 'Pop', 'A big figure or a short, punchy hook.', 'Tragedy, deaths or injuries.', mood=('energetic',)),
    Preset('wipe', 'entrance', 'Wipe', 'Revealing a headline word by word for emphasis; quotes.', mood=('serious', 'explainer')),
    Preset('none', 'entrance', 'No animation', 'Text that must be readable from the first frame.', 'Use rarely.'),
    # Camera motion on the scene's photo.
    Preset('zoom_in', 'motion', 'Push in', 'Building tension or focus on one subject.', mood=('urgent', 'serious')),
    Preset('zoom_out', 'motion', 'Pull out', 'Revealing context; the end of a story.', mood=('calm', 'explainer')),
    Preset('still', 'motion', 'Still', 'Photos with text or details that must not be cropped by motion.'),
    # How a scene replaces the one before it.
    Preset('fade_in', 'transition', 'Crossfade', 'The default between scenes.', mood=('calm', 'serious')),
    Preset('cut', 'transition', 'Cut', 'Fast, urgent pacing; a sharp change of subject.', 'Calm explainers.', mood=('urgent', 'energetic')),
    Preset('slide', 'transition', 'Slide', 'Moving to the next fact in a sequence.', mood=('energetic', 'explainer')),
    # How the photo fills the frame.
    Preset('cover', 'fit', 'Fill the frame', 'Most photos; the crop follows faces.'),
    Preset('blur', 'fit', 'Whole photo', 'Photos that must not be cropped: documents, wide groups, charts.'),
)
# Ids that differ from the schema's value, so every catalog id stays unique across groups.
SCHEMA_VALUE = {'still': 'none', 'fade_in': 'fade'}
BY_ID = {p.id: p for p in CATALOG}
assert len(BY_ID) == len(CATALOG), 'catalog ids must be unique'


def group(name):
    return [p for p in CATALOG if p.group == name]


def value(preset):
    """The value the reel schema stores for this preset."""
    return SCHEMA_VALUE.get(preset.id, preset.id)


def schema_values(name):
    return {value(p) for p in group(name)}


EXPECTED = {'look': set(get_args(Look)), 'scene_kind': set(get_args(SceneKind)), 'entrance': set(get_args(Anim)),
            'motion': {'zoom_in', 'zoom_out', 'none'}, 'transition': set(get_args(Transition)), 'fit': {'cover', 'blur'}}


def catalog_for(look=None, kinds=None):
    """The presets usable on a reel in `look` with scenes of `kinds`: what the director is offered, and nothing more."""
    kinds = set(kinds or ALL_KINDS)
    return [p for p in CATALOG if (p.looks is None or look is None or look in p.looks) and p.applies_to & kinds]


def describe(presets):
    """The catalog as short prompt lines, grouped: `value` (Label): when to use; avoid when."""
    lines, current = [], None
    for p in presets:
        if p.group != current:
            current = p.group
            lines.append(f'{current}:')
        avoid = f' Avoid: {p.avoid_when}' if p.avoid_when else ''
        lines.append(f'- {value(p)} ({p.label}): {p.use_when}{avoid}')
    return '\n'.join(lines)
