"""Check with AI: images sent to every provider, the contact sheet, and visual fixes through the same checks."""
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from backend.services.ai import AIConfig, generate_json
from backend.services.reel_creator.composition import Background, Composition, CScene
from backend.services.reel_creator.vision_check import VisionRequest, check_with_ai, contact_sheet
from test_ai_providers import Answer


@pytest.fixture(autouse=True)
def cpu_scaling(monkeypatch):
    from backend.services.reel_creator import acceleration
    monkeypatch.setattr(acceleration, '_local', SimpleNamespace(disabled=True))


def png(color='#336699', size=(90, 160)):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, 'PNG')
    return out.getvalue()


def test_gemini_gets_the_image_inline():
    sent = []

    def gemini(**kwargs):
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"words": "ok"}'}]}}]})
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=kwargs.get('timeout'))
    generate_json(AIConfig(model='gemini-3.8-flash', api_key='k'), system='S', schema=Answer, context={}, action='t', images=[png()], client_factory=gemini)
    assert sent[0]['contents'][0]['parts'][0]['inlineData']['mimeType'] == 'image/png'


def test_image_limits():
    cfg = AIConfig(model='gemini-3.8-flash', api_key='k')
    with pytest.raises(ValueError, match='at most 4'):
        generate_json(cfg, system='S', schema=Answer, context={}, action='t', images=[png()] * 5)
    with pytest.raises(ValueError, match='PNG or JPEG'):
        generate_json(cfg, system='S', schema=Answer, context={}, action='t', images=[b'GIF89a...'])


def test_contact_sheet_is_a_numbered_grid_within_the_width():
    sheet = Image.open(io.BytesIO(contact_sheet([Image.new('RGB', (360, 640), '#123456')] * 5)))
    assert sheet.width <= 1500 and sheet.height > sheet.width // 4  # two rows of four
    corner = sheet.getpixel((14, 14))
    assert corner[0] > 200 and corner[1] > 150  # the yellow number tab


def request(composition):
    return VisionRequest(api_key='k', model='gemini-3.8-flash', composition=composition)


def test_visual_fixes_pass_the_same_checks_and_never_touch_words():
    composition = Composition(scenes=[CScene(headline='Metro Line 4 gets the green light'), CScene(kind='end', headline='Would you ride it?')])
    seen = []

    def ask(req, **kwargs):
        seen.append(kwargs)
        return dict(message='One fix.', issues=[dict(scene=1, problem='The text sits on the subject.')], operations=[
            dict(op='set_layout', scene=1, layout='magazine'), dict(op='set_layout', scene=1, layout='big_number'),
            dict(op='edit_text', scene=1, headline='Something else')])
    result = check_with_ai(request(composition), provider=ask)
    assert result['composition']['scenes'][0]['layout'] == 'magazine' and result['composition']['scenes'][0]['headline'].startswith('Metro')
    assert any('does not suit' in r for r in result['refused']) and any('look can change' in r for r in result['refused'])
    assert {n['rule'] for n in result['notes']} >= {'seen', 'changed'}
    image = seen[0]['images'][0]
    assert image[:8] == b'\x89PNG\r\n\x1a\n' and [s['scene'] for s in seen[0]['context']['scenes']] == [1, 2]


def test_text_that_cannot_fit_stops_the_check_before_any_cost():
    composition = Composition(scenes=[CScene(layout='big_number', kind='stat', stat='W' * 23, headline='Count')])
    with pytest.raises(ValueError, match='Scene 1'):
        check_with_ai(request(composition), provider=lambda *a, **k: pytest.fail('the AI must not be asked'))
