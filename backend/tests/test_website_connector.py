"""Different HTTP API shapes use the same schema-driven connector and recovery journal."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from backend.services import website_service as web
from backend.services import website_connector as mapping


def config():
    operation = {'method': 'POST', 'path': '/posts', 'body': {
        'headline': '{{article.title}}', 'summary': '{{article.excerpt}}', 'body': '{{article.content}}',
        'state': 'draft', 'image': '{{article.cover_id}}', 'region': '{{article.custom_fields.region}}',
        'seo': {'title': '{{article.seo_title}}'}, 'request': '{{request_id}}'},
        'response': {'id': 'result.identifier', 'status': 'result.state', 'url': 'result.link'}}
    return {'schemaVersion': 2, 'adapter': 'http-json-v1', 'name': 'Other CMS', 'baseUrl': 'https://other.example',
        'username': 'editor', 'password': 'private-password', 'schema': {
        'authentication': {'type': 'json-login', 'login': {'method': 'POST', 'path': '/session',
            'body': {'user': '{{credentials.username}}', 'pass': '{{credentials.password}}'}}, 'tokenPath': 'auth.jwt'},
        'fields': {'title': {'required': True, 'maxLength': 90}, 'custom_fields.region': {'required': True, 'label': 'Region', 'default': 'Asia'}},
        'contentFormat': 'html', 'publishedValue': 'live', 'publicationStatusField': 'state', 'idempotentMutations': True,
        'operations': {'test': {'method': 'GET', 'path': '/me'},
            'upload': {'method': 'POST', 'path': '/assets', 'encoding': 'multipart', 'fileField': 'image', 'body': {'description': '{{article.alt}}'}, 'response': {'id': 'asset.id'}},
            'publish': operation, 'update': {**operation, 'method': 'PATCH', 'path': '/posts/{{article.id}}'},
            'verify': {'method': 'GET', 'path': '/posts/{{article.id}}', 'response': {'published': 'public'}},
            'commentLink': {'method': 'POST', 'path': '/links', 'body': {'target': '{{article.id}}', 'label': '{{label}}', 'key': '{{request_id}}'}, 'response': {'url': 'tracking.url'}},
            'catalog': {'method': 'GET', 'path': '/posts', 'response': {'articles': 'items'}}},
        'catalogArticleMapping': {'id': 'identifier', 'title': 'headline', 'content': 'body'}}}


@pytest.fixture
def http_site(monkeypatch, tmp_path):
    monkeypatch.setattr(web, 'ROOT', tmp_path / 'store')
    monkeypatch.setattr(web, 'SHARED_MEDIA_DIR', tmp_path / 'media')
    web.SHARED_MEDIA_DIR.mkdir()
    (web.SHARED_MEDIA_DIR / 'cover.jpg').write_bytes(b'test-image')
    state = {'requests': [], 'published': {}, 'links': {}, 'uploads': set(), 'lose_link': False, 'public': True}
    def handler(request):
        state['requests'].append(request)
        if request.url.path == '/session':
            assert json.loads(request.content) == {'user': 'editor', 'pass': 'private-password'}
            return httpx.Response(200, json={'auth': {'jwt': 'test-access-token'}})
        assert request.headers['Authorization'] == 'Bearer test-access-token'
        if request.method == 'GET':
            if request.url.path == '/me': return httpx.Response(200, json={'authenticated': True})
            if request.url.path == '/posts': return httpx.Response(200, json={'items': [{'identifier': 900, 'headline': 'A story', 'body': 'Full story'}]})
            return httpx.Response(200, json={'public': state['public']})
        key = request.headers['Idempotency-Key']
        if request.url.path == '/assets':
            assert b'name="image"' in request.content
            assert b'test-image' in request.content
            state['uploads'].add(key)
            return httpx.Response(200, json={'asset': {'id': 7}})
        if request.url.path.startswith('/posts'):
            body = json.loads(request.content)
            assert body['state'] == 'live'  # Schema cannot accidentally send a draft.
            assert body['region'] == 'Asia'
            assert body['image'] == 7
            assert body['body'] == '<p>First &lt;fact&gt;.</p><p>Second fact.</p>'
            state['published'].setdefault(key, {'result': {'identifier': 900, 'state': 'live', 'link': '/news/900'}})
            return httpx.Response(200, json=state['published'][key])
        if request.url.path == '/links':
            state['links'].setdefault(key, {'tracking': {'url': '/go/' + key}})
            if state['lose_link']:
                state['lose_link'] = False
                raise httpx.ReadError('response lost after commit')
            return httpx.Response(200, json=state['links'][key])
        raise AssertionError('Unexpected endpoint')
    client = httpx.Client
    monkeypatch.setattr(web.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    return state


def preparation(connection_id):
    return web.Prepare(request_id='a' * 32, connection_id=connection_id, article={
        'title': 'A story', 'excerpt': '', 'content': 'First <fact>.\n\nSecond fact.', 'cover_file': 'cover.jpg'},
        profiles=['account-a', 'account-b'], post_type='photo', media_file='cover.jpg', caption='Read more', unique_captions=False)


def test_different_api_login_upload_mapping_publication_and_unique_comments(http_site):
    saved = web.save_connection(web.Connector.model_validate(config()))
    assert saved['schema']['fields']['custom_fields.region']['default'] == 'Asia'
    assert 'private-password' not in json.dumps(saved)
    assert 'test-access-token' not in json.dumps(saved)
    request = preparation(saved['id'])
    record = web.prepare(request)
    assert record['ready'] and record['article']['url'] == 'https://other.example/news/900'
    assert len(http_site['published']) == 1 and len(http_site['uploads']) == 1 and len(http_site['links']) == 2
    assert set(http_site['uploads']).isdisjoint(http_site['published'])
    links = [destination['link'] for destination in record['destinations'].values()]
    assert len(set(links)) == 2 and all('/go/' in link for link in links)
    assert web.catalog(saved['id'])['articles'][0]['id'] == '900'
    count = len(http_site['requests'])
    assert web.prepare(request) == record
    assert len(http_site['requests']) == count
    with web.db() as db:
        journal = db.execute('SELECT payload FROM preparations').fetchone()[0]
    assert 'private-password' not in journal and 'test-access-token' not in journal


def test_generic_api_lost_response_reuses_keys(http_site):
    saved = web.save_connection(web.Connector.model_validate(config()))
    request = preparation(saved['id'])
    http_site['lose_link'] = True
    with pytest.raises(ValueError, match='reconcile'): web.prepare(request)
    first_key = next(iter(http_site['links']))
    result = web.prepare(request)
    assert result['destinations']['account-a']['link'].endswith(first_key)
    assert len(http_site['published']) == 1 and len(http_site['links']) == 2


def test_false_string_does_not_confirm_publication(http_site):
    saved = web.save_connection(web.Connector.model_validate(config()))
    http_site['public'] = 'false'
    with pytest.raises(ValueError, match='publicly available'): web.prepare(preparation(saved['id']))
    assert not http_site['links']


def test_reimport_changed_schema_cannot_resume_inflight_mutations(http_site):
    value = config()
    saved = web.save_connection(web.Connector.model_validate(value))
    http_site['lose_link'] = True
    with pytest.raises(ValueError): web.prepare(preparation(saved['id']))
    value['schema']['operations']['commentLink']['path'] = '/different-links'
    assert web.save_connection(web.Connector.model_validate(value))['id'] == saved['id']
    with pytest.raises(ValueError, match='schema changed'): web.prepare(preparation(saved['id']))


@pytest.mark.parametrize('change', ['origin', 'credentials', 'mutation', 'read-mutation', 'mapping', 'idempotency', 'version'])
def test_unsafe_or_incomplete_schema_is_rejected(change):
    value = config()
    if change == 'origin': value['schema']['operations']['publish']['path'] = '//outside.example/posts'
    if change == 'credentials': value['schema']['operations']['publish']['body']['leak'] = '{{credentials.password}}'
    if change == 'mutation': value['schema']['operations']['publish']['method'] = 'GET'
    if change == 'read-mutation': value['schema']['operations']['test']['method'] = 'POST'
    if change == 'mapping': value['schema']['operations']['publish']['response'].pop('status')
    if change == 'idempotency': value['schema']['idempotentMutations'] = False
    if change == 'version': value['schemaVersion'] = 1
    with pytest.raises(ValidationError): web.Connector.model_validate(value)


def test_templates_keep_types_encode_path_values_and_reject_unknown_fields():
    context = {'article': {'id': '../a?x=1', 'cover': 7, 'enabled': False, 'list': ['a', 'b']}}
    assert mapping.render('/posts/{{article.id}}', context, path=True) == '/posts/..%2Fa%3Fx%3D1'
    assert mapping.render({'cover': '{{article.cover}}', 'enabled': '{{article.enabled}}', 'list': '{{article.list}}'}, context) == {'cover': 7, 'enabled': False, 'list': ['a', 'b']}
    with pytest.raises(ValueError): mapping.render('{{article.missing}}', context)
    with pytest.raises(ValueError): mapping.safe_url('https://site.example', 'https://other.example/path')
    assert mapping.safe_url('https://site.example', '/story/1') == 'https://site.example/story/1'


def test_bearer_and_basic_auth_keep_secrets_out_of_public_connection(http_site):
    value = config()
    value['schema']['authentication'] = {'type': 'bearer'}
    value['api_token'] = 'test-access-token'
    value.pop('username'); value.pop('password')
    saved = web.save_connection(web.Connector.model_validate(value))
    assert 'test-access-token' not in json.dumps(saved)
    assert web.connection(saved['id'])['api_token'] == 'test-access-token'
    value['schema']['authentication'] = {'type': 'basic'}
    value.update(email='user', password='password')
    site = web.Website({**value, 'schema': web.Connector.model_validate(value).schema_definition.model_dump()})
    mapping.authenticate(site)
    assert isinstance(site.client.auth, httpx.BasicAuth)
    site.client.close()


def test_schema_required_fields_types_and_content_conversion():
    schema = web.Connector.model_validate(config()).schema_definition
    article = web.Article(title='Story', content='Text', custom_fields={'region': 99})
    with pytest.raises(ValueError, match='Region'): mapping.article_values(article, schema)
    schema.fields['custom_fields.region'].default = None
    article.custom_fields = {}
    with pytest.raises(ValueError, match='requires Region'): mapping.article_values(article, schema)


def test_legacy_preparation_fingerprint_does_not_change_for_empty_custom_fields():
    request = web.Prepare(request_id='a'*32, connection_id='b'*32, article={'title': 'Story', 'excerpt': 'Summary', 'content': 'Body'}, profiles=['a'], post_type='photo', media_file='a.jpg')
    old = request.model_dump(exclude={'api_key', 'model', 'request_id'})
    old['article'].pop('custom_fields')
    expected = hashlib.sha256(json.dumps(old, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert web.fingerprint(request) == expected


def test_generated_connection_contains_the_schema_and_validates():
    root = Path(__file__).resolve().parents[2]
    example = json.loads((root / 'docs/examples/asian-dot-connection.json').read_text())
    imported = web.Connector.model_validate(example)
    assert imported.schemaVersion == 2
    assert imported.schema_definition.operations.publish.payloadField == 'payload'
    assert imported.schema_definition.authentication.prefix == 'JWT '


def test_multipart_without_file_and_form_login_keep_declared_encodings():
    calls = []
    class Site:
        value = {'baseUrl': 'https://site.example'}
        def request(self, method, path, **kwargs):
            calls.append(kwargs)
            return {'id': 1}
    operation = mapping.Operation(method='POST', path='/posts', encoding='multipart', payloadField='payload', body={'title': '{{article.title}}'})
    mapping.operate(Site(), operation, {'article': {'title': 'Story'}, 'request_id': 'stable'})
    assert 'data' not in calls[-1]
    assert json.loads(calls[-1]['files']['payload'][1]) == {'title': 'Story'}
    operation.encoding = 'form'
    mapping.operate(Site(), operation, {'article': {'title': 'Story'}, 'request_id': 'stable'})
    assert 'files' not in calls[-1] and 'payload' in calls[-1]['data']


def test_alternative_required_fields_allow_cover_file_or_existing_id():
    value = config()
    value['schema']['requireAny'] = [['cover_file', 'cover_id']]
    schema = web.Connector.model_validate(value).schema_definition
    article = web.Article(title='Story', content='Body')
    with pytest.raises(ValueError, match='cover_file'): mapping.article_values(article, schema)
    article.cover_id = '42'
    assert mapping.article_values(article, schema)['cover_id'] == '42'
    article.cover_id = ''; article.cover_file = 'cover.jpg'
    assert mapping.article_values(article, schema)['cover_file'] == 'cover.jpg'


def test_saved_connection_can_be_tested_without_publishing(http_site):
    saved = web.save_connection(web.Connector.model_validate(config()))
    before = len(http_site['requests'])
    assert web.test_connection(saved['id']) == {'success': True}
    assert [(request.method, request.url.path) for request in http_site['requests'][before:]] == [('POST', '/session'), ('GET', '/me')]
    assert not http_site['published'] and not http_site['uploads'] and not http_site['links']
