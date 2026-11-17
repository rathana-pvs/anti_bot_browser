"""AI composer: an article and photos become a composition whose parts are chosen for the story and each scene.

1. The writer (writer.py) writes the scenes' words from the article, with all its grounding checks.
2. One structured call picks the recipe, pacing and every scene's layout, camera, transition and entrances, from only
   the parts that suit that scene (components.py). Urgent recipes need the operator to ask for them.
3. The quality rules (quality.py) fix or refuse anything unreadable.
Chat edits to a composition reuse the director's checked operations, with composition parts.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.services.ai import AIConfig, generate_json

from . import components
from .components import AnimationId, CameraId, EmphasisId, ExitId, LayoutId, Pacing, RECIPE_BY_ID, TransitionId
from .composition import Background, Composition, CScene, PartStyle
from .director import Editor, Rejected, Turn, article_prefix, direct, plain_schema
from .quality import review
from .reel import MAX_SCENES, highlight_span
from .news_ai import numbers
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
            lines.append({'emphasis': 'emphasis (what the text does once it is in):', 'exit': 'exits (how the text leaves):'}.get(current, f'{current}s:'))
        suits = '' if p.applies_to == components.KINDS else f' [only for {", ".join(sorted(p.applies_to))} scenes]'
        extra = (' Avoid: ' + p.avoid_when if p.avoid_when else '') + (' (loud)' if p.loud else '') + (' (needs a photo)' if p.needs_photo else '')
        lines.append(f'- {p.id} ({p.label}): {p.use_when}{suits}{extra}')
    lines.append('recipes:')
    for r in components.AI_RECIPES:
        lines.append(f'- {r.id} ({r.label}): {r.use_when}')
    lines.append('Reel Studio looks, only when the operator names one: ' + ', '.join(r.id for r in components.RECIPES if r.studio == 'reel'))
    return '\n'.join(lines)


COMPOSE_SYSTEM = f'''You are the art director of a short Facebook news reel. The scenes' words are already written and
checked; you choose how each scene looks and moves, using only the parts listed below, by their id.
{GROUNDING}
You get the scenario the operator typed (may be empty), the story's opening, and each scene: its kind, its words,
whether it has a photo and whether that photo shows a face. Return:
- mood: calm, serious, urgent, energetic or explainer; pacing: calm, normal or fast.
- recipe: the closest of the recipes listed. pulse only when the scenario asks for breaking, urgent or live coverage;
  a Reel Studio look only when the scenario names it.
- scenes: one entry per scene, in order, with layout, camera, transition (how it replaces the scene before; scene 1's
  is ignored), entrances for headline, detail and lead (the big figure or quote mark), an emphasis (what the text does
  once it is in, while the camera moves the photo) and an exit (how the text leaves before the next scene). Leave a
  field out to keep the recipe's choice.
Rules: a part marked "only for" suits only those scene kinds. The camera waits until the text is in, so each scene has
two moments: at most one loud part while the text arrives (transition, entrances) and one while the photo moves
(camera, emphasis). Give most scenes an emphasis and vary them: highlight_sweep or word_pop when the headline has a
highlight, pulse on a stat's figure, underline otherwise; the text also drifts slowly on its own, so drift alone is
the quietest choice. Use one or two exits across the reel, not a new one
every scene. Do not use the same
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
    emphasis: EmphasisId | None = None
    exit: ExitId | None = None

    @model_validator(mode='before')
    @classmethod
    def known_parts(cls, value):
        """An id the AI made up leaves that part to the recipe instead of losing every pick."""
        if not isinstance(value, dict):
            return value
        kinds = dict(layout='layout', camera='camera', transition='transition', headline='animation', detail='animation',
                     lead='animation', emphasis='emphasis', exit='exit')
        return {k: (v if k not in kinds or v is None or (kinds[k], v) in components.BY_ID else None) for k, v in value.items()}


class Picks(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mood: Literal['calm', 'serious', 'urgent', 'energetic', 'explainer'] = 'calm'
    pacing: Pacing = 'normal'
    recipe: str = 'clean'
    scenes: list[ScenePick] = Field(default_factory=list, max_length=MAX_SCENES)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return light_schema(plain_schema(super().model_json_schema(*args, **kwargs)))


class ComposeRequest(WriteRequest):
    """An article to compose from, the photos to use in order, and what the operator wants it to feel like."""
    photos: list[str] = Field(default_factory=list, max_length=MAX_SCENES)
    scenario: str = Field(default='', max_length=300)
    recipe: str | None = None  # the operator's own choice wins over the AI's


def names(text, recipe):
    r = RECIPE_BY_ID[recipe]
    return any(re.search(rf'\b{re.escape(n)}\b', text.replace('_', ' '), re.I) for n in (recipe.replace('_', ' '), r.label))


URGENT = re.compile(r'\b(breaking|urgent|live|developing|just in)\b', re.I)


def allowed(recipe, text):
    """An AI Studio recipe the AI may choose; urgent ones and Reel Studio's looks only when the operator's words ask."""
    r = RECIPE_BY_ID[recipe]
    if r.studio == 'reel':
        return names(text, recipe)
    return not r.urgent or names(text, recipe) or bool(URGENT.search(text))


def checked_recipe(asked, operator_recipe, scenario):
    """The operator's recipe, else the AI's when it exists and is allowed, else AI Studio's calm default."""
    if operator_recipe in RECIPE_BY_ID:
        return operator_recipe
    if asked in RECIPE_BY_ID and allowed(asked, scenario):
        return asked
    return components.DEFAULT_RECIPE


def apply_recipe(composition, recipe_id):
    """The recipe's own parts on every unlocked scene: layouts, transitions, cameras in turn, emphasis and exits. Its
    fonts, colours, tag and music apply when the reel is drawn, under anything set by hand."""
    recipe = RECIPE_BY_ID[recipe_id]
    composition.recipe, composition.pacing = recipe_id, recipe.pacing
    for index, scene in enumerate(composition.scenes):
        if scene.locked:
            continue
        scene.layout, scene.camera = recipe.layout_for(scene.kind), None
        scene.transition = recipe.transition if index else None
        if recipe.studio == 'ai':
            scene.emphasis = None if scene.kind == 'end' else recipe.emphasis.get(scene.kind)
            scene.exit = recipe.exit
    return composition


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
    apply_recipe(composition, recipe)
    composition.pacing = picks.pacing if 'pacing' in picks.model_fields_set else composition.pacing
    for index, scene in enumerate(composition.scenes):
        pick = picks.scenes[index] if index < len(picks.scenes) else ScenePick()
        if pick.layout and scene.kind in components.part('layout', pick.layout).applies_to:
            scene.layout = pick.layout
        scene.camera = pick.camera or None
        if index and pick.transition:
            scene.transition = pick.transition
        styles = dict(scene.styles)
        for role in ROLES:
            animation = getattr(pick, role)
            if animation and scene.kind in components.part('animation', animation).applies_to:
                styles[role] = (styles.get(role) or PartStyle()).model_copy(update={'animation': animation})
        scene.styles = styles
        if pick.emphasis and scene.kind in components.part('emphasis', pick.emphasis).applies_to:
            scene.emphasis = pick.emphasis
        elif scene.kind != 'end' and not scene.emphasis:  # the text keeps moving while the photo does, whatever the AI left out
            scene.emphasis = 'highlight_sweep' if highlight_span(scene.headline, scene.highlight) else 'underline'
        scene.exit = pick.exit or scene.exit
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
  article's facts and figures only; highlight is 1-3 words copied from that scene's headline). At most 120
  characters for a headline, 160 for a detail, 24 for a stat, 60 for an attribution.
- set_layout: scene, layout. set_camera: scene, camera. set_transition: scene, transition.
- set_animation: scene, role (headline, detail or lead), animation.
- set_emphasis: scene, emphasis (what the text does once it is in). set_exit: scene, exit (how the text leaves).
- set_recipe: recipe (pulse only for breaking, urgent or live news the operator asks for; a Reel Studio look only when
  named). set_pacing: pacing.
- add_scene: after (0 = before scene 1), kind (story, stat or quote), headline, detail, highlight.
Use part ids exactly as listed below. At most 12 operations.
- remove_scene: scene. move_scene: scene, to.
At most one loud part while the text arrives and one while the photo moves; vary entrances and emphases. message is one or two plain sentences about what you changed.

Parts:
'''


class CompOp(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['edit_text', 'set_layout', 'set_camera', 'set_transition', 'set_animation', 'set_emphasis', 'set_exit', 'set_recipe', 'set_pacing',
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
    emphasis: EmphasisId | None = None
    exit: ExitId | None = None
    recipe: str | None = Field(default=None, max_length=24)
    pacing: Pacing | None = None


class CompPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(default='', max_length=400)
    operations: list[CompOp] = Field(default_factory=list, max_length=12)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return light_schema(plain_schema(super().model_json_schema(*args, **kwargs)))


PART_FIELDS = ('layout', 'camera', 'transition', 'animation', 'emphasis', 'exit', 'headline', 'detail', 'lead')


def light_schema(schema):
    """The plan's form as sent to the AI: no length limits, and part ids as plain text (the prompt lists them).

    Gemini compiles every limit and choice into states, and too many is refused ("too many states for serving").
    The answer is still checked against the full CompPlan, and a wrong id or over-long text is asked for again.
    """
    def walk(node, name=None):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {k: v for k, v in node.items() if k not in ('maxLength', 'maxItems', 'minLength', 'minItems')}
        if name in PART_FIELDS:
            out.pop('enum', None)
            out['type'] = 'string'
        return {k: ({p: walk(v, p) for p, v in val.items()} if k == 'properties' else walk(val)) for k, val in out.items()}
    return walk(schema)


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

    def set_emphasis(self, op):
        scene = self.scene(op.scene)
        if op.emphasis:
            self.suits('emphasis', op.emphasis, scene)
            self.note(op.scene, 'emphasis', scene.emphasis, op.emphasis)
            scene.emphasis = op.emphasis

    def set_exit(self, op):
        scene = self.scene(op.scene)
        if op.exit:
            self.note(op.scene, 'exit', scene.exit, op.exit)
            scene.exit = op.exit

    def set_recipe(self, op):
        if not op.recipe or op.recipe == self.reel.recipe:
            return
        if op.recipe not in RECIPE_BY_ID:
            raise Rejected('There is no such recipe.')
        recipe = RECIPE_BY_ID[op.recipe]
        if not allowed(op.recipe, self.request.instruction):
            raise Rejected(f'The {recipe.label} recipe is only used when you ask for it.')
        self.note(None, 'recipe', self.reel.recipe, op.recipe)
        apply_recipe(self.reel, op.recipe)

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
                                       transition=s.transition, emphasis=s.emphasis, exit=s.exit, has_photo=bool(s.image_id), locked=bool(s.locked),
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


# ---- the spoken script ----------------------------------------------------------

SPOKEN_CHARACTERS = 240  # about 15 seconds of speech: room in a scene of at most 20
SCRIPT_SYSTEM = f'''You write the voiceover for a short Facebook news reel. Each scene already has short on-screen
text; the voice tells the story more fully in plain spoken English, as a calm news presenter would.
{GROUNDING}
For each scene listed, write say: one or two spoken sentences of at most {SPOKEN_CHARACTERS} characters that add context
the screen leaves out (who, where, why it matters), without repeating the screen text word for word. Scene 1 opens the
story and makes people keep watching. A stat scene says its figure. A quote scene says who said it and quotes the words
exactly. The end scene invites viewers to comment or read on. The scenes together are one story: do not repeat a fact.
Write numbers as the article writes them. No links, hashtags, emoji or stage directions.'''


class ScriptLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene: int = Field(ge=1, le=MAX_SCENES)
    say: str = Field(max_length=600)  # longer answers are refused below with a reason, not a validation error


class Script(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenes: list[ScriptLine] = Field(default_factory=list, max_length=MAX_SCENES * 2)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return light_schema(plain_schema(super().model_json_schema(*args, **kwargs)))


class ScriptRequest(AIConfig):
    composition: Composition
    source_text: str = Field(default='', max_length=120000)
    goal: Literal['engage', 'readmore'] = 'engage'


def checked_line(say, request):
    """A spoken line the reel may use: short enough for its scene, the article's numbers only, no links."""
    say = ' '.join(say.split()).strip()
    if not say:
        return None, 'no line was written'
    if len(say) > SPOKEN_CHARACTERS + 40:
        return None, 'the line was too long to speak in one scene'
    known = numbers(request.source_text + '\n' + '\n'.join(f'{x.headline} {x.detail} {x.stat}' for x in request.composition.scenes))
    if not numbers(say) <= known or re.search(r'https?://|www\.|#\w', say, re.I):
        return None, 'the line used a number that is not in the article, or a link'
    return say, None


def write_script(request: ScriptRequest, provider=None):
    """A spoken line for every unlocked scene, checked; a scene whose line fails keeps what it had."""
    composition = request.composition
    wanted = [i for i, x in enumerate(composition.scenes) if not x.locked]
    if not wanted:
        raise ValueError('Every scene is locked; unlock a scene to write its voice script.')
    context = dict(goal=request.goal, scenes=[dict(scene=i + 1, kind=x.kind, headline=x.headline, detail=x.detail, stat=x.stat or None,
                                                   attribution=x.attribution or None) for i, x in enumerate(composition.scenes) if i in wanted])
    usage = {}
    ask = provider or (lambda req, **kw: generate_json(req, schema=Script, action='script', effort='low', **kw))
    try:
        script = Script.model_validate(ask(request, system=SCRIPT_SYSTEM, context=context, prefix=article_prefix(request), usage=usage))
    except ValidationError:
        raise ValueError('The AI did not return a usable script. Try again, or use another model.') from None
    lines = {line.scene - 1: line.say for line in script.scenes}
    scenes, written, skipped = [], 0, []
    for index, scene in enumerate(composition.scenes):
        if index not in wanted:
            scenes.append(scene)
            continue
        say, problem = checked_line(lines.get(index, ''), request)
        if say:
            scenes.append(scene.model_copy(update={'say': say[:280]}))
            written += 1
        else:
            scenes.append(scene)
            skipped.append(dict(scene=index + 1, reason=problem))
    result = Composition.model_validate(composition.model_copy(update={'scenes': scenes}).model_dump())
    return dict(composition=result.model_dump(), written=written, skipped=skipped,
                usage=dict(input=usage.get('input', 0), output=usage.get('output', 0)), model=request.model)


__all__ = ['ComposeRequest', 'ComposeDirectRequest', 'ScriptRequest', 'compose', 'direct_composition', 'write_script', 'article_prefix']
