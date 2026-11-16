"""Check with AI: one picture of every scene, as finished, for a vision model to point out what looks wrong.

The scenes are laid out on one labelled contact sheet (one image, one call). The model answers with the same checked
operations as the director, limited to how scenes look (layout, camera, entrances, photo crop); the words are not
touched here. The quality rules then run over the result. The local rules (quality.py) run first and cost nothing.
"""
import io
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.services.ai import AIConfig, generate_json

from .composer import CompOp, CompositionEditor, describe_parts
from .composition import Composition, CompositionPlan
from .director import Rejected, plain_schema
from .quality import review
from .reel import MAX_SCENES
from .typeset import draw_line

VISUAL_OPS = ('set_layout', 'set_camera', 'set_transition', 'set_animation', 'set_pacing')
SHEET_WIDTH = 1500
COLUMNS = 4


def contact_sheet(frames, columns=COLUMNS, max_width=SHEET_WIDTH):
    """The frames in a grid, each numbered in its corner, as one PNG no wider than `max_width`."""
    if not frames:
        raise ValueError('There are no scenes to check.')
    columns = min(columns, len(frames))
    gap = 12
    width = (max_width - gap * (columns + 1)) // columns
    height = round(width * frames[0].height / frames[0].width)
    rows = -(-len(frames) // columns)
    sheet = Image.new('RGB', (gap + columns * (width + gap), gap + rows * (height + gap)), (24, 24, 27))
    draw = ImageDraw.Draw(sheet)
    for number, frame in enumerate(frames, 1):
        x, y = gap + ((number - 1) % columns) * (width + gap), gap + ((number - 1) // columns) * (height + gap)
        sheet.paste(frame.convert('RGB').resize((width, height), Image.Resampling.LANCZOS), (x, y))
        draw.rectangle((x, y, x + 54, y + 46), fill=(250, 204, 21))
        draw_line(draw, (x + 14, y + 36), str(number), 34, (0, 0, 0, 255), 'bold')
    out = io.BytesIO()
    sheet.save(out, 'PNG', optimize=True)
    return out.getvalue()


SYSTEM = '''You are a picture editor checking a short news reel before it is posted. The image shows every scene as it
will look once its text has appeared, numbered in the yellow corner. Look for what a viewer would notice:
text covering a face or the main subject, text hard to read on the photo, an awkward crop (heads cut off, the subject
off-frame), a layout that wastes the photo, or too much motion for a serious story. Ignore spelling and wording.
Answer with issues (scene number and a short plain description) and operations that fix them, using only:
- set_layout: scene, layout. set_camera: scene, camera. set_transition: scene, transition.
- set_animation: scene, role (headline, detail or lead), animation. set_pacing: pacing.
Change nothing that looks fine. Return no operations when the reel looks right.
The parts you may choose from:
'''


class Issue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene: int = Field(ge=1, le=MAX_SCENES)
    problem: str = Field(max_length=200)


class VisionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(default='', max_length=400)
    issues: list[Issue] = Field(default_factory=list, max_length=12)
    operations: list[CompOp] = Field(default_factory=list, max_length=12)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        return plain_schema(super().model_json_schema(*args, **kwargs))


class VisionRequest(AIConfig):
    composition: Composition
    mood: Literal['', 'calm', 'serious', 'urgent', 'energetic', 'explainer'] = ''


def settled_frames(composition, images, width=360):
    plan = CompositionPlan(composition, width, images)
    return [plan.frame(plan.settled(i)) for i in range(len(composition.scenes))]


def check_with_ai(request: VisionRequest, assets=None, images=None, provider=None):
    """The composition after the AI's visual fixes (only those that pass every rule), with what it saw and what changed."""
    composition = request.composition
    first, local_fixes, problems = review(composition, assets, images)  # the free rules first
    if problems:
        raise ValueError(problems[0].message)
    sheet = contact_sheet(settled_frames(first, images or {}))
    usage = {}
    context = dict(scenes=[dict(scene=i + 1, kind=s.kind, layout=s.layout, camera=s.camera, locked=bool(s.locked)) for i, s in enumerate(first.scenes)],
                   mood=request.mood or None)
    ask = provider or (lambda req, **kw: generate_json(req, schema=VisionPlan, action='vision', effort='low', **kw))
    try:
        plan = VisionPlan.model_validate(ask(request, system=SYSTEM + describe_parts(), context=context, images=[sheet], usage=usage))
    except ValidationError:
        raise ValueError('The AI did not return a usable check. Try again, or use another model.') from None
    holder = type('Edit', (), dict(reel=first, source_text='', instruction=''))()  # visual changes only: the editor's checks, no words
    editor = CompositionEditor(holder)
    refused = []
    for op in plan.operations:
        if op.op not in VISUAL_OPS:
            refused.append('Only how scenes look can change here.')
            continue
        try:
            editor.apply(op)
        except Rejected as exc:
            refused.append(str(exc))
    fixed, fixes, problems = review(Composition.model_validate(editor.reel.model_dump()), assets, images)
    notes = [dict(scene=f.scene, rule='quality', message=f.message) for f in local_fixes + fixes]
    notes += [dict(scene=i.scene, rule='seen', message=i.problem) for i in plan.issues]
    notes += [dict(scene=c['scene'] or 0, rule='changed', message=f"{c['field']}: {c['before'] or 'default'} → {c['after']}") for c in editor.changes]
    return dict(composition=fixed.model_dump(), message=plan.message or ('Looks good.' if not plan.issues else 'Checked.'),
                notes=notes, refused=refused + [p.message for p in problems], usage=dict(input=usage.get('input', 0), output=usage.get('output', 0)))
