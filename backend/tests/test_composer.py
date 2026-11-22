"""AI composer: scenario-driven part choices, recipe rules, quality review and chat edits on compositions."""
from types import SimpleNamespace

import pytest

from backend.services.reel_creator.composer import ComposeDirectRequest, ComposeRequest, compose, describe_parts, direct_composition
from backend.services.reel_creator.composition import Background, Composition, CScene, PartStyle

ARTICLE = ('The city council voted 7 to 2 on Tuesday to approve Metro Line 4, linking the airport to downtown. '
           '"This changes how our city moves," said Mayor Lina Chan. Officials said the trip will be 20 minutes shorter '
           'and construction starts in March.')
WRITTEN = dict(look='clean', hooks=['Metro Line 4 approved'], caption='A caption.', comment='', source_label='City Wire',
               scenes=[dict(headline='Metro Line 4 gets the green light', detail='The council voted 7 to 2.', kind='story', stat='',
                            attribution='', highlight='Metro Line 4'),
                       dict(headline='Shorter trip to downtown', detail='', kind='stat', stat='20 minutes', attribution='', highlight=''),
                       dict(headline='This changes how our city moves', detail='', kind='quote', stat='', attribution='Mayor Lina Chan',
                            highlight='')],
               end=dict(headline='Would you ride it?', detail='Tell us in the comments.', highlight=''))


@pytest.fixture(autouse=True)
def cpu_scaling(monkeypatch):
    from backend.services.reel_creator import acceleration
    monkeypatch.setattr(acceleration, '_local', SimpleNamespace(disabled=True))


def request(**values):
    return ComposeRequest(**{'provider': 'gemini', 'api_key': 'k', 'model': 'gemini-3.8-flash', 'text': ARTICLE, **values})


def provider(answer):
    seen = []

    def ask(req, *, system, context, usage, **_):
        seen.append(dict(system=system, context=context))
        usage.update(input=900, output=120)
        return answer
    ask.seen = seen
    return ask


def test_parts_are_chosen_per_scene_and_checked():
    answer = dict(mood='explainer', pacing='fast', recipe='clean', scenes=[
        dict(layout='split', headline='words', camera='pan_left'),
        dict(layout='big_number', lead='count_up'),
        dict(layout='quote_card', headline='typewriter', transition='wipe'),
        dict(layout='big_number', headline='punch_in')])  # a figure layout on the end card does not suit it
    ask = provider(answer)
    result = compose(request(scenario='calm explainer for commuters'), writer=lambda r: WRITTEN, provider=ask)
    scenes = result['composition']['scenes']
    assert [s['layout'] for s in scenes] == ['split', 'big_number', 'quote_card', 'centered_card']  # clean's look is Reel Studio's; spotlight's end card
    assert scenes[0]['styles']['headline']['animation'] == 'words' and scenes[0]['camera'] == 'pan_left'
    assert scenes[1]['styles']['lead']['animation'] == 'count_up'
    assert scenes[2]['transition'] == 'wipe' and scenes[0]['transition'] is None
    assert result['composition']['pacing'] == 'fast' and result['recipe'] == 'spotlight' and result['caption'] == 'A caption.'
    assert result['usage'] == {'input': 900, 'output': 120}
    facts = ask.seen[0]['context']['scenes']
    assert [f['kind'] for f in facts] == ['story', 'stat', 'quote', 'end'] and ask.seen[0]['context']['scenario'].startswith('calm')
    assert 'count_up' in ask.seen[0]['system'] and 'big_number' in ask.seen[0]['system']


def test_urgent_recipes_need_the_operator_to_ask():
    loud = dict(recipe='breaking', scenes=[])
    assert compose(request(scenario='make it exciting'), writer=lambda r: WRITTEN, provider=provider(loud))['recipe'] == 'spotlight'
    assert compose(request(scenario='a breaking update'), writer=lambda r: WRITTEN, provider=provider(loud))['recipe'] == 'breaking'
    assert compose(request(recipe='editorial'), writer=lambda r: WRITTEN, provider=provider(loud))['recipe'] == 'editorial'
    pulse = dict(recipe='pulse', scenes=[])
    assert compose(request(scenario='make it exciting'), writer=lambda r: WRITTEN, provider=provider(pulse))['recipe'] == 'spotlight'
    assert compose(request(scenario='live coverage of the vote'), writer=lambda r: WRITTEN, provider=provider(pulse))['recipe'] == 'pulse'


def test_the_ai_picks_ai_studio_recipes_not_reel_studio_looks():
    ask = provider(dict(recipe='night_desk', scenes=[]))
    result = compose(request(scenario='serious analysis'), writer=lambda r: WRITTEN, provider=ask)
    assert result['recipe'] == 'night_desk' and result['composition']['pacing'] == 'calm'
    scenes = result['composition']['scenes']
    assert scenes[0]['layout'] == 'open_photo' and scenes[0]['exit'] == 'wipe_out' and scenes[1]['layout'] == 'big_number'
    assert compose(request(), writer=lambda r: WRITTEN, provider=provider(dict(recipe='wire', scenes=[])))['recipe'] == 'spotlight'
    system = ask.seen[0]['system']
    assert '- night_desk (Night desk)' in system and '- wire (' not in system and 'only when the operator names one: breaking' in system


def test_photos_go_to_scenes_in_order_with_their_face_focus():
    assets = {'a' * 32: dict(width=900, height=1600, focus=dict(x=.3, y=.2, faces=1))}
    ask = provider(dict(scenes=[]))
    result = compose(request(photos=['a' * 32]), assets=assets, writer=lambda r: WRITTEN, provider=ask)
    first = result['composition']['scenes'][0]['background']
    assert first['image_id'] == 'a' * 32 and (first['focal_x'], first['focal_y']) == (.3, .2)
    assert ask.seen[0]['context']['scenes'][0]['has_face'] is True


def test_an_unusable_choice_keeps_the_recipe_look():
    result = compose(request(), writer=lambda r: WRITTEN, provider=provider({'scenes': 'nonsense'}))
    assert result['recipe'] == 'spotlight'
    assert [s['layout'] for s in result['composition']['scenes']] == ['centered_card', 'big_number', 'quote_card', 'centered_card']


def test_the_writer_is_asked_for_one_scene_per_photo():
    seen = []
    compose(request(photos=['a' * 32, 'b' * 32]), writer=lambda r: seen.append(r) or WRITTEN, provider=provider({}))
    assert seen[0].scenes == 2 and seen[0].caption is True


def composition():
    return Composition(recipe='clean', scenes=[CScene(headline='Metro Line 4 gets the green light', background=Background(image_id='a' * 32)),
                                               CScene(kind='stat', stat='20 minutes', headline='Shorter trip to downtown'),
                                               CScene(kind='end', headline='Would you ride it?')])


def direct_request(instruction, reel=None):
    return ComposeDirectRequest(provider='claude', api_key='k', model='claude-sonnet-5-5', reel=reel or composition(),
                                instruction=instruction, source_text=ARTICLE)


def plan(*operations, message='Done.'):
    return provider(dict(message=message, operations=list(operations)))


def test_chat_edits_change_parts_with_the_same_checks():
    result = direct_composition(direct_request('make the figure count up on a big number'), plan(
        dict(op='set_layout', scene=2, layout='big_number'), dict(op='set_animation', scene=2, role='lead', animation='count_up'),
        dict(op='set_camera', scene=1, camera='pan_right'), dict(op='set_layout', scene=1, layout='big_number')))
    scenes = result['reel']['scenes']
    assert scenes[1]['layout'] == 'big_number' and scenes[1]['styles']['lead']['animation'] == 'count_up'
    assert scenes[0]['camera'] == 'pan_right' and scenes[0]['layout'] == 'light_card'
    assert any('does not suit' in r for r in result['refused'])


def test_chat_edits_keep_grounding_locks_and_recipe_rules():
    invented = direct_composition(direct_request('shorter'), plan(dict(op='edit_text', scene=1, headline='Line 4 costs 9 billion')))
    assert invented['refused'] and invented['reel']['scenes'][0]['headline'] == 'Metro Line 4 gets the green light'
    locked = composition()
    locked.scenes[0].locked = True
    assert direct_composition(direct_request('pan', locked), plan(dict(op='set_camera', scene=1, camera='pan_left')))['refused']
    recipe = plan(dict(op='set_recipe', recipe='breaking'))
    assert direct_composition(direct_request('more exciting'), recipe)['reel']['recipe'] == 'clean'
    broken = direct_composition(direct_request('use the breaking recipe'), recipe)['reel']
    assert broken['recipe'] == 'breaking' and broken['scenes'][0]['layout'] == 'news_strap'


def test_quality_fixes_are_reported_as_changes():
    result = direct_composition(direct_request('lots of energy'), plan(
        dict(op='set_camera', scene=1, camera='push_face'), dict(op='set_transition', scene=2, transition='zoom_through'),
        dict(op='set_animation', scene=2, role='headline', animation='punch_in')))
    assert any(c['field'] == 'quality' for c in result['changes'])


def test_parts_description_lists_only_what_suits():
    text = describe_parts(['quote'])
    assert 'quote_card' in text and 'big_number' not in text and 'recipes:' in text


def test_text_gets_an_emphasis_and_exit_once_it_is_in():
    answer = dict(scenes=[dict(emphasis='word_pop', exit='fade_up'), dict(emphasis='pulse'), dict(emphasis='pulse'), dict()])
    scenes = compose(request(), writer=lambda r: WRITTEN, provider=provider(answer))['composition']['scenes']
    assert scenes[0]['emphasis'] == 'word_pop' and scenes[0]['exit'] == 'fade_up'
    assert scenes[1]['emphasis'] == 'pulse'
    assert scenes[2]['emphasis'] == 'underline'  # pulse is for a figure; a quote with no highlight is underlined instead
    assert scenes[3]['emphasis'] is None     # the end card stays put
    left_out = compose(request(), writer=lambda r: WRITTEN, provider=provider({}))['composition']['scenes']
    assert left_out[0]['emphasis'] == 'highlight_sweep'  # the AI left it out: the highlight is swept in anyway


def test_chat_edits_set_emphasis_and_exit():
    result = direct_composition(direct_request('make the figure beat and the hook slide away'), plan(
        dict(op='set_emphasis', scene=2, emphasis='pulse'), dict(op='set_exit', scene=1, exit='slide_out'),
        dict(op='set_emphasis', scene=1, emphasis='pulse')))
    scenes = result['reel']['scenes']
    assert scenes[1]['emphasis'] == 'pulse' and scenes[0]['exit'] == 'slide_out' and scenes[0]['emphasis'] != 'pulse'
    assert any('does not suit' in r for r in result['refused'])



def test_a_recipe_brings_its_own_type_colour_and_music_but_no_label():
    from backend.services.reel_creator.composer import apply_recipe
    reel = apply_recipe(composition(), 'boxed')
    styles = reel.reel_styles()
    assert styles['headline'].background == '#ffffff' and styles['headline'].animation == 'left'
    drawn = reel.reel_for()
    assert drawn.accent_color == '#e11d48' and drawn.tag_text == '' and reel.music_preset == 'live_update'
    mine = reel.model_copy(update={'styles': {'headline': PartStyle(animation='rise')}})
    assert mine.reel_styles()['headline'].animation == 'rise' and mine.reel_styles()['headline'].background == '#ffffff'  # yours wins, field by field


@pytest.mark.parametrize('recipe', [r.id for r in __import__('backend.services.reel_creator.components', fromlist=['AI_RECIPES']).AI_RECIPES])
def test_every_ai_studio_recipe_draws_every_kind_of_scene(recipe):
    from backend.services.reel_creator.composer import apply_recipe
    from backend.services.reel_creator.composition import CompositionPlan
    from backend.services.reel_creator.quality import review
    reel = Composition(scenes=[CScene(headline='Metro Line 4 gets the green light', highlight='green light', detail='The council voted 7 to 2.'),
                               CScene(kind='stat', stat='20 minutes', headline='Shorter trip to downtown'),
                               CScene(kind='quote', headline='This changes how our city moves', attribution='Mayor Lina Chan'),
                               CScene(kind='end', headline='Would you ride it?')])
    fixed, _, problems = review(apply_recipe(reel, recipe))
    assert not problems and fixed.recipe == recipe
    plan = CompositionPlan(fixed, 270, {})
    frames = {plan.frame(plan.settled(i)).tobytes() for i in range(4)}
    assert len(frames) == 4


def test_ai_studio_has_its_own_recipes_beside_reel_studio_looks():
    from backend.services.reel_creator.components import AI_RECIPES, LOOK_LAYOUT, RECIPES
    assert {r.id for r in AI_RECIPES} >= {'spotlight', 'boxed', 'night_desk', 'explainer', 'pulse', 'broadsheet', 'captioned', 'dispatch', 'gentle'}
    assert set(LOOK_LAYOUT) == {r.look for r in RECIPES if r.studio == 'reel'} and LOOK_LAYOUT['clean'] == 'light_card'


def test_changing_recipe_restyles_unlocked_scenes():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from backend.routers.reel_creator import router
    app = FastAPI(); app.include_router(router)
    reel = composition()
    reel.scenes[0].locked, reel.scenes[0].background = True, Background()  # no photo to look up
    answer = TestClient(app).post('/api/reel-creator/compositions/recipe', json=dict(composition=reel.model_dump(), recipe='dispatch'))
    assert answer.status_code == 200, answer.text
    scenes = answer.json()['composition']['scenes']
    assert scenes[0]['layout'] == 'light_card' and scenes[1]['layout'] == 'big_number' and scenes[1]['exit'] == 'wipe_out'
    assert TestClient(app).post('/api/reel-creator/compositions/recipe', json=dict(composition=reel.model_dump(), recipe='nope')).status_code == 400


def test_the_ai_is_sent_a_light_form_and_its_answers_are_still_checked():
    """Gemini refuses forms with too many limits and choices; the checks happen on the answer instead."""
    import json
    from backend.services.reel_creator.composer import CompPlan, Picks, ScenePick
    from backend.services.reel_creator.vision_check import VisionPlan, usable
    for schema in (CompPlan, Picks, VisionPlan):
        text = json.dumps(schema.model_json_schema())
        assert 'maxLength' not in text and 'maxItems' not in text and '"news_strap"' not in text
    assert ScenePick.model_validate(dict(layout='made_up', camera='zoom_in')).model_dump(exclude_none=True) == dict(camera='zoom_in')
    answer, dropped = usable(dict(operations=[dict(op='set_layout', scene=1, layout='made_up'), dict(op='set_camera', scene=1, camera='still')]))
    assert dropped == 1 and answer['operations'] == [dict(op='set_camera', scene=1, camera='still')]


def test_the_voice_script_is_written_from_the_article_and_checked():
    from backend.services.reel_creator.composer import ScriptRequest, write_script
    article = 'The council approved Metro Line 4 on Tuesday by a vote of 7-2. Work starts in March and will take three years.'
    composition = Composition(scenes=[CScene(headline='Line 4 gets the green light'), CScene(headline='Work starts in March'),
                                      CScene(headline='Kept as it is', locked=True, say='Mine.')])
    seen = {}

    def provider(request, **kw):
        seen.update(kw)
        return dict(scenes=[dict(scene=1, say='  The council has approved Metro Line 4, in a vote of 7-2 on Tuesday. '),
                            dict(scene=2, say='Building starts in March and will take 12 years.'),  # a number the article lacks
                            dict(scene=3, say='Overwritten.')])
    request = ScriptRequest(model='gemini-3.8-flash', api_key='k', composition=composition, source_text=article)
    result = write_script(request, provider=provider)
    scenes = result['composition']['scenes']
    assert scenes[0]['say'] == 'The council has approved Metro Line 4, in a vote of 7-2 on Tuesday.'
    assert scenes[1]['say'] == '' and result['skipped'][0]['scene'] == 2 and 'number' in result['skipped'][0]['reason']
    assert scenes[2]['say'] == 'Mine.' and result['written'] == 1           # a locked scene is left alone
    assert [s['scene'] for s in seen['context']['scenes']] == [1, 2] and 'ARTICLE' in seen['prefix']
