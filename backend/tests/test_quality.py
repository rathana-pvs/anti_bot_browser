"""Quality rules for AI Reel Studio compositions: compatibility, busyness, variety, faces, contrast and fit."""
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.services.reel_creator.composition import Background, Composition, CScene, PartStyle
from backend.services.reel_creator.quality import face_box, review


@pytest.fixture(autouse=True)
def cpu_scaling(monkeypatch):
    from backend.services.reel_creator import acceleration
    monkeypatch.setattr(acceleration, '_local', SimpleNamespace(disabled=True))


def rules(fixes):
    return [(f.scene, f.rule) for f in fixes]


def test_parts_that_do_not_suit_a_scene_are_replaced():
    composition = Composition(recipe='clean', scenes=[
        CScene(layout='big_number', headline='A story scene has no figure'),
        CScene(layout='quote_card', kind='stat', stat='7-2', headline='Council vote', styles={'headline': PartStyle(animation='count_up')}),
        CScene(layout='pop_collage', headline='No photo for a collage', camera='push_face'),
        CScene(headline='x ' * 60, styles={'headline': PartStyle(animation='typewriter')})])
    fixed, fixes, problems = review(composition)
    assert [s.layout for s in fixed.scenes[:3]] == ['light_card', 'light_card', 'light_card']
    assert fixed.scenes[1].styles['headline'].animation == 'pop'
    assert fixed.scenes[2].camera == 'zoom_in'
    assert fixed.scenes[3].styles['headline'].animation == 'fade'
    assert {(1, 'compatibility'), (2, 'compatibility'), (3, 'compatibility'), (4, 'compatibility')} <= set(rules(fixes))
    assert not problems


def test_one_loud_part_per_scene():
    composition = Composition(camera_waits=False, scenes=[CScene(headline='A punchy hook for the reel', camera='push_face', transition='zoom_through',
                                             background=Background(image_id='a' * 32), styles={'headline': PartStyle(animation='punch_in')})])
    fixed, fixes, _ = review(composition)
    scene = fixed.scenes[0]
    loud = [scene.camera == 'push_face', scene.transition == 'zoom_through', scene.styles['headline'].animation == 'punch_in']
    assert sum(loud) == 1 and ('busy' in {f.rule for f in fixes})


def test_one_loud_part_while_text_arrives_and_one_while_the_photo_moves():
    """With the camera waiting for the text, a loud entrance and a loud camera no longer happen at once."""
    scene = CScene(headline='A punchy hook for the reel', highlight='punchy hook', camera='push_face', transition='zoom_through',
                   emphasis='word_pop', background=Background(image_id='a' * 32), styles={'headline': PartStyle(animation='punch_in')})
    fixed = review(Composition(scenes=[scene]))[0].scenes[0]
    assert fixed.camera == 'push_face' and fixed.emphasis == 'highlight_sweep'  # the photo's moment: the camera stays
    assert [fixed.transition, fixed.styles['headline'].animation].count('zoom_through') + [fixed.styles['headline'].animation].count('punch_in') == 1


def test_emphasis_suits_its_scene():
    fixed, fixes, _ = review(Composition(scenes=[CScene(headline='No figure here', emphasis='pulse')]))
    assert fixed.scenes[0].emphasis == 'underline' and (1, 'compatibility') in rules(fixes)


def test_no_entrance_three_times_in_a_row():
    rise = {'headline': PartStyle(animation='rise')}
    fixed, fixes, _ = review(Composition(scenes=[CScene(headline=f'Scene number {n}', styles=rise) for n in range(4)]))
    animations = [s.styles['headline'].animation for s in fixed.scenes]
    assert animations[:2] == ['rise', 'rise'] and animations[2] != 'rise' and (3, 'variety') in rules(fixes)


def test_faces_are_placed_through_the_crop():
    scene = CScene(background=Background(image_id='a' * 32, focal_x=.5, focal_y=.4))
    photo = dict(x=0, y=0, w=1, h=1)
    asset = dict(width=1600, height=900, focus=dict(x=.5, y=.3, faces=1))
    box = face_box(scene, asset, photo)
    assert box and abs(box['x'] + box['w'] / 2 - .5) < .01 and abs(box['y'] + box['h'] / 2 - .3) < .01
    assert face_box(scene, dict(asset, focus=dict(x=.05, y=.3, faces=1)), photo) is None  # cropped away
    assert face_box(scene, dict(asset, focus=dict(x=.5, y=.3, faces=0)), photo) is None


def photo(tmp_path, color, name):
    path = tmp_path / name
    Image.new('RGB', (900, 1600), color).save(path)
    return path


def test_text_over_a_face_moves_or_changes_layout(tmp_path):
    images = {'a' * 32: photo(tmp_path, '#335577', 'a.png')}
    low_face = dict(width=900, height=1600, focus=dict(x=.5, y=.72, faces=1))  # where News strap puts its text
    strap = Composition(scenes=[CScene(layout='news_strap', headline='Mayor speaks after the vote', background=Background(image_id='a' * 32))])
    fixed, fixes, _ = review(strap, {'a' * 32: low_face}, images)
    assert (1, 'face') in rules(fixes) and fixed.scenes[0].text_position == 0.0
    card = strap.model_copy(update=dict(scenes=[strap.scenes[0].model_copy(update=dict(layout='centered_card'))]))
    middle_face = dict(low_face, focus=dict(x=.5, y=.45, faces=1))
    fixed, fixes, _ = review(card, {'a' * 32: middle_face}, images)
    assert (1, 'face') in rules(fixes) and fixed.scenes[0].layout == 'magazine'


def test_a_bright_photo_is_darkened_behind_white_text(tmp_path):
    images = {'a' * 32: photo(tmp_path, '#f4f1e8', 'bright.png'), 'b' * 32: photo(tmp_path, '#101418', 'dark.png')}
    bright = Composition(scenes=[CScene(layout='big_number', kind='stat', stat='20 min', headline='Saved on the trip',
                                        background=Background(image_id='a' * 32, shade=.2))])
    fixed, fixes, _ = review(bright, {}, images)
    assert (1, 'contrast') in rules(fixes) and fixed.scenes[0].background.shade > .2
    dark = bright.model_copy(deep=True)
    dark.scenes[0].background.image_id = 'b' * 32
    assert 'contrast' not in {f.rule for f in review(dark, {}, images)[1]}


def test_text_that_cannot_fit_is_reported_not_hidden():
    composition = Composition(scenes=[CScene(layout='big_number', kind='stat', stat='W' * 23, headline='Count')])
    _, _, problems = review(composition)
    assert problems and problems[0].rule == 'fit' and 'Scene 1' in problems[0].message


def test_review_route(tmp_path, monkeypatch):
    import io
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import reel_creator as router
    from backend.services.reel_creator.service import ReelCreator
    creator = ReelCreator(tmp_path / 'creator', tmp_path / 'media', admission=lambda: None)
    monkeypatch.setattr(router, 'get_service', lambda: creator)
    try:
        data = io.BytesIO()
        Image.new('RGB', (900, 1600), '#335577').save(data, 'PNG')
        asset = creator.import_asset(data.getvalue(), 'p.png', 'image')
        body = Composition(scenes=[CScene(layout='big_number', headline='Not a figure', background=Background(image_id=asset['id']))]).model_dump()
        app = FastAPI()
        app.include_router(router.router)
        answer = TestClient(app).post('/api/reel-creator/compositions/review', json=body).json()
        assert answer['composition']['scenes'][0]['layout'] == 'light_card' and answer['fixes'][0]['rule'] == 'compatibility'
    finally:
        creator.stop()
