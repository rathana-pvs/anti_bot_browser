"""Offline article/Gemini checks; never read saved keys or contact a provider."""
import json
import socket
import sqlite3
import wave

import httpx
import pytest
from pydantic import ValidationError

from backend.services.reel_creator import articles, news_ai
from backend.services.reel_creator.audio_presets import PRESETS, preset_path
from backend.services.reel_creator.models import Composition
from backend.services.reel_creator.store import Store, Conflict

SOURCE = ('The committee met on Tuesday to discuss the proposed budget. '
          'Members said the proposal remains under review. A final vote has not been scheduled.')
COPY = dict(hook='Budget proposal remains under review', headline='Committee discusses proposed budget',
            highlights=[dict(text='No final vote scheduled', source_quote='A final vote has not been scheduled.')],
            category='US POLITICS', post_caption='The committee discussed the proposed budget on Tuesday. '
            'Members said the proposal remains under review. A final vote has not been scheduled.',
            hashtags=['#News'], source_label='Invented Outlet', suggested_template='newsroom', suggested_audio='politics')


def request(**changes):
    return news_ai.NewsRequest(api_key='dummy-test-key', model='gemini-test', text=SOURCE, **changes)


def factory(handler):
    return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://user:pass@example.com/story',
                               'https://example.com:8443/a', 'http://[fe80::1%25eth0]/'])
def test_invalid_article_urls(url):
    with pytest.raises(ValueError):
        articles.public_url(url)


@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['::1'], ['::ffff:127.0.0.1'],
                                     ['93.184.216.34', '192.168.1.1'], ['169.254.169.254']])
def test_dns_rejects_internal_and_mixed_addresses(monkeypatch, addresses):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in addresses])
    with pytest.raises(ValueError, match='public'):
        articles.public_addresses('example.com', 443)


def test_pinned_address_and_redirect_revalidation():
    seen = []
    def resolver(host, port):
        seen.append(host)
        if host == 'internal.example':
            raise ValueError('private redirect blocked')
        return ['93.184.216.34']
    def handler(req):
        assert req.url.host == '93.184.216.34'
        assert req.headers['host'] == 'news.example'
        assert req.extensions['sni_hostname'] == 'news.example'
        return httpx.Response(302, headers={'location': 'https://internal.example/private'})
    with pytest.raises(ValueError, match='private redirect'):
        articles.download_article('https://news.example/story', factory(handler), resolver)
    assert seen == ['news.example', 'internal.example']


@pytest.mark.parametrize('headers,body', [({'content-type': 'application/pdf'}, b'%PDF'),
    ({'content-type': 'text/html', 'content-encoding': 'br'}, b'compressed'),
    ({'content-type': 'text/html'}, b'x' * (articles.MAX_HTML + 1))])
def test_article_response_bounds(headers, body):
    with pytest.raises(ValueError):
        articles.download_article('https://news.example/story',
            factory(lambda req: httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))),
            lambda *args: ['93.184.216.34'])


def test_extract_article_body_and_paste_fallback():
    html = ('<html><head><title>Budget review</title></head><body><nav>Subscribe Login</nav>'
            '<article><h1>Budget review</h1><p>' + SOURCE + '</p><p>'
            'The discussion continued as committee members considered the next steps in the process.'
            '</p></article></body></html>').encode()
    result = articles.extract_article('https://news.example/story', lambda u: (html, u))
    assert SOURCE in result['text'] and 'Subscribe Login' not in result['text']
    assert result['url'] == 'https://news.example/story'
    assert result['title'] == 'Budget review'
    assert result['publisher'] == 'news.example'
    with pytest.raises(ValueError, match='Paste'):
        articles.extract_article('https://news.example/story', lambda u: (b'<html>Login</html>', u))


def test_generation_grounds_quotes_attributes_and_adds_source():
    req = request(source_url='https://news.example/story', publisher='Supplied Publisher')
    result = news_ai.generate_news(req, lambda r: COPY)
    assert result['copy']['source_label'] == 'Supplied Publisher'
    assert result['post_caption'].endswith('#News\n\nSource: https://news.example/story')
    assert result['source_fingerprint'] == news_ai.source_fingerprint(SOURCE, req.source_url, 'English')
    assert 'api_key' not in json.dumps(result)
    assert result['copy']['highlights'][0]['source_quote'] in SOURCE


@pytest.mark.parametrize('change', [
    {'highlights': [dict(text='New claim', source_quote='This quote does not exist in the source.')]},
    {'hook': 'The committee approved 500 proposals'}, {'post_caption': 'Read this story at https://fake.example/story'},
    {'highlights': []}])
def test_generated_unsupported_content_is_rejected(change):
    with pytest.raises(ValueError):
        news_ai.generate_news(request(), lambda r: {**COPY, **change})


def test_breaking_label_requires_operator_choice():
    assert news_ai.validate_copy({**COPY, 'category': 'BREAKING NEWS'}, request()).category == 'NEWS UPDATE'
    assert news_ai.validate_copy({**COPY, 'category': 'BREAKING NEWS'}, request(category_hint='BREAKING NEWS')).category == 'BREAKING NEWS'


def test_provider_uses_saved_model_structured_json_and_header_key():
    def handler(req):
        assert req.url.path.endswith('/gemini-test:generateContent')
        assert req.headers['x-goog-api-key'] == 'dummy-test-key'
        payload = json.loads(req.content)
        assert payload['generationConfig']['responseMimeType'] == 'application/json'
        assert 'responseJsonSchema' in payload['generationConfig']
        assert 'dummy-test-key' not in req.content.decode()
        assert SOURCE in payload['contents'][0]['parts'][0]['text']
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'thought': True, 'text': 'not output'}, {'text': json.dumps(COPY)}]}}]})
    assert news_ai.provider_generate(request(), factory(handler)) == COPY


@pytest.mark.parametrize('status,body', [(403, {'error': 'dummy-test-key secret'}),
                                       (200, []), (200, {'candidates': []}),
                                       (200, {'candidates': [{'finishReason': 'MAX_TOKENS'}]})])
def test_provider_errors_do_not_expose_response_or_key(status, body):
    with pytest.raises(ValueError) as error:
        news_ai.provider_generate(request(), factory(lambda req: httpx.Response(status, json=body)))
    assert 'dummy-test-key' not in str(error.value) and 'secret' not in str(error.value)


def test_legacy_draft_migrates_without_music_and_preserves_editorial(tmp_path):
    db = sqlite3.connect(tmp_path / 'creator.sqlite3')
    db.execute('CREATE TABLE drafts(id TEXT PRIMARY KEY,revision INTEGER,composition TEXT,updated REAL)')
    db.execute('INSERT INTO drafts VALUES(?,?,?,?)', ('legacy', 1, '{}', 0))
    db.commit(); db.close()
    store = Store(tmp_path)
    draft = store.drafts()[0]
    assert draft['editorial'] == {} and Composition(**draft['composition']).audio_preset == 'none'
    store.save_draft('legacy', 1, {}, {'text': SOURCE, 'post_caption': 'Reviewed caption'})
    store.save_draft('legacy', 2, {})
    assert store.drafts()[0]['editorial']['post_caption'] == 'Reviewed caption'
    with pytest.raises(Conflict):
        store.save_draft('legacy', 1, {}, {})
    with pytest.raises(ValidationError):
        news_ai.EditorialDraft(api_key='must never persist')


def test_caption_is_snapshotted_in_render_job(tmp_path):
    store = Store(tmp_path)
    job = store.create_job('req', 'fp', {}, 'final', 'Reviewed caption')
    assert store.job(job['id'])['post_caption'] == 'Reviewed caption'
    assert store.create_job('req', 'fp', {}, 'final', 'Ignored duplicate')['post_caption'] == 'Reviewed caption'


def test_bundled_news_audio_is_real_pcm_and_path_limited():
    import array
    import hashlib
    hashes = set()
    assert len(PRESETS) == 9
    for preset in PRESETS:
        assert Composition(audio_preset=preset).audio_preset == preset
        with wave.open(str(preset_path(preset))) as audio:
            assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
            assert audio.getnframes() / audio.getframerate() == PRESETS[preset]['duration']
            data = audio.readframes(audio.getnframes())
            samples = array.array('h', data)
            assert 1000 < max(abs(n) for n in samples) < 32767
            # Each recording fades to silence and has its own actual waveform.
            assert samples[0] == 0 and abs(samples[-1]) < 50
            hashes.add(hashlib.sha256(data).hexdigest())
    assert len(hashes) == len(PRESETS)
    with pytest.raises(ValueError):
        preset_path('../../private')


def test_article_gzip_support_and_decompression_size_limit():
    import gzip
    def fetch(body):
        return articles.download_article('https://news.example/story',
            factory(lambda req: httpx.Response(200, headers={'content-type': 'text/html', 'content-encoding': 'gzip'},
                                               stream=httpx.ByteStream(gzip.compress(body)))),
            lambda *args: ['93.184.216.34'])
    html = b'<html><article>Example public news text</article></html>'
    assert fetch(html)[0] == html
    with pytest.raises(ValueError, match='too large'):
        fetch(b'x' * (articles.MAX_HTML + 1))


def test_khmer_hashtag_combining_marks_are_supported():
    result = news_ai.validate_copy({**COPY, 'hashtags': ['#ព័ត៌មាន', '#US_Politics']}, request())
    assert result.hashtags == ['#ព័ត៌មាន', '#US_Politics']


def test_generated_copy_is_checked_against_video_geometry_before_returning():
    # Fits the JSON character limit but cannot fit a single rendered word.
    bad = {**COPY, 'headline': 'W' * 140}
    with pytest.raises(ValueError, match='readable size'):
        news_ai.generate_news(request(), lambda r: bad)


@pytest.mark.parametrize('version', [1, 2])
def test_three_line_headline_and_two_highlights_fit_as_one_block(tmp_path, version):
    from PIL import Image
    from backend.services.reel_creator.composition import fit_news_text, make_layers
    from backend.services.reel_creator.story import overlays
    spec = Composition(version=version, category='NEWS UPDATE',
        headline='Committee members discuss the proposed budget and review plans for the coming year',
        supporting='Members review the proposed budget\nA final vote has not been scheduled')
    lines, size, supporting, subtitle_size = fit_news_text(spec)
    assert len(lines) == 3 and len(supporting) == 2
    assert size < 66  # This previously fit each field but overflowed the combined block.
    assert 1430 + len(lines)*round(size*1.55) + 10 + len(supporting)*round(subtitle_size*1.55) <= 1800
    assert ''.join(lines).replace(' ', '') == spec.headline.replace(' ', '')
    image=tmp_path/'photo.png'; Image.new('RGB',(600,800),'blue').save(image)
    if version == 1:
        make_layers(spec,image)
    else:
        assert any(name=='highlight1' for name,_ in overlays(spec))


def test_cosmetic_hashtag_errors_do_not_discard_news_copy():
    raw=['News',' #US Politics ', '#Daily-Update!', '#NEWS', '#bad😊', 'https://example.com', '#', '#Valid']
    result=news_ai.generate_news(request(),lambda r:{**COPY,'hashtags':raw})
    assert result['copy']['hashtags']==['#News','#US_Politics','#Daily_Update','#Valid']
    assert result['copy']['hook']==COPY['hook']
    assert '😊' not in result['post_caption']
    assert news_ai.normalize_hashtags('#News #Politics')==['#News','#Politics']
    assert news_ai.normalize_hashtags(['#One','#Two','#Three','#Four','#Five','#Six'])==['#One','#Two','#Three','#Four','#Five']
