"""Quality rules for compositions, enforced in code: whatever the AI or the operator picks, the reel stays readable.

`review` finds problems and fixes the ones it can (each with the plainest, cheapest fix), then reports the rest.
Text that cannot fit its layout is not fixed here; it stays an error that names the scene.
"""
from dataclasses import dataclass

from .components import PARTS, RECIPE_BY_ID, part
from .composition import Composition, CompositionPlan

ROLES = ('headline', 'detail', 'lead')
CALMER = {'pop': 'rise', 'punch_in': 'rise', 'push_face': 'zoom_in', 'zoom_through': 'crossfade', 'word_pop': 'highlight_sweep'}
VARIETY = ('rise', 'fade', 'left', 'wipe')
OVER_PHOTO = {'news_strap', 'big_headline', 'wire_report', 'big_number', 'quote_card', 'centered_card', 'open_photo', 'ruled_photo'}
MOVABLE = {p.id for p in PARTS if p.text_moves}  # layouts whose text block can move up or down
LONG_TYPEWRITER = 90  # characters; letter by letter takes too long beyond this
MIN_CONTRAST = 4.5
FACE_SHARE = .26      # a face box is about this share of the photo's width


@dataclass
class Issue:
    scene: int      # 1-based
    rule: str
    message: str


def _animation(scene, composition, role):
    return composition.animation_for(scene, role)


def _set_animation(scene, role, animation):
    from .composition import PartStyle
    style = scene.styles.get(role) or PartStyle()
    scene.styles = {**scene.styles, role: style.model_copy(update={'animation': animation})}


def _fallback_layout(composition, kind):
    recipe = RECIPE_BY_ID.get(composition.recipe or '')
    layout = recipe.layout_for(kind) if recipe else 'light_card'
    return layout if kind in part('layout', layout).applies_to else 'light_card'


def compatibility(composition, fixes):
    """Each part suits the scene it is used on; anything that does not is replaced by its plain counterpart."""
    for number, scene in enumerate(composition.scenes, 1):
        layout = part('layout', scene.layout)
        if scene.kind not in layout.applies_to or (layout.needs_photo and not scene.image_id):
            new = _fallback_layout(composition, scene.kind)
            fixes.append(Issue(number, 'compatibility', f'{layout.label} does not suit this scene; used {part("layout", new).label}.'))
            scene.layout = new
        for role in ROLES:
            animation = _animation(scene, composition, role)
            if not animation:
                continue
            reason = None
            if animation == 'count_up' and (role != 'lead' or scene.kind != 'stat'):
                reason = 'Count up is only for a scene\'s big figure'
            elif animation == 'typewriter' and len(scene.headline if role == 'headline' else scene.detail) > LONG_TYPEWRITER:
                reason = 'Typewriter is too slow for text this long'
            if reason:
                _set_animation(scene, role, 'pop' if animation == 'count_up' else 'fade')
                fixes.append(Issue(number, 'compatibility', reason + '; changed it.'))
        if scene.emphasis and scene.kind not in part('emphasis', scene.emphasis).applies_to:
            fixes.append(Issue(number, 'compatibility', f'{part("emphasis", scene.emphasis).label} does not suit this scene; used an underline.'))
            scene.emphasis = 'underline'
        if scene.camera == 'push_face' and not scene.image_id:
            scene.camera = 'zoom_in'
            fixes.append(Issue(number, 'compatibility', 'Push to face needs a photo; used Push in.'))


def busyness(composition, fixes):
    """At most one attention-grabbing part at a time in each scene; the others are calmed.

    When the camera waits for the text, the scene has two moments: the text arriving (transition and entrances), then the
    photo moving (camera and emphasis). Each may have one loud part. A loud layout is loud throughout.
    """
    for number, scene in enumerate(composition.scenes, 1):
        camera = [('camera', None)] if scene.camera and part('camera', scene.camera).loud else []
        arriving = [('transition', None)] if scene.transition and part('transition', scene.transition).loud else []
        for role in ROLES:
            animation = _animation(scene, composition, role)
            if animation and part('animation', animation).loud:
                arriving.append(('animation', role))
        moving = camera + ([('emphasis', None)] if scene.emphasis and part('emphasis', scene.emphasis).loud else [])
        moments = (arriving, moving) if composition.camera_waits else ((camera + arriving + moving[len(camera):]),)
        keep = 0 if part('layout', scene.layout).loud else 1  # the first loud part of each moment stays
        for kind, role in (item for moment in moments for item in moment[keep:]):
            if kind == 'emphasis':
                scene.emphasis = CALMER[scene.emphasis]
            elif kind == 'camera':
                scene.camera = CALMER[scene.camera]
            elif kind == 'transition':
                scene.transition = CALMER[scene.transition]
            elif kind == 'animation':
                _set_animation(scene, role, CALMER[_animation(scene, composition, role)])
            else:
                continue  # a loud layout is the scene's point; calm the rest instead
            fixes.append(Issue(number, 'busy', f'Too much motion at once; calmed the {kind}.'))


def variety(composition, fixes):
    """No headline entrance on more than two scenes in a row."""
    run, previous = 0, None
    for number, scene in enumerate(composition.scenes, 1):
        animation = _animation(scene, composition, 'headline')
        run = run + 1 if animation and animation == previous else 1
        if animation and run > 2:
            new = next(a for a in VARIETY if a != animation)
            _set_animation(scene, 'headline', new)
            fixes.append(Issue(number, 'variety', f'The same entrance three scenes in a row; used {part("animation", new).label}.'))
            animation, run = new, 1
        previous = animation


def _box(layout, role):
    found = [b for b in layout['boxes'] if b['role'] == role]
    return found[0] if found else None


def _overlap(a, b):
    return a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']


def face_box(scene, asset, photo, frame_aspect=9 / 16):
    """Where the photo's faces land in the frame, as fractions; None when there are none or nothing is cropped."""
    focus = (asset or {}).get('focus') or {}
    if not focus.get('faces') or not photo or not asset.get('width') or not asset.get('height'):
        return None
    aspect, rect = asset['width'] / asset['height'], photo['w'] / photo['h'] * frame_aspect  # width over height, in pixels
    # The cover crop keeps the whole height (wide photo) or the whole width (tall photo), centred on the focal point.
    bg = scene.background
    if aspect > rect:
        share = rect / aspect
        left = min(max(bg.focal_x - share / 2, 0), 1 - share)
        fx, fy = (focus['x'] - left) / share, focus['y']
    else:
        share = aspect / rect
        top = min(max(bg.focal_y - share / 2, 0), 1 - share)
        fx, fy = focus['x'], (focus['y'] - top) / share
    if not (0 <= fx <= 1 and 0 <= fy <= 1):
        return None
    w = FACE_SHARE * photo['w']
    h = w * 1.25 * frame_aspect  # a face is a little taller than wide
    return dict(x=photo['x'] + fx * photo['w'] - w / 2, y=photo['y'] + fy * photo['h'] - h / 2, w=w, h=h)


def faces(composition, plan, assets, fixes):
    """No headline over a face: move the text where the layout allows, otherwise use a layout with the text off the photo."""
    for index, scene in enumerate(composition.scenes):
        if not scene.image_id:
            continue
        layout = plan.layout(index)
        face = face_box(scene, assets.get(scene.image_id), layout.get('photo'), plan.W / plan.H)
        text = _box(layout, 'headline')
        if not face or not text or not _overlap(face, text):
            continue
        middle = face['y'] + face['h'] / 2
        if scene.layout in MOVABLE:
            scene.text_position = 1.0 if middle < .5 else 0.0
            fixes.append(Issue(index + 1, 'face', 'The headline covered a face; moved it ' + ('down.' if middle < .5 else 'up.')))
        else:
            scene.layout = 'magazine' if 'story' in part('layout', 'magazine').applies_to else 'light_card'
            fixes.append(Issue(index + 1, 'face', 'The headline covered a face; used Magazine, with the photo below the text.'))


def _luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    r, g, b = rgb
    return .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b)


def contrast(composition, plan, fixes):
    """White text over a photo needs a dark enough photo behind it; darken the photo where it does not."""
    for index, scene in enumerate(composition.scenes):
        if scene.layout not in OVER_PHOTO or not scene.image_id:
            continue
        text = _box(plan.layout(index), 'headline')
        if not text:
            continue
        background = plan.background(index, min(1.0, plan.lengths[index] / 2))
        W, H = background.size
        crop = background.crop((round(text['x'] * W), round(text['y'] * H), round((text['x'] + text['w']) * W), round((text['y'] + text['h']) * H)))
        if not crop.width or not crop.height:
            continue
        mean = tuple(sum(c) / len(c) for c in zip(*crop.resize((24, 8)).getdata()))
        ratio = 1.05 / (_luminance(mean) + .05)  # white text
        if ratio < MIN_CONTRAST:
            shade = min(2.0, (scene.background.shade or 1) + .6)
            if shade > (scene.background.shade or 1):
                scene.background = scene.background.model_copy(update={'shade': shade})
                fixes.append(Issue(index + 1, 'contrast', 'The photo was too bright behind the headline; darkened it.'))


def review(composition: Composition, assets=None, images=None, width=360, rounds=3):
    """The composition with every fixable problem fixed, the fixes made, and what is still wrong (text that cannot fit)."""
    assets, images = assets or {}, images or {}
    current = composition.model_copy(deep=True)
    fixes, problems = [], []
    for _ in range(rounds):
        before = len(fixes)
        compatibility(current, fixes)
        busyness(current, fixes)
        variety(current, fixes)
        try:
            plan = CompositionPlan(current, width, images)
        except ValueError as exc:
            problems.append(Issue(0, 'fit', str(exc)))
            break
        faces(current, plan, assets, fixes)
        if images:
            contrast(current, CompositionPlan(current, width, images), fixes)
        if len(fixes) == before:
            break
    current = Composition.model_validate(current.model_dump())
    return current, fixes, problems
