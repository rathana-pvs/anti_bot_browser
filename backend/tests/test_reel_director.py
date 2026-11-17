"""AI director: catalog parity, checked edit plans, locks, grounding and the /direct route."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.routers import reel_creator as router
from backend.services.reel_creator import catalog
from backend.services.reel_creator.director import DirectRequest, direct, system_for
from backend.services.reel_creator.reel import Reel, Scene

class Answer(BaseModel):
    words: str = ''


ARTICLE = ('The city council voted 7 to 2 on Tuesday to approve a new flood relief plan covering three provinces. '
           '"This plan will reach every family that lost a home," said Mayor Lina Chan. '
           'Officials said payments will begin next week after the cabinet signs the order.')


def reel(**scene_values):
    return Reel(look='clean', scenes=[Scene(headline='Council approves flood relief plan', detail='Three provinces are covered'),
                                      Scene(headline='Payments begin next week', detail='After the cabinet signs', **scene_values),
                                      Scene(headline='What do you think of the plan?', kind='end')])


def request(instruction='Make it punchier', **values):
    return DirectRequest(provider='claude', api_key='k', model='claude-sonnet-5-5', reel=values.pop('reel', reel()),
                         instruction=instruction, source_text=ARTICLE, **values)


def provider(*plans):
    calls = []

    def answer(req, *, system, context, prefix, usage):
        calls.append(dict(system=system, context=context, prefix=prefix))
        usage.update(input=1000, output=100)
        return plans[min(len(calls), len(plans)) - 1]
    answer.calls = calls
    return answer


def test_catalog_matches_what_the_renderer_and_schema_accept():
    for name, expected in catalog.EXPECTED.items():
        assert catalog.schema_values(name) == expected, f'catalog group {name} is out of step with reel.py'
    assert all(p.use_when for p in catalog.CATALOG)
    prompt = system_for(reel())
    assert '- pop (Pop):' in prompt and '- none (Still):' in prompt


def test_plan_edits_text_and_styles_and_lists_the_changes():
    answer = provider({'message': 'Made the hook a question and slid scene 2 in.', 'operations': [
        {'op': 'edit_text', 'scene': 1, 'headline': 'Will flood relief reach three provinces?', 'highlight': 'three provinces'},
        {'op': 'style', 'scene': 2, 'entrance_role': 'headline', 'animation': 'left', 'transition': 'slide'}]})
    result = direct(request(), provider=answer)
    scenes = result['reel']['scenes']
    assert scenes[0]['headline'] == 'Will flood relief reach three provinces?' and scenes[0]['highlight'] == 'three provinces'
    assert scenes[1]['style']['headline']['animation'] == 'left' and scenes[1]['transition'] == 'slide'
    fields = {(c['scene'], c['field']) for c in result['changes']}
    assert {(1, 'headline'), (1, 'highlight'), (2, 'headline entrance'), (2, 'transition')} <= fields
    assert result['refused'] == [] and result['usage'] == {'input': 1000, 'output': 100}
    assert len(answer.calls) == 1 and answer.calls[0]['prefix'].startswith('ARTICLE') and ARTICLE in answer.calls[0]['prefix']
    assert ARTICLE not in str(answer.calls[0]['context'])  # the article is the cached prefix, sent once


def test_invented_numbers_and_quotes_are_refused_then_retried_once():
    bad = {'message': 'x', 'operations': [{'op': 'edit_text', 'scene': 2, 'headline': 'Payments of 500 dollars begin'}]}
    good = {'message': 'Shortened scene 2.', 'operations': [{'op': 'edit_text', 'scene': 2, 'headline': 'Payments start next week'}]}
    answer = provider(bad, good)
    result = direct(request(), provider=answer)
    assert len(answer.calls) == 2 and answer.calls[1]['context']['previous_attempt_refused']
    assert result['reel']['scenes'][1]['headline'] == 'Payments start next week' and result['refused'] == []
    fake_quote = {'message': 'x', 'operations': [{'op': 'edit_text', 'scene': 2, 'kind': 'quote', 'headline': 'We will rebuild everything',
                                                  'attribution': 'Mayor Lina Chan'}]}
    real_quote = {'message': 'x', 'operations': [{'op': 'edit_text', 'scene': 2, 'kind': 'quote',
                                                  'headline': 'This plan will reach every family that lost a home', 'attribution': 'Mayor Lina Chan'}]}
    assert direct(request(), provider=provider(fake_quote))['refused']
    assert direct(request(), provider=provider(real_quote))['reel']['scenes'][1]['kind'] == 'quote'


def test_refusals_that_persist_keep_the_valid_part_of_the_plan():
    plan = {'message': 'x', 'operations': [{'op': 'style', 'scene': 1, 'animation': 'fade'},
                                           {'op': 'edit_text', 'scene': 1, 'kind': 'stat', 'stat': '7-2', 'headline': 'Council votes'}]}
    result = direct(request(), provider=provider(plan))
    assert result['reel']['scenes'][0]['style']['headline']['animation'] == 'fade'
    assert result['reel']['scenes'][0]['kind'] == 'story' and any('hook' in r for r in result['refused'])


def test_locked_scenes_are_never_changed_or_shifted():
    plan = {'message': 'x', 'operations': [{'op': 'edit_text', 'scene': 2, 'headline': 'Payments start next week'},
                                           {'op': 'remove_scene', 'scene': 2}, {'op': 'move_scene', 'scene': 1, 'to': 2}]}
    result = direct(request(reel=reel(locked=True)), provider=provider(plan))
    assert result['reel']['scenes'][1]['headline'] == 'Payments begin next week' and len(result['refused']) == 3


def test_urgency_looks_only_when_the_operator_names_them():
    plan = {'message': 'x', 'operations': [{'op': 'set_reel', 'look': 'breaking'}]}
    assert direct(request('Make it more exciting'), provider=provider(plan))['reel']['look'] == 'clean'
    assert direct(request('Use the breaking look'), provider=provider(plan))['reel']['look'] == 'breaking'
    calm = {'message': 'x', 'operations': [{'op': 'set_reel', 'look': 'editorial', 'reel_motion': 'none'}]}
    assert direct(request('Calmer please'), provider=provider(calm))['reel']['look'] == 'editorial'


def test_added_scenes_go_before_the_end_card_and_respect_the_limit():
    plan = {'message': 'x', 'operations': [{'op': 'add_scene', 'after': 3, 'kind': 'stat', 'stat': '7 to 2', 'headline': 'Council vote'}]}
    scenes = direct(request(), provider=provider(plan))['reel']['scenes']
    assert [s['kind'] for s in scenes] == ['story', 'story', 'stat', 'end']
    full = Reel(look='clean', scenes=[Scene(headline=f'Scene {n} of the flood plan') for n in range(1, 9)])
    assert direct(request(reel=full), provider=provider(plan))['refused']


def test_an_unusable_answer_fails_plainly_and_keeps_the_reel():
    import pytest
    with pytest.raises(ValueError, match='could not make a valid change'):
        direct(request(), provider=provider({'operations': 'nonsense'}))


def test_direct_route(monkeypatch):
    plan = {'message': 'Done.', 'operations': [{'op': 'style', 'scene': 1, 'motion': 'zoom_in'}]}
    monkeypatch.setattr('backend.services.reel_creator.director.default_provider', provider(plan))
    app = FastAPI()
    app.include_router(router.router)
    body = request().model_dump(mode='json')
    body['api_key'] = 'k'
    response = TestClient(app).post('/api/reel-creator/direct', json=body)
    assert response.status_code == 200 and response.json()['reel']['scenes'][0]['motion'] == 'zoom_in'
    body['model'] = 'gemini-3.8-flash'
    assert TestClient(app).post('/api/reel-creator/direct', json=body).status_code == 422


def test_model_check_uses_the_director_format():
    import pytest
    from backend.services.ai import AIConfig
    from backend.services.reel_creator.director import check_model
    cfg = AIConfig(model='gemini-3.8-flash', api_key='k')
    seen = []
    ok = check_model(cfg, provider=lambda config, **kw: seen.append(kw['schema'].__name__) or {'message': 'Ready.', 'operations': []})
    assert ok['ok'] and ok['model'] == 'gemini-3.8-flash' and seen == ['EditPlan']
    with pytest.raises(ValueError, match='not in the format'):
        check_model(cfg, provider=lambda config, **kw: {'operations': 'nope'})


def test_gemini_errors_carry_googles_own_words_without_keys():
    import httpx
    import pytest
    from backend.services.reel_creator.news_ai import provider_generate
    from backend.services.ai import AIConfig

    def client(status, body):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
        return lambda **kwargs: httpx.Client(transport=transport, timeout=kwargs.get('timeout'))
    cfg = AIConfig(model='gemini-3.8-flash', api_key='AIzaSyTESTKEY0000000000000000000')
    bad_key = {'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.'}}
    with pytest.raises(ValueError, match='rejected the API key'):
        provider_generate(cfg, client(400, bad_key), system='s', schema=Answer, context={})
    schema = {'error': {'message': 'Invalid JSON payload received. Unknown name "anyOf" for key AIzaSyTESTKEY0000000000000000000'}}
    with pytest.raises(ValueError) as error:
        provider_generate(cfg, client(400, schema), system='s', schema=Answer, context={})
    assert 'Unknown name "anyOf"' in str(error.value) and 'AIza' not in str(error.value)
    cut = {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': '{"wo'}]}}]}
    with pytest.raises(ValueError, match='ran out of room'):
        provider_generate(cfg, client(200, cut), system='s', schema=Answer, context={})


def test_the_plan_schema_sent_to_providers_is_plain():
    import json
    from backend.services.reel_creator.director import EditPlan
    schema = EditPlan.model_json_schema()
    text = json.dumps(schema)
    for unsupported in ('anyOf', '$ref', '$defs', '"null"', 'minimum', 'maximum', '"default"'):
        assert unsupported not in text, unsupported
    op = schema['properties']['operations']['items']
    assert op['properties']['scene'] == {'type': 'integer'} and op['required'] == ['op'] and op['additionalProperties'] is False
    assert 'pop' in op['properties']['animation']['enum'] and op['properties']['headline']['maxLength'] == 120
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EditPlan.model_validate({'operations': [{'op': 'style', 'scene': 9}]})  # ranges are still checked on the answer
