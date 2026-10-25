"""Offline template-aware authoring: no saved key, provider, or rendering jobs."""
from copy import deepcopy
import json
import httpx
import pytest
from backend.services.reel_creator.models import Composition, Photo
from backend.services.reel_creator.content_fit import check_content_fit
from backend.services.reel_creator.template_news import TemplateNewsRequest, generate_template_news, TEMPLATE_SYSTEM, TemplateCopy
from backend.services.reel_creator.news_ai import provider_generate, EditorialDraft

SOURCE=('The committee met on Tuesday to discuss the proposed budget. '
        'Members said the proposal remains under review. A final vote has not been scheduled.')
COPY=dict(hook='Budget proposal remains under review',headline='Committee discusses proposed budget',
          highlights=[dict(text='No final vote scheduled',source_quote='A final vote has not been scheduled.')],
          category='NEWS UPDATE',post_caption='The committee discussed the proposed budget on Tuesday. Members said the proposal remains under review.',
          hashtags=['#News'],source_label='Invented outlet',suggested_template='lens_report',suggested_audio='headlines',
          scenes=[dict(headline='Budget remains under review',caption='No final vote scheduled.',source_quotes=['Members said the proposal remains under review.']),
                  dict(headline='Committee discusses budget',caption='The proposal remains under review.',source_quotes=['The committee met on Tuesday to discuss the proposed budget.'])])


def composition(template='lens_report',ratio='4:5',**changes):
    return Composition(version=3 if template=='lens_report' else 2,template=template,layout_revision=2 if template=='lens_report' else 1,
                       aspect_ratio=ratio,photos=[Photo(image_id='a'*32,headline='Original first title',caption='Original first caption'),
                                                Photo(image_id='b'*32,headline='Original second title',caption='Original second caption')],
                       image_id='a'*32,duration=10,slide_seconds=5,headline='Original shared title',supporting='Original shared subtitle',
                       source='Existing source',**changes)


def request(spec=None,**changes):
    return TemplateNewsRequest(api_key='dummy-test-key',model='gemini-test',text=SOURCE,publisher='Verified Publisher',
                               composition=spec or composition(),**changes)


@pytest.mark.parametrize('template,ratio',[('lens_report','4:5'),('lens_report','9:16'),('daily_brief','9:16'),
                                           ('newsroom','4:5'),('editorial','1:1'),('brief','9:16')])
def test_generation_fits_chosen_template_without_changing_draft(template,ratio):
    req=request(composition(template,ratio));before=req.model_dump()
    result=generate_template_news(req,lambda r:deepcopy(COPY))
    assert result['fit']['fits'],result['fit']
    assert result['proposal']['template']==template and result['proposal']['aspect_ratio']==ratio
    assert result['copy']['suggested_template']==template
    assert result['proposal']['photos'][0]['headline']=='Budget remains under review'
    assert result['copy']['source_label']=='Verified Publisher'
    assert req.model_dump()==before
    assert 'api_key' not in json.dumps(result)


def test_generation_context_has_real_limits_and_no_media_or_credentials():
    req=request(tone='concise',intent='adapt')
    data=req.provider_context();encoded=json.dumps(data)
    assert data['settings']['template']=='lens_report'
    assert data['settings']['aspect_ratio']=='4:5'
    assert data['scenes'][0]['max_words']==13
    assert data['scenes'][0]['seconds']==5
    assert data['tone']=='concise' and data['intent']=='adapt'
    assert 'dummy-test-key' not in encoded and 'a'*32 not in encoded
    assert 'offset_x' not in encoded and 'logo_id' not in encoded


def test_fit_failure_retries_once_with_specific_feedback():
    calls=[]
    def provider(req):
        calls.append(req)
        result=deepcopy(COPY)
        if not req.fit_feedback:result['scenes'][0]['caption']='The committee continues reviewing the proposed budget and members have not scheduled any final vote on the proposal.'
        return result
    result=generate_template_news(request(),provider)
    assert result['fit']['fits'] and len(calls)==2
    assert 'Scene 1' in calls[1].fit_feedback[0]
    with pytest.raises(ValueError,match='still needs shortening'):
        generate_template_news(request(),lambda r:{**deepcopy(COPY),'scenes':[{**COPY['scenes'][0],'caption':'The committee continues reviewing the proposed budget and members have not scheduled any final vote on the proposal.'},COPY['scenes'][1]]})


@pytest.mark.parametrize('change',[{'scenes':[COPY['scenes'][0]]},
    {'scenes':[{**COPY['scenes'][0],'source_quotes':['This passage is absent from the supplied source.']},COPY['scenes'][1]]},
    {'scenes':[{**COPY['scenes'][0],'headline':'Committee approves 999 proposals'},COPY['scenes'][1]]}])
def test_bad_scene_count_or_ungrounded_output_is_rejected(change):
    calls=[]
    def provider(req):calls.append(req);return {**deepcopy(COPY),**change}
    with pytest.raises(ValueError):generate_template_news(request(),provider)
    assert len(calls)==1


def test_shortening_preserves_other_scenes_layout_and_manual_framing():
    spec=composition(lens_theme='dark',show_progress=False)
    req=request(spec,intent='shorten',scene_index=1)
    raw={**deepcopy(COPY),'scenes':[COPY['scenes'][1]]}
    result=generate_template_news(req,lambda r:raw)
    assert result['proposal']['photos'][0]==spec.photos[0].model_dump()
    assert result['proposal']['headline']==spec.headline
    assert result['proposal']['supporting']==spec.supporting
    assert result['proposal']['lens_theme']=='dark' and result['proposal']['show_progress'] is False
    assert len(req.provider_context()['scenes'])==1


def test_settings_and_local_fit_checks_never_call_provider():
    spec=composition('daily_brief','4:5')
    assert any(i['kind']=='settings' for i in check_content_fit(spec)['issues'])
    with pytest.raises(ValueError,match='9:16'):generate_template_news(request(spec),lambda r:pytest.fail('Must not call provider'))
    bad=composition();bad.photos[0].caption='word '*39
    fit=check_content_fit(bad)
    assert not fit['fits'] and {i['kind'] for i in fit['issues']}>={'layout','reading'}


def test_hidden_captions_and_generation_before_photo_import():
    spec=Composition(version=3,template='lens_report',layout_revision=2,show_subtitle=False,duration=10)
    raw={**deepcopy(COPY),'scenes':[COPY['scenes'][0]]}
    result=generate_template_news(request(spec),lambda r:raw)
    assert result['proposal']['headline']==COPY['scenes'][0]['headline']
    assert result['proposal']['supporting']=='' and not result['proposal']['photos']
    assert result['fit']['fits']


def test_structured_provider_uses_template_prompt_schema_and_sanitized_context():
    def handler(req):
        data=json.loads(req.content)
        assert data['systemInstruction']['parts'][0]['text']==TEMPLATE_SYSTEM
        context=json.loads(data['contents'][0]['parts'][0]['text'])
        assert context['settings']['aspect_ratio']=='4:5' and len(context['scenes'])==2
        assert 'api_key' not in context and 'composition' not in context
        assert data['generationConfig']['responseJsonSchema']['properties']['scenes']['minItems']==1
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(COPY)}]}}]})
    factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw)
    raw=provider_generate(request(),factory,system=TEMPLATE_SYSTEM,schema=TemplateCopy)
    assert raw==COPY


def test_review_metadata_and_scene_evidence_survive_draft_save_schema():
    draft=EditorialDraft(generated=TemplateCopy(**COPY),applied_settings='["lens_report","4:5"]',tone='explainer')
    restored=EditorialDraft.model_validate_json(draft.model_dump_json())
    assert restored.tone=='explainer' and restored.generated.scenes[1].source_quotes==COPY['scenes'][1]['source_quotes']


def test_local_routes_check_fit_and_return_reviewable_proposal(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import reel_creator
    from backend.services.reel_creator import template_news
    app=FastAPI();app.include_router(reel_creator.router)
    calls=[]
    def provider(req,**kwargs):calls.append(req);return deepcopy(COPY)
    monkeypatch.setattr(template_news,'provider_generate',provider)
    client=TestClient(app)
    checked=client.post('/api/reel-creator/content-fit',json=composition().model_dump())
    assert checked.status_code==200 and not calls
    req=request();body={**req.model_dump(mode='json'),'api_key':'dummy-test-key'}
    generated=client.post('/api/reel-creator/generate-template-news',json=body)
    assert generated.status_code==200,generated.text
    assert generated.json()['proposal']['aspect_ratio']=='4:5' and generated.json()['fit']['fits']
    assert len(calls)==1 and 'dummy-test-key' not in generated.text


def test_generating_without_visible_captions_preserves_hidden_manual_copy():
    spec=composition(show_subtitle=False)
    result=generate_template_news(request(spec),lambda r:deepcopy(COPY))
    assert result['proposal']['supporting']==spec.supporting
    assert result['proposal']['photos'][0]['caption']==spec.photos[0].caption
    assert result['copy']['scenes'][0]['caption']==''


def test_template_generation_repairs_hashtags_without_extra_provider_call():
    calls=[]
    def provider(req):
        calls.append(req)
        return {**deepcopy(COPY),'hashtags':['News','#US Politics','#bad😊']}
    result=generate_template_news(request(),provider)
    assert len(calls)==1
    assert result['copy']['hashtags']==['#News','#US_Politics']
    assert result['warnings']
    assert result['fit']['fits']


def test_flexible_daily_auto_timing_includes_the_shared_subtitle():
    from backend.services.reel_creator.photo_timing import photo_seconds
    spec=composition('daily_brief','4:5',photo_layout='full_bleed')
    spec.supporting='The committee continues reviewing proposals and no vote is scheduled.'
    spec.photos=[photo.model_copy(update={'headline':'Committee reviews proposal'}) for photo in spec.photos]
    assert photo_seconds(spec)==[5,5]
    short=spec.model_copy(update={'duration':6,'auto_timing':False,'slide_seconds':3})
    fit=check_content_fit(short)
    assert not fit['fits']
    assert fit['scenes'][0]['recommended_seconds']==5
    repaired=Composition.model_validate({**short.model_dump(),'duration':10,'auto_timing':False,'slide_seconds':None})
    assert check_content_fit(repaired)['fits']
    assert repaired.supporting==short.supporting


def test_slight_reading_overage_is_trimmed_on_final_attempt():
    def provider(req):
        raw = deepcopy(COPY)
        # In lens_report with 5s hold: max_words = 13.
        # Headline 'Budget remains under review' is 4 words.
        # Caption 'No final vote scheduled by the committee members.' is 8 words. Total = 12 (fits).
        # But if caption is 11 words: Total = 15 words (2 words over max_words 13).
        raw['scenes'][0]['caption'] = 'No final vote scheduled by the committee members for next week.'
        return raw
    result = generate_template_news(request(), provider)
    assert result['fit']['fits']
