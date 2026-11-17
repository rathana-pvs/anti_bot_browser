"""Before posting: the fact panel, the hook coach and the story shape check."""
import pytest

from backend.services.reel_creator.coach import CoachRequest, coach_hooks, fact_panel, story_check
from backend.services.reel_creator.composition import Composition, CScene

ARTICLE = ('The city council approved Metro Line 4 on Tuesday by a vote of 7-2. Mayor Lina Chan said: "This changes '
           'everything for the east side." Work starts in March and will take three years. The line will cost $1.2bn.')


def request(scenes, **extra):
    return CoachRequest(model='gemini-3.8-flash', api_key='k', composition=Composition(scenes=scenes), source_text=ARTICLE, **extra)


def test_the_fact_panel_finds_each_line_in_the_article_and_flags_what_it_lacks():
    composition = Composition(scenes=[
        CScene(headline='Council approves Metro Line 4', detail='The vote on Tuesday was 7-2'),
        CScene(kind='quote', headline='This changes everything for the east side', attribution='Mayor Lina Chan'),
        CScene(kind='stat', stat='$1.5bn', headline='What the line will cost', say='Governor Sam Ortiz backs the plan.')])
    panel = fact_panel(composition, ARTICLE)
    first = {c['field']: c for c in panel['scenes'][0]['checks']}
    assert first['headline']['status'] == 'ok' and 'approved Metro Line 4' in first['headline']['source']
    quote = {c['field']: c for c in panel['scenes'][1]['checks']}
    assert quote['headline']['status'] == 'ok' and not quote['headline']['quote_missing'] and quote['attribution']['status'] == 'ok'
    stat = {c['field']: c for c in panel['scenes'][2]['checks']}
    assert stat['stat']['missing_numbers'] == ['1.5'] and stat['stat']['status'] == 'problem'
    assert stat['voice']['missing_names'] == ['Sam Ortiz'] and stat['voice']['status'] == 'problem'
    made_up = fact_panel(Composition(scenes=[CScene(kind='quote', headline='We will finish early', attribution='Mayor Lina Chan')]), ARTICLE)
    assert made_up['scenes'][0]['checks'][0]['quote_missing']
    other_forms = fact_panel(Composition(scenes=[CScene(headline='Democratic leaders back Line 4'), CScene(kind='end', headline='Full story in the first comment')]),
                             ARTICLE + ' Democrats on the council backed it.')
    assert other_forms['scenes'][0]['checks'][0]['missing_names'] == [] and other_forms['scenes'][1]['checks'][0]['note']
    assert panel['counts']['problem'] == 2 and fact_panel(composition, '')['has_article'] is False


def test_hook_options_are_checked_and_best_first():
    def provider(req, **kw):
        assert kw['context']['current_hook'] == 'Line 4 approved' and 'ARTICLE' in kw['prefix']
        return dict(hooks=[dict(text='Line 4 approved', angle='question', score=9),                        # the current one
                           dict(text='Is the east side about to change?', angle='question', highlight='east side', score=7),
                           dict(text='A $1.2bn line, approved 7-2', angle='number', highlight='$1.2bn', score=8),
                           dict(text='A $3bn surprise for the city', angle='surprise', score=10)])              # not in the article
    result = coach_hooks(request([CScene(headline='Line 4 approved'), CScene(headline='Work starts in March')]), provider=provider)
    assert [h['text'] for h in result['hooks']] == ['A $1.2bn line, approved 7-2', 'Is the east side about to change?']
    assert result['hooks'][1]['highlight'] == 'east side'
    with pytest.raises(ValueError):
        coach_hooks(request([CScene(headline='x y z')]).model_copy(update={'source_text': ''}), provider=provider)


def test_story_suggestions_are_kept_only_when_this_reel_can_take_them():
    scenes = [CScene(headline='Line 4 approved'), CScene(headline='It costs $1.2bn'), CScene(headline='Work starts in March'),
              CScene(headline='Locked one', locked=True), CScene(kind='end', headline='What do you think?')]

    def provider(req, **kw):
        return dict(verdict='ok', summary='Good, but the cost should come later.', suggestions=[
            dict(kind='move', scene=2, to=3, problem='The cost lands before the context.'),
            dict(kind='move', scene=1, to=2, problem='Hooks move.'),            # the hook stays first
            dict(kind='move', scene=3, to=5, problem='Past the end card.'),
            dict(kind='cut', scene=4, problem='Locked.'),
            dict(kind='rewrite', scene=3, instruction='Say how long the work will take', problem='Vague.'),
            dict(kind='add', after=3, instruction='The mayor’s quote about the east side', problem='No voice from the city.'),
            dict(kind='nonsense', scene=2)])                                     # malformed: set aside, the rest kept
    result = story_check(request(scenes), provider=provider)
    assert result['verdict'] == 'ok' and [s['kind'] for s in result['suggestions']] == ['move', 'rewrite', 'add']
    assert result['suggestions'][0] == dict(kind='move', scene=2, to=3, after=None, problem='The cost lands before the context.', instruction='')


def test_coach_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import reel_creator as router
    app = FastAPI()
    app.include_router(router.router)
    body = dict(composition=Composition(scenes=[CScene(headline='Council approves Metro Line 4')]).model_dump(), source_text=ARTICLE)
    answer = TestClient(app).post('/api/reel-creator/compositions/facts', json=body).json()
    assert answer['scenes'][0]['checks'][0]['status'] == 'ok'
