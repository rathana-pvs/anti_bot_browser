"""Help before posting: a fact panel (local, free), hook options (AI) and a story shape check (AI).

The fact panel links every number, name and quote on screen or in the voice to the article sentence it comes from.
The AI coaches only suggest; their answers are checked like every other AI answer, and nothing is applied here.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.services.ai import AIConfig, generate_json

from .composer import light_schema
from .composition import Composition
from .director import article_prefix, plain_schema
from .news_ai import normalized, numbers
from .reel import MAX_SCENES
from .writer import GROUNDING, checked_highlight

# ---- fact panel ------------------------------------------------------------------

SENTENCE = re.compile(r'(?<=[.!?…])["”’)]?\s+')
WORD = re.compile(r"[\w'’-]+")
NAME = re.compile(r"\b[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*")
COMMON = frozenset('''a an the and or but if of in on at to for from by with as is are was were be been it its this that these
those he she they we you i his her their our your not no new more most after before over under about into than then now
will would can could may might has have had do does did said says say also just still all any each one who what when
where why how which while because so very'''.split())
SOURCE_SHARE = .5  # a sentence is the source when it holds at least half of a line's content words


def content_words(text):
    return {w for w in (normalized(x).strip("'’-") for x in WORD.findall(text)) if len(w) > 2 and w not in COMMON}


def sentences(article):
    return [s.strip() for s in SENTENCE.split(' '.join(article.split())) if len(s.strip()) > 12]


def names_in(text):
    """Capitalised names in a line, leaving out the first word of each sentence (capitalised anyway)."""
    found = []
    for part in re.split(r'(?<=[.!?:])\s+', text.strip()):
        for match in NAME.finditer(part):
            name = match.group().rstrip('.')
            if match.start() == 0:  # the sentence's first word: keep only the rest of a multi-word name
                name = ' '.join(name.split()[1:])
            if name and name.casefold() not in COMMON:
                found.append(name)
    return found


def name_in(name, facts):
    """A name the article has, allowing other forms of it: "Democratic" for "Democrats", "Cambodian" for "Cambodia"."""
    if normalized(name) in facts:
        return True
    words = [normalized(w).strip("'’.-") for w in name.split()]
    return all(re.search(r'(?<!\w)' + re.escape(w[:max(4, len(w) - 3)]), facts) for w in words if w)


def source_of(text, article_sentences):
    """The article sentence that best supports a line, or '' when none holds enough of its words."""
    words = content_words(text)
    if not words:
        return ''
    best, share = '', 0.
    for sentence in article_sentences:
        overlap = len(words & content_words(sentence)) / len(words)
        if overlap > share:
            best, share = sentence, overlap
    return best if share >= SOURCE_SHARE else ''


def fact_panel(composition: Composition, article: str):
    """For each scene, each line it shows or says: where it comes from in the article, and what the article lacks."""
    facts, known, lines = normalized(article), numbers(article), sentences(article)
    out = []
    for number, scene in enumerate(composition.scenes, 1):
        fields = [('stat', scene.stat), ('headline', scene.headline), ('detail', scene.detail), ('attribution', scene.attribution),
                  ('voice', scene.say)]
        checks = []
        for field, text in fields:
            text = ' '.join(text.split())
            if not text:
                continue
            missing_numbers = sorted(numbers(text) - known)
            missing_names = [n for n in dict.fromkeys(names_in(text)) if not name_in(n, facts)]
            quote_missing = field == 'headline' and scene.kind == 'quote' and normalized(text.strip(' "“”\'‘’')) not in facts
            source = source_of(text, lines) if article.strip() else ''
            problem = bool(missing_numbers or missing_names or quote_missing)
            invite = scene.kind == 'end' and field != 'voice' and not problem  # "Full story in the first comment" claims nothing
            checks.append(dict(field=field, text=text, source=source, missing_numbers=missing_numbers, missing_names=missing_names,
                               quote_missing=quote_missing, note='Call to action: nothing to check' if invite else '',
                               status='problem' if problem else 'ok' if source or invite else 'check'))
        out.append(dict(scene=number, checks=checks))
    counts = {s: sum(c['status'] == s for scene in out for c in scene['checks']) for s in ('ok', 'check', 'problem')}
    return dict(scenes=out, counts=counts, has_article=bool(article.strip()))


# ---- hook coach --------------------------------------------------------------------

HOOK_SYSTEM = f'''You coach the opening of a Facebook news reel. Most viewers scroll with the sound off and decide in two
seconds whether to stop. Scene 1's headline is the hook.
{GROUNDING}
Write exactly 3 new hooks of 4-10 words, each in a different angle: question (a real question the story answers),
number (its most striking figure), consequence (what it means for the viewer), surprise (the unexpected fact), or human
(the person at its centre). Each must be fully supported by the article, different from current_hook and from each
other, and never exaggerate, sensationalise or invent tension. highlight is 1-3 words copied from that hook to colour.
score is 1-10 for how likely it is to make someone stop scrolling while staying accurate and fair. why is one short
plain sentence.'''


class HookOption(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(max_length=200)
    angle: Literal['question', 'number', 'consequence', 'surprise', 'human'] = 'question'
    highlight: str = Field(default='', max_length=80)
    score: int = Field(default=5, ge=1, le=10)
    why: str = Field(default='', max_length=300)


class HookAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hooks: list[HookOption] = Field(default_factory=list, max_length=6)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return light_schema(plain_schema(super().model_json_schema(*args, **kwargs)))


class CoachRequest(AIConfig):
    composition: Composition
    source_text: str = Field(default='', max_length=120000)
    goal: Literal['engage', 'readmore'] = 'engage'


def grounded(text, request):
    known = numbers(request.source_text + '\n' + '\n'.join(f'{s.headline} {s.detail} {s.stat}' for s in request.composition.scenes))
    return numbers(text) <= known and not re.search(r'https?://|www\.|#\w', text, re.I)


def coach_hooks(request: CoachRequest, provider=None):
    """Three checked hook options for scene 1, best first. Choosing one is the operator's."""
    if not request.source_text.strip():
        raise ValueError('This reel has no article saved, so new hooks cannot be checked against it.')
    current = request.composition.scenes[0].headline
    context = dict(goal=request.goal, current_hook=current, other_scenes=[s.headline for s in request.composition.scenes[1:]])
    usage = {}
    ask = provider or (lambda req, **kw: generate_json(req, schema=HookAnswer, action='hooks', effort='low', **kw))
    try:
        answer = HookAnswer.model_validate(ask(request, system=HOOK_SYSTEM, context=context, prefix=article_prefix(request), usage=usage))
    except ValidationError:
        raise ValueError('The AI did not return usable hooks. Try again, or use another model.') from None
    seen, hooks = {normalized(current).strip(' ?.!')}, []
    for hook in answer.hooks:
        text = ' '.join(hook.text.split()).strip()
        key = normalized(text).strip(' ?.!')
        if not 3 <= len(text.split()) <= 12 or len(text) > 120 or key in seen or not grounded(text, request):
            continue
        seen.add(key)
        hooks.append(dict(text=text, angle=hook.angle, highlight=checked_highlight(text, hook.highlight), score=hook.score,
                          why=' '.join(hook.why.split())[:200]))
    if not hooks:
        raise ValueError('The AI wrote no hook that the article supports. Try again.')
    hooks.sort(key=lambda h: -h['score'])
    return dict(hooks=hooks[:3], usage=dict(input=usage.get('input', 0), output=usage.get('output', 0)), model=request.model)


# ---- story shape -------------------------------------------------------------------

STORY_SYSTEM = f'''You are a senior news video editor reviewing the running order of a short Facebook news reel before it
is posted. A strong reel tells one story: a hook (scene 1), the context, the key fact or figure, why it matters, and an
ending that invites comments or reading on. One idea per scene; no scene repeats another's fact; nothing important from
the article is missing; nothing is padding.
{GROUNDING}
Return verdict (strong, ok or weak), summary (one or two plain sentences) and at most 4 suggestions, most useful first.
Each suggestion has kind and problem (one short sentence) and:
- move: scene and to (new position). Scene 1 stays the hook and an end scene stays last.
- cut: scene (a scene that repeats or adds nothing).
- rewrite: scene and instruction (what to change in its words, for the director).
- add: after (the scene number it follows) and instruction (the article fact the new scene should carry).
Never suggest touching a scene marked locked. Return no suggestions when the reel is strong.'''


class StorySuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['move', 'cut', 'rewrite', 'add']
    scene: int | None = Field(default=None, ge=1, le=MAX_SCENES)
    to: int | None = Field(default=None, ge=1, le=MAX_SCENES)
    after: int | None = Field(default=None, ge=0, le=MAX_SCENES)
    problem: str = Field(default='', max_length=300)
    instruction: str = Field(default='', max_length=400)


class StoryAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    verdict: Literal['strong', 'ok', 'weak'] = 'ok'
    summary: str = Field(default='', max_length=500)
    suggestions: list[StorySuggestion] = Field(default_factory=list, max_length=8)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return light_schema(plain_schema(super().model_json_schema(*args, **kwargs)))


def usable_suggestion(s, scenes):
    """The suggestion when it can be done on this reel: real scenes, nothing locked, the hook first and the end last."""
    count = len(scenes)
    end = scenes[-1].kind == 'end'
    if s.kind in ('move', 'cut', 'rewrite'):
        if not s.scene or s.scene > count or scenes[s.scene - 1].locked:
            return None
    if s.kind == 'move':
        last = count - 1 if end else count
        if s.scene == 1 or not s.to or s.to == 1 or s.to == s.scene or s.to > last or (end and s.scene == count):
            return None
        if any(x.locked for x in scenes[min(s.scene, s.to) - 1:max(s.scene, s.to)]):
            return None  # a locked scene in between would shift
    if s.kind == 'cut' and (s.scene == 1 or count <= 2):
        return None
    if s.kind in ('rewrite', 'add') and len(s.instruction.strip()) < 8:
        return None
    if s.kind == 'add' and (s.after is None or s.after < 1 or s.after > (count - 1 if end else count) or count >= MAX_SCENES):
        return None
    return dict(kind=s.kind, scene=s.scene, to=s.to if s.kind == 'move' else None, after=s.after if s.kind == 'add' else None,
                problem=' '.join(s.problem.split())[:200], instruction=' '.join(s.instruction.split())[:300])


def story_check(request: CoachRequest, provider=None):
    """The AI's read of the running order, with suggestions this reel can take. Nothing is changed here."""
    scenes = request.composition.scenes
    context = dict(goal=request.goal, scenes=[dict(scene=i + 1, kind=s.kind, headline=s.headline, detail=s.detail, stat=s.stat or None,
                                                   says=s.say or None, locked=bool(s.locked)) for i, s in enumerate(scenes)])
    usage = {}
    ask = provider or (lambda req, **kw: generate_json(req, schema=StoryAnswer, action='story', effort='low', **kw))
    raw = ask(request, system=STORY_SYSTEM, context=context, prefix=article_prefix(request), usage=usage)
    if isinstance(raw, dict) and isinstance(raw.get('suggestions'), list):  # one malformed suggestion does not lose the rest
        kept = []
        for item in raw['suggestions']:
            try:
                StorySuggestion.model_validate(item)
                kept.append(item)
            except ValidationError:
                pass
        raw = {**raw, 'suggestions': kept[:8]}
    try:
        answer = StoryAnswer.model_validate(raw)
    except ValidationError:
        raise ValueError('The AI did not return a usable review. Try again, or use another model.') from None
    suggestions = [u for u in (usable_suggestion(s, scenes) for s in answer.suggestions) if u][:4]
    return dict(verdict=answer.verdict, summary=' '.join(answer.summary.split())[:300], suggestions=suggestions,
                usage=dict(input=usage.get('input', 0), output=usage.get('output', 0)), model=request.model)


__all__ = ['CoachRequest', 'coach_hooks', 'fact_panel', 'story_check']
