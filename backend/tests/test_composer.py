"""AI composer: scenario-driven part choices, recipe rules, quality review and chat edits on compositions."""
from types import SimpleNamespace

import pytest

from backend.services.reel_creator.composer import ComposeDirectRequest, ComposeRequest, compose, describe_parts, direct_composition
from backend.services.reel_creator.composition import Background, Composition, CScene

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
    assert [s['layout'] for s in scenes] == ['split', 'big_number', 'quote_card', 'light_card']
    assert scenes[0]['styles']['headline']['animation'] == 'words' and scenes[0]['camera'] == 'pan_left'
    assert scenes[1]['styles']['lead']['animation'] == 'count_up'
    assert scenes[2]['transition'] == 'wipe' and scenes[0]['transition'] is None
    assert result['composition']['pacing'] == 'fast' and result['recipe'] == 'clean' and result['caption'] == 'A caption.'
    assert result['usage'] == {'input': 900, 'output': 120}
    facts = ask.seen[0]['context']['scenes']
    assert [f['kind'] for f in facts] == ['story', 'stat', 'quote', 'end'] and ask.seen[0]['context']['scenario'].startswith('calm')
    assert 'count_up' in ask.seen[0]['system'] and 'big_number' in ask.seen[0]['system']


def test_urgent_recipes_need_the_operator_to_ask():
    loud = dict(recipe='breaking', scenes=[])
    assert compose(request(scenario='make it exciting'), writer=lambda r: WRITTEN, provider=provider(loud))['recipe'] == 'clean'
    assert compose(request(scenario='a breaking update'), writer=lambda r: WRITTEN, provider=provider(loud))['recipe'] == 'breaking'
    assert compose(request(recipe='editorial'), writer=lambda r: WRITTEN, provider=provider(loud))['recipe'] == 'editorial'


def test_photos_go_to_scenes_in_order_with_their_face_focus():
    assets = {'a' * 32: dict(width=900, height=1600, focus=dict(x=.3, y=.2, faces=1))}
    ask = provider(dict(scenes=[]))
    result = compose(request(photos=['a' * 32]), assets=assets, writer=lambda r: WRITTEN, provider=ask)
    first = result['composition']['scenes'][0]['background']
    assert first['image_id'] == 'a' * 32 and (first['focal_x'], first['focal_y']) == (.3, .2)
    assert ask.seen[0]['context']['scenes'][0]['has_face'] is True


def test_an_unusable_choice_keeps_the_recipe_look():
    result = compose(request(), writer=lambda r: WRITTEN, provider=provider({'scenes': 'nonsense'}))
    assert result['recipe'] == 'clean' and {s['layout'] for s in result['composition']['scenes']} == {'light_card'}


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
