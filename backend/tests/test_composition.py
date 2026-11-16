"""AI Reel Studio compositions: parts registry, conversion from Reel Studio, pixel parity and mixed layouts."""
import io

import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError

from backend.services.reel_creator import components
from backend.services.reel_creator.composition import CompositionPlan, Composition, CScene, Background, from_reel, parse_document, plan_for
from backend.services.reel_creator.reel import LOOKS, ElementStyle, Reel, Scene
from backend.services.reel_creator.renderer import Plan
from backend.services.reel_creator.service import ReelCreator

@pytest.fixture(autouse=True)
def cpu_scaling(monkeypatch):
    """Parity compares two paths through the same scaler; staying on the CPU leaves no GPU context behind for later tests."""
    from types import SimpleNamespace
    from backend.services.reel_creator import acceleration
    monkeypatch.setattr(acceleration, '_local', SimpleNamespace(disabled=True))


WIDTH = 360  # the size the service checks layouts at; the drawing code is the same at any width


def photo(tmp_path, name='photo.png', size=(900, 600), color='#346482'):
    path = tmp_path / name
    image = Image.new('RGB', size, color)
    for x in range(0, size[0], 60):  # detail, so a wrong crop or zoom shows up as a pixel difference
        image.paste('#e8c07a', (x, 0, x + 20, size[1]))
    image.save(path)
    return path


def scenes(image_id):
    return [Scene(headline='Flood relief plan approved for three provinces', detail='Payments begin next week.', image_id=image_id, highlight='three provinces'),
            Scene(headline='Council vote', kind='stat', stat='7-2', image_id=image_id),
            Scene(headline='This plan will reach every family', kind='quote', attribution='Mayor Lina Chan'),
            Scene(headline='What do you think of the plan?', kind='end')]


def same(a, b):
    return ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is None


def times(plan):
    return [0, .5, 1.2, plan.starts[1] - .2, plan.starts[1] + .9, plan.starts[2] + 2, plan.duration - .1]


def test_parts_registry_matches_the_schema_literals():
    for kind, literal in components.LITERALS.items():
        assert {p.id for p in components.PARTS if p.kind == kind} == components.literal_values(kind), kind
    assert {r.look for r in components.RECIPES} == set(LOOKS)
    from backend.services.reel_creator.renderer import TYPE
    assert all(p.look in TYPE and hasattr(Plan, '_' + p.look) for p in components.parts('layout'))
    assert all(p.use_when for p in components.PARTS)


@pytest.mark.parametrize('look', sorted(LOOKS))
@pytest.mark.parametrize('ratio', ['9:16', '4:5'])
def test_every_look_renders_the_same_as_a_composition(tmp_path, look, ratio):
    images = {'a' * 32: photo(tmp_path)}
    reel = Reel(look=look, ratio=ratio, brand='LOCAL NEWS', source='City Wire', dateline='Tuesday', scenes=scenes('a' * 32))
    old, new = Plan(reel, WIDTH, images), CompositionPlan(from_reel(reel), WIDTH, images)
    assert (old.W, old.H, old.duration) == (new.W, new.H, new.duration)
    for t in times(old):
        assert same(old.frame(t), new.frame(t)), f'{look} {ratio} differs at {t:.2f}s'


def test_styles_motion_and_transitions_survive_conversion(tmp_path):
    images = {'a' * 32: photo(tmp_path)}
    base = scenes('a' * 32)
    base[0] = base[0].model_copy(update=dict(style={'headline': ElementStyle(font='serif', animation='wipe', color='#ffcc00')}, motion='none'))
    base[1] = base[1].model_copy(update=dict(transition='slide', fit='blur'))
    base[2] = base[2].model_copy(update=dict(transition='cut', backdrop='#223344'))
    reel = Reel(look='wire', motion='zoom_out', accent='#12ab34', tag='LIVE NOW', style={'detail': ElementStyle(scale=.8)}, scenes=base)
    composition = from_reel(reel)
    assert composition.scenes[0].camera == 'still' and composition.scenes[1].transition == 'slide'
    old, new = Plan(reel, WIDTH, images), CompositionPlan(composition, WIDTH, images)
    for t in times(old):
        assert same(old.frame(t), new.frame(t)), f'differs at {t:.2f}s'


def test_each_scene_of_a_mixed_composition_is_drawn_by_its_own_layout(tmp_path):
    images = {'a' * 32: photo(tmp_path)}
    layouts = ['news_strap', 'magazine', 'light_card', 'photo_panel']
    composition = from_reel(Reel(look='clean', scenes=scenes('a' * 32)))
    mixed = composition.model_copy(update=dict(scenes=[s.model_copy(update=dict(layout=layout)) for s, layout in zip(composition.scenes, layouts)]))
    plan = CompositionPlan(mixed, WIDTH, images)
    for index, layout in enumerate(layouts):
        uniform = mixed.model_copy(update=dict(scenes=[s.model_copy(update=dict(layout=layout)) for s in mixed.scenes]))
        t = plan.starts[index] + 1.0  # inside the scene, away from transitions
        assert same(plan.frame(t), CompositionPlan(uniform, WIDTH, images).frame(t)), f'scene {index + 1} ({layout})'
    assert plan.layout(0)['boxes'] and plan.layout(3)['boxes']


def test_a_scene_too_long_for_its_layout_is_named():
    composition = Composition(scenes=[CScene(layout='light_card', headline='Short headline'),
                                      CScene(layout='big_headline', kind='stat', stat='W' * 23, headline='Count')])
    with pytest.raises(ValueError, match='Scene 2'):
        CompositionPlan(composition, WIDTH, {})


def test_composition_documents_validate_and_round_trip():
    composition = Composition(recipe='editorial', scenes=[CScene(headline='A', background=Background(color='#112233'))])
    again = parse_document(composition.model_dump())
    assert isinstance(again, Composition) and again == composition
    assert isinstance(parse_document(Reel().model_dump()), Reel)
    assert composition.music_preset == LOOKS['editorial']['music']
    with pytest.raises(ValidationError):
        Composition(recipe='nonsense')
    with pytest.raises(ValidationError):
        CScene(layout='nonsense')
    with pytest.raises(ValidationError):
        Composition(scenes=[CScene(headline='x', seconds=20)] * 5)


def test_the_service_renders_a_composition_poster_and_layout(tmp_path):
    creator = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    try:
        data = io.BytesIO()
        Image.open(photo(tmp_path)).save(data, 'PNG')
        asset = creator.import_asset(data.getvalue(), 'news.png', 'image')
        composition = Composition(recipe='politics', scenes=[
            CScene(layout='photo_panel', headline='Council approves flood relief', background=Background(image_id=asset['id'])),
            CScene(layout='pop_collage', headline='Mayor celebrates the vote', background=Background(image_id=asset['id']))])
        poster = Image.open(io.BytesIO(creator.render_poster(composition, 1)))
        assert poster.size[0] > 0
        assert creator.scene_layout(composition, 0)['boxes']
        images, logo, music = creator.validate_assets(composition)
        assert set(images) == {asset['id']} and music
        assert isinstance(plan_for(composition, 180, images), CompositionPlan)
    finally:
        creator.stop()


# --- new parts --------------------------------------------------------------

def composed(image_id, **scene):
    base = dict(headline='Council approves the flood relief plan today', detail='Payments begin next week.',
                background=Background(image_id=image_id))
    return Composition(scenes=[CScene(**{**base, **scene}), CScene(headline='What do you think?', kind='end')])


@pytest.mark.parametrize('layout', ['big_number', 'quote_card', 'centered_card', 'split'])
def test_new_layouts_draw_their_text_and_photo(tmp_path, layout):
    images = {'a' * 32: photo(tmp_path)}
    kind = {'big_number': dict(kind='stat', stat='300,000', headline='Homes without power'),
            'quote_card': dict(kind='quote', headline='We will rebuild every home', attribution='Mayor Lina Chan')}.get(layout, {})
    plan = CompositionPlan(composed('a' * 32, layout=layout, **kind), WIDTH, images)
    boxes = {b['role'] for b in plan.layout(0)['boxes']}
    assert 'headline' in boxes and ('lead' in boxes) == bool(kind)
    settled = plan.frame(plan.settled(0))
    assert settled.size == (plan.W, plan.H)
    bare = CompositionPlan(composed(None, layout=layout, **kind), WIDTH, images)  # no photo still draws
    assert bare.frame(bare.settled(0)).size == (plan.W, plan.H)


def test_word_by_word_and_typewriter_reveal_the_headline_in_steps(tmp_path):
    images = {'a' * 32: photo(tmp_path)}
    from backend.services.reel_creator.composition import PartStyle
    for motion in ('words', 'typewriter'):
        plan = CompositionPlan(composed('a' * 32, styles={'headline': PartStyle(animation=motion)}), WIDTH, images)
        line = next(e for e in plan.scene_plans[0].scene_elements[0] if e.role == 'headline')
        assert line.anim == motion and len(line.cuts) >= 2 and list(line.cuts) == sorted(line.cuts)
        assert line.dur >= .45  # takes as long as its words or letters need, never less than a plain entrance
        early, late = plan.scene_plans[0]._revealed(line.tile, line.cuts, .3, motion == 'words'), line.tile
        drawn = lambda tile: sum(1 for a in tile.getchannel('A').getdata() if a)
        assert early is not None and 0 < drawn(early) < drawn(late)


def test_count_up_climbs_to_the_figure_and_words_fall_back_to_pop(tmp_path):
    images = {'a' * 32: photo(tmp_path)}
    from backend.services.reel_creator.composition import PartStyle
    plan = CompositionPlan(composed('a' * 32, layout='big_number', kind='stat', stat='$1,250', headline='Paid to each family',
                                    styles={'lead': PartStyle(animation='count_up')}), WIDTH, images)
    lead = next(e for e in plan.scene_plans[0].scene_elements[0] if e.role == 'lead')
    assert lead.anim == 'count_up' and len(lead.frames) > 10 and lead.frames[-1].tobytes() == lead.tile.tobytes()
    assert all(f.size == lead.tile.size for f in lead.frames)
    words = CompositionPlan(composed('a' * 32, layout='big_number', kind='stat', stat='half', headline='Of homes damaged',
                                     styles={'lead': PartStyle(animation='count_up')}), WIDTH, images)
    assert next(e for e in words.scene_plans[0].scene_elements[0] if e.role == 'lead').anim == 'pop'


@pytest.mark.parametrize('camera', ['pan_left', 'pan_right', 'pan_up', 'push_face'])
def test_new_cameras_move_the_photo(tmp_path, camera):
    images = {'a' * 32: photo(tmp_path, size=(1600, 1600))}
    plan = CompositionPlan(composed('a' * 32, camera=camera, layout='news_strap'), WIDTH, images)
    start, end = plan.background(0, .1), plan.background(0, plan.lengths[0] - .1)
    assert not same(start, end)


@pytest.mark.parametrize('transition', ['wipe', 'zoom_through'])
def test_new_transitions_blend_into_the_next_scene(tmp_path, transition):
    images = {'a' * 32: photo(tmp_path), 'b' * 32: photo(tmp_path, 'other.png', color='#aa2233')}
    composition = Composition(scenes=[CScene(headline='First scene of the story', background=Background(image_id='a' * 32)),
                                      CScene(headline='Second scene', background=Background(image_id='b' * 32), transition=transition)])
    plan = CompositionPlan(composition, WIDTH, images)
    end = plan.lengths[0]
    middle = plan.frame(end - .25)
    assert not same(middle, plan.frame(end - .9)) and not same(middle, plan.frame(end + .3))


def test_pacing_scales_entrances():
    from backend.services.reel_creator.composition import PartStyle  # noqa: F401
    calm, fast = (CompositionPlan(Composition(pacing=pace, scenes=[CScene(headline='A steady headline for pacing')]), WIDTH, {})
                  for pace in ('calm', 'fast'))
    first = lambda plan: next(e for e in plan.scene_plans[0].scene_elements[0] if e.role == 'headline')
    assert first(calm).dur > first(fast).dur


def test_a_quote_with_a_speaker_and_a_detail_line_lays_out_in_every_look():
    for look in LOOKS:
        Plan(Reel(look=look, scenes=[Scene(kind='quote', headline='We will rebuild every home', attribution='Mayor Lina Chan',
                                           detail='Payments begin next week.')]), WIDTH, {})
