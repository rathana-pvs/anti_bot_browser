"""AI composer: an article and photos become a composition whose parts are chosen for the story and each scene.

1. The writer (writer.py) writes the scenes' words from the article, with all its grounding checks.
2. One structured call picks the recipe, pacing and every scene's layout, camera, transition and entrances, from only
   the parts that suit that scene (components.py). Urgent recipes need the operator to ask for them.
3. The quality rules (quality.py) fix or refuse anything unreadable.
Chat edits to a composition reuse the director's checked operations, with composition parts.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.services.ai import AIConfig, generate_json

from . import components
from .components import AnimationId, CameraId, LayoutId, Pacing, RECIPE_BY_ID, TransitionId
from .composition import Background, Composition, CScene, PartStyle
from .director import Editor, Rejected, Turn, article_prefix, direct, plain_schema
from .quality import review
from .reel import MAX_SCENES
from .writer import GROUNDING, WriteRequest, write_reel

ROLES = ('headline', 'detail', 'lead')


def describe_parts(kinds=None):
    """The parts as prompt lines, by kind, each with the scene kinds it suits and whether it is loud."""
    lines, current = [], None
    for p in components.PARTS:
        if p.kind == 'text_style':
            continue
        if kinds and not p.applies_to & set(kinds):
            continue
        if p.kind != current:
            current = p.kind
            lines.append(f'{current}s:')
        suits = '' if p.applies_to == components.KINDS else f' [only for {", ".join(sorted(p.applies_to))} scenes]'
        extra = (' Avoid: ' + p.avoid_when if p.avoid_when else '') + (' (loud)' if p.loud else '') + (' (needs a photo)' if p.needs_photo else '')
        lines.append(f'- {p.id} ({p.label}): {p.use_when}{suits}{extra}')
    lines.append('recipes:')
    for r in components.RECIPES:
        lines.append(f'- {r.id} ({r.label}): {r.use_when}')
    return '\n'.join(lines)


COMPOSE_SYSTEM = f'''You are the art director of a short Facebook news reel. The scenes' words are already written and
checked; you choose how each scene looks and moves, using only the parts listed below, by their id.
{GROUNDING}
You get the scenario the operator typed (may be empty), the story's opening, and each scene: its kind, its words,
whether it has a photo and whether that photo shows a face. Return:
- mood: calm, serious, urgent, energetic or explainer; pacing: calm, normal or fast.
- recipe: the closest recipe. breaking, hot and pop_art only when the operator's scenario asks for them.
- scenes: one entry per scene, in order, with layout, camera, transition (how it replaces the scene before; scene 1's
  is ignored), and entrances for headline, detail and lead (the big figure or quote mark). Leave a field out to keep
  the recipe's choice.
Rules: a part marked "only for" suits only those scene kinds. At most one loud part per scene. Do not use the same
headline entrance on more than two scenes in a row. Stat scenes suit big_number with count_up on the lead; quote
scenes suit quote_card with typewriter or wipe on the headline. Faces: prefer push_face or zoom_in, and layouts that
keep text off the face. Sad, tragic or violent stories: calm parts only (fade, rise, wipe, crossfade, zoom_out, still).
Vary layouts across scenes when it helps the story, but keep the reel coherent: two or three layouts at most.

Parts:
'''


class ScenePick(BaseModel):
    model_config = ConfigDict(extra='forbid')
    layout: LayoutId | None = None
    camera: CameraId | None = None
    transition: TransitionId | None = None
    headline: AnimationId | None = None
    detail: AnimationId | None = None
    lead: AnimationId | None = None


class Picks(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mood: Literal['calm', 'serious', 'urgent', 'energetic', 'explainer'] = 'calm'
    pacing: Pacing = 'normal'
    recipe: str = 'clean'
    scenes: list[ScenePick] = Field(default_factory=list, max_length=MAX_SCENES)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return plain_schema(super().model_json_schema(*args, **kwargs))


class ComposeRequest(WriteRequest):
    """An article to compose from, the photos to use in order, and what the operator wants it to feel like."""
    photos: list[str] = Field(default_factory=list, max_length=MAX_SCENES)
    scenario: str = Field(default='', max_length=300)
    recipe: str | None = None  # the operator's own choice wins over the AI's


def names(text, recipe):
    r = RECIPE_BY_ID[recipe]
    return any(re.search(rf'\b{re.escape(n)}\b', text.replace('_', ' '), re.I) for n in (recipe.replace('_', ' '), r.label))


def checked_recipe(asked, operator_recipe, scenario):
    """The operator's recipe, else the AI's when it exists and is allowed, else a calm default."""
    if operator_recipe in RECIPE_BY_ID:
        return operator_recipe
    if asked in RECIPE_BY_ID and (not RECIPE_BY_ID[asked].urgent or names(scenario, asked)):
        return asked
    return 'clean'


def scenes_from(written, photos, assets):
    """The writer's scenes and closing card as composition scenes, one photo each in order; the end card has none."""
    out = []
    for index, s in enumerate(written['scenes']):
        image_id = photos[index] if index < len(photos) else None
        focus = ((assets or {}).get(image_id) or {}).get('focus') or {}
        out.append(CScene(kind=s['kind'], headline=s['headline'], detail=s['detail'], stat=s['stat'], attribution=s['attribution'],
                          highlight=s['highlight'],
                          background=Background(image_id=image_id, focal_x=focus.get('x', .5), focal_y=focus.get('y', .4))))
    if written.get('end'):
        end = written['end']
        out.append(CScene(kind='end', headline=end['headline'], detail=end.get('detail', ''), highlight=end.get('highlight', '')))
    return out[:MAX_SCENES]


def apply_picks(composition, picks, recipe):
    """The recipe's starting parts, then each scene's picks where they suit the scene."""
    chosen = RECIPE_BY_ID[recipe]
    composition.recipe, composition.pacing = recipe, picks.pacing
    for index, scene in enumerate(composition.scenes):
        pick = picks.scenes[index] if index < len(picks.scenes) else ScenePick()
        scene.layout = chosen.layout_for(scene.kind)
        if pick.layout and scene.kind in components.part('layout', pick.layout).applies_to:
            scene.layout = pick.layout
        scene.camera = pick.camera or None
        scene.transition = (pick.transition or chosen.transition) if index else None
        styles = dict(scene.styles)
        for role in ROLES:
            animation = getattr(pick, role)
            if animation and scene.kind in components.part('animation', animation).applies_to:
                styles[role] = (styles.get(role) or PartStyle()).model_copy(update={'animation': animation})
        scene.styles = styles
    return composition


def scene_facts(composition, assets):
    facts = []
    for number, s in enumerate(composition.scenes, 1):
        asset = (assets or {}).get(s.image_id) or {}
        facts.append(dict(scene=number, kind=s.kind, headline=s.headline, detail=s.detail, stat=s.stat or None,
                          has_photo=bool(s.image_id), has_face=bool((asset.get('focus') or {}).get('faces'))))
    return facts


def compose(request: ComposeRequest, assets=None, images=None, writer=None, provider=None):
    """Write, choose the parts, check: returns the composition, its caption and comment, the hooks and what was fixed."""
    usage = dict(input=0, output=0)
    look = RECIPE_BY_ID[request.recipe].look if request.recipe in RECIPE_BY_ID else 'auto'
    written = (writer or write_reel)(request.model_copy(update=dict(look=look, caption=True,
                                                                    scenes=min(len(request.photos), MAX_SCENES - 1) or None)))
    composition = Composition(scenes=scenes_from(written, request.photos, assets), brand=request.publisher[:48],
                              source=written.get('source_label', '')[:90])
    context = dict(scenario=request.scenario, story_opening=request.text[:1200], goal=request.goal, scenes=scene_facts(composition, assets))
    calls = {}

    def ask():
        if provider:
            return provider(request, system=COMPOSE_SYSTEM + describe_parts(), context=context, usage=calls)
        return generate_json(request, system=COMPOSE_SYSTEM + describe_parts(), schema=Picks, context=context, action='compose',
                             effort='low', usage=calls)
    try:
        picks = Picks.model_validate(ask())
    except ValidationError:
        picks = Picks()  # the words are written; the recipe's own parts are a fine look
    usage.update(input=calls.get('input', 0), output=calls.get('output', 0))
    recipe = checked_recipe(picks.recipe, request.recipe, request.scenario)
    composition = apply_picks(composition, picks, recipe)
    fixed, fixes, problems = review(composition, assets, images)
    return dict(composition=fixed.model_dump(), caption=written['caption'], comment=written['comment'], hooks=written['hooks'],
                mood=picks.mood, recipe=recipe, fixes=[vars(f) for f in fixes], problems=[vars(p) for p in problems], usage=usage,
                model=request.model)


# ---- chat edits on a composition ------------------------------------------------

DIRECT_SYSTEM = f'''You are the director of a short Facebook news reel built from parts. The operator tells you what to
change; you answer with a short message and a list of operations. You never write code or invent effects: you choose
only from the parts listed below, by their id.
{GROUNDING}
Scenes are numbered from 1. Scene 1 is the hook and stays a story scene. An end scene can only be the last one. Never
change, move or remove a scene marked locked. Change only what the instruction asks for. Prefer few operations.
Operations (fields not listed for an operation must be left out):
- edit_text: scene, and any of headline, detail, kind, stat, attribution, highlight (as the writer's rules: the
  article's facts and figures only; highlight is 1-3 words copied from that scene's headline).
- set_layout: scene, layout. set_camera: scene, camera. set_transition: scene, transition.
- set_animation: scene, role (headline, detail or lead), animation.
- set_recipe: recipe (breaking, hot and pop_art only when the operator names them). set_pacing: pacing.
- add_scene: after (0 = before scene 1), kind (story, stat or quote), headline, detail, highlight.
- remove_scene: scene. move_scene: scene, to.
At most one loud part per scene; vary entrances. message is one or two plain sentences about what you changed.

Parts:
'''


class CompOp(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['edit_text', 'set_layout', 'set_camera', 'set_transition', 'set_animation', 'set_recipe', 'set_pacing',
                'add_scene', 'remove_scene', 'move_scene']
    scene: int | None = Field(default=None, ge=1, le=MAX_SCENES)
    to: int | None = Field(default=None, ge=1, le=MAX_SCENES)
    after: int | None = Field(default=None, ge=0, le=MAX_SCENES)
    headline: str | None = Field(default=None, max_length=120)
    detail: str | None = Field(default=None, max_length=160)
    kind: Literal['story', 'stat', 'quote', 'end'] | None = None
    stat: str | None = Field(default=None, max_length=24)
    attribution: str | None = Field(default=None, max_length=60)
    highlight: str | None = Field(default=None, max_length=40)
    layout: LayoutId | None = None
    camera: CameraId | None = None
    transition: TransitionId | None = None
    role: Literal['headline', 'detail', 'lead'] | None = None
    animation: AnimationId | None = None
    recipe: str | None = Field(default=None, max_length=24)
    pacing: Pacing | None = None


class CompPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(default='', max_length=400)
    operations: list[CompOp] = Field(default_factory=list, max_length=12)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return plain_schema(super().model_json_schema(*args, **kwargs))


class ComposeDirectRequest(AIConfig):
    reel: Composition  # named as the director's, so its checked editor works on it
    instruction: str = Field(min_length=2, max_length=500)
    source_text: str = Field(default='', max_length=120000)
    goal: Literal['engage', 'readmore'] = 'engage'
    history: list[Turn] = Field(default_factory=list, max_length=6)


class CompositionEditor(Editor):
    """The director's checked editor (text grounding, locks, scene order) with composition parts."""

    def suits(self, kind, id, scene):
        part = components.part(kind, id)
        if scene.kind not in part.applies_to:
            raise Rejected(f'{part.label} does not suit a {scene.kind} scene.')
        if part.needs_photo and not scene.image_id:
            raise Rejected(f'{part.label} needs a photo.')
        return part

    def set_layout(self, op):
        scene = self.scene(op.scene)
        if op.layout:
            self.suits('layout', op.layout, scene)
            self.note(op.scene, 'layout', scene.layout, op.layout)
            scene.layout = op.layout

    def set_camera(self, op):
        scene = self.scene(op.scene)
        if op.camera:
            self.suits('camera', op.camera, scene)
            self.note(op.scene, 'camera', scene.camera, op.camera)
            scene.camera = op.camera

    def set_transition(self, op):
        scene = self.scene(op.scene)
        if op.transition:
            self.note(op.scene, 'transition', scene.transition, op.transition)
            scene.transition = op.transition

    def set_animation(self, op):
        scene = self.scene(op.scene)
        if op.animation:
            self.suits('animation', op.animation, scene)
            role = op.role or 'headline'
            current = scene.styles.get(role) or PartStyle()
            self.note(op.scene, f'{role} entrance', current.animation, op.animation)
            scene.styles = {**scene.styles, role: current.model_copy(update={'animation': op.animation})}

    def set_recipe(self, op):
        if not op.recipe or op.recipe == self.reel.recipe:
            return
        if op.recipe not in RECIPE_BY_ID:
            raise Rejected('There is no such recipe.')
        recipe = RECIPE_BY_ID[op.recipe]
        if recipe.urgent and not names(self.request.instruction, op.recipe):
            raise Rejected(f'The {recipe.label} recipe is only used when you ask for it.')
        self.note(None, 'recipe', self.reel.recipe, op.recipe)
        self.reel.recipe = op.recipe
        for number, scene in enumerate(self.reel.scenes, 1):
            if not scene.locked:
                scene.layout = recipe.layout_for(scene.kind)
                scene.transition = recipe.transition if number > 1 else None

    def set_pacing(self, op):
        if op.pacing and op.pacing != self.reel.pacing:
            self.note(None, 'pacing', self.reel.pacing, op.pacing)
            self.reel.pacing = op.pacing

    def add_scene(self, op):
        if len(self.reel.scenes) >= MAX_SCENES:
            raise Rejected(f'A reel holds at most {MAX_SCENES} scenes.')
        after = len(self.reel.scenes) if op.after is None else min(op.after, len(self.reel.scenes))
        if self.reel.scenes[-1].kind == 'end':
            after = min(after, len(self.reel.scenes) - 1)
        kind = op.kind if op.kind in ('story', 'stat', 'quote') else 'story'
        recipe = RECIPE_BY_ID.get(self.reel.recipe or 'clean')
        scene = CScene(layout=recipe.layout_for(kind), kind=kind, headline=' '.join((op.headline or '').split()),
                       detail=' '.join((op.detail or '').split()), stat=op.stat or '', attribution=op.attribution or '',
                       highlight=op.highlight or '', transition=recipe.transition)
        if not scene.headline:
            raise Rejected('A new scene needs a headline.')
        self.reel.scenes.insert(after, scene)
        try:
            self.placed(scene, after + 1)
            self.grounded(scene, after + 1)
        except Rejected:
            del self.reel.scenes[after]
            raise
        self.note(after + 1, 'scene', None, f'added: {scene.headline}')


def composition_context(request):
    return dict(reel=dict(recipe=request.reel.recipe, pacing=request.reel.pacing, seconds=request.reel.duration,
                          scenes=[dict(scene=i + 1, kind=s.kind, headline=s.headline, detail=s.detail, layout=s.layout, camera=s.camera,
                                       transition=s.transition, has_photo=bool(s.image_id), locked=bool(s.locked),
                                       entrances={r: st.animation for r, st in s.styles.items() if st.animation} or None)
                                  for i, s in enumerate(request.reel.scenes)]),
                goal=request.goal, history=[t.model_dump() for t in request.history], instruction=request.instruction)


def direct_composition(request: ComposeDirectRequest, provider=None, assets=None, images=None):
    """A chat edit on a composition: checked operations, then the quality rules over the result."""
    def apply(req, plan):
        editor = CompositionEditor(req)
        refused = []
        for op in plan.operations:
            try:
                editor.apply(op)
            except Rejected as exc:
                refused.append(str(exc))
            except ValidationError:
                refused.append(f'Scene {op.scene} could not take that change.' if op.scene else 'The reel could not take that change.')
        try:
            edited = Composition.model_validate(editor.reel.model_dump())
        except ValidationError as exc:
            raise Rejected(' '.join(e['msg'].removeprefix('Value error, ') for e in exc.errors()) or 'The edited reel is not valid.') from None
        fixed, fixes, problems = review(edited, assets, images)
        changes = editor.changes + [dict(scene=f.scene, field='quality', before='', after=f.message) for f in fixes]
        return fixed, changes, refused + [p.message for p in problems]

    def ask(req, *, system, context, prefix, usage):
        if provider:
            return provider(req, system=system, context=context, prefix=prefix, usage=usage)
        return generate_json(req, system=system, schema=CompPlan, context=context, prefix=prefix, action='direct', effort='low', usage=usage)
    return direct(request, ask, schema=CompPlan, apply=apply, system=DIRECT_SYSTEM + describe_parts(), ctx=composition_context(request))


__all__ = ['ComposeRequest', 'ComposeDirectRequest', 'compose', 'direct_composition', 'article_prefix']
