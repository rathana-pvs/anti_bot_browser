import asyncio
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from backend.services import website_service as web

HOOKS = [
    'Local residents discuss the proposed park changes as officials explain what comes next for the neighborhood.',
    'The council outlines its park proposal, giving neighbors a closer look at plans for shared public space.',
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'ROOT', tmp_path / 'websites')
    monkeypatch.setattr(web, 'SHARED_MEDIA_DIR', tmp_path / 'media')
    web.SHARED_MEDIA_DIR.mkdir()


def request(**changes):
    return web.Prepare(**{ 'request_id': 'a' * 32, 'connection_id': 'b' * 32,
        'article': {'title': 'Council considers park changes', 'excerpt': 'Residents review a new park proposal.',
                    'content': 'Local residents discuss the council park proposal for shared public space and neighborhood changes.', 'cover_id': '1'},
        'profiles': ['page-a', 'page-b'], 'post_type': 'reel', 'media_file': 'video.mp4',
        'caption': 'Original caption', 'unique_captions': False, 'api_key': 'private-provider-key', **changes })


class FakeWebsite:
    values = {}
    calls = []
    lose_link = False
    published = True

    def __init__(self, value):
        self.value = value

    def __enter__(self): return self
    def __exit__(self, *_): pass

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == 'GET': return {'published': self.published}
        if 'data' in kwargs:
            payload = json.loads(kwargs['data']['payload'])
            assert payload['action'] == 'publish'
            return {'id': '123', 'status': 'published', 'url': 'https://news.example/article/park'}
        payload = kwargs['json']
        key = payload['requestId']
        self.values.setdefault(key, {'url': 'https://news.example/article/' + key + '/park'})
        if self.lose_link:
            type(self).lose_link = False
            raise ValueError('Connection dropped after remote commit')
        return self.values[key]


def site(monkeypatch):
    FakeWebsite.values, FakeWebsite.calls = {}, []
    FakeWebsite.lose_link, FakeWebsite.published = False, True
    monkeypatch.setattr(web, 'Website', FakeWebsite)
    monkeypatch.setattr(web, 'connection', lambda _: {'baseUrl': 'https://news.example', 'password': 'private-site-password'})


def test_connection_validation_and_secret_separation(monkeypatch):
    class ConnectionWebsite(FakeWebsite):
        def request(self, *args, **kwargs): return {'adapter': 'payload-news-v1'}
    monkeypatch.setattr(web, 'Website', ConnectionWebsite)
    saved = web.save_connection(web.Connector(name='News', baseUrl='https://news.example', username='editor@example.com', password='private-site-password'))
    assert 'password' not in json.dumps(saved)
    assert 'editor@example.com' not in json.dumps(web.connections())
    assert web.connection(saved['id'])['password'] == 'private-site-password'
    assert web.ROOT.stat().st_mode & 0o777 == 0o700
    assert (web.ROOT / 'websites.sqlite3').stat().st_mode & 0o777 == 0o600
    for url in ['http://news.example', 'https://user:password@news.example', 'https://news.example/path', 'https://news.example?x=1']:
        with pytest.raises(ValueError): web.base_url(url)
    assert web.base_url('http://localhost:3000/') == 'http://localhost:3000'


def test_each_destination_has_own_link_and_durable_snapshot(monkeypatch):
    site(monkeypatch)
    prepared = web.prepare(request())
    assert prepared['ready']
    assert prepared['destinations']['page-a']['link'] != prepared['destinations']['page-b']['link']
    calls = len(FakeWebsite.calls)
    assert web.prepare(request()) == prepared
    assert len(FakeWebsite.calls) == calls
    with web.db() as db:
        journal = db.execute('SELECT payload FROM preparations').fetchone()[0]
    assert 'private-provider-key' not in journal and 'private-site-password' not in journal
    monkeypatch.setattr(web, 'Website', Mock(side_effect=AssertionError('No execution-time HTTP')))
    post = web.snapshot_post({'type': 'reel', 'media_file': 'video.mp4', 'base_caption': 'Original caption',
                             'read_more': {'preparation_id': prepared['id']}}, ['page-b', 'page-a'])
    assert post['ai_spin'] is False
    assert web.execution_variant(post, 'page-b')['first_comment'] == prepared['destinations']['page-b']['link']


def test_lost_link_response_reconciles_same_remote_key(monkeypatch):
    site(monkeypatch)
    FakeWebsite.lose_link = True
    with pytest.raises(ValueError, match='Connection dropped'): web.prepare(request())
    first_key = next(iter(FakeWebsite.values))
    prepared = web.prepare(request())
    assert prepared['destinations']['page-a']['link'] == FakeWebsite.values[first_key]['url']
    assert len(FakeWebsite.values) == 2
    assert sum('data' in args for _, _, args in FakeWebsite.calls) == 1


def test_unpublished_article_blocks_all_link_creation(monkeypatch):
    site(monkeypatch)
    FakeWebsite.published = False
    with pytest.raises(ValueError, match='publicly available'): web.prepare(request())
    assert not FakeWebsite.values
    record, _ = web.read_preparation('a' * 32)
    assert not record['ready']


def test_stale_context_or_profile_change_is_rejected(monkeypatch):
    site(monkeypatch)
    web.prepare(request())
    with pytest.raises(ValueError, match='changed'): web.prepare(request(caption='New caption'))
    base = {'type': 'reel', 'media_file': 'video.mp4', 'base_caption': 'Original caption', 'read_more': {'preparation_id': 'a' * 32}}
    for patch, targets in [({'media_file': 'new.mp4'}, ['page-a', 'page-b']), ({}, ['page-a']), ({'type': 'photo'}, ['page-a', 'page-b'])]:
        with pytest.raises(ValueError, match='changed'): web.snapshot_post({**base, **patch}, targets)


def test_live_variants_use_live_comment_and_reviewed_hooks(monkeypatch):
    site(monkeypatch)
    monkeypatch.setattr(web, 'generate_hooks', lambda _: HOOKS)
    record = web.prepare(request(post_type='live', unique_captions=True))
    post = web.snapshot_post({'type': 'live', 'media_file': 'video.mp4', 'base_caption': 'Original caption',
        'read_more': {'preparation_id': record['id'], 'captions': {'page-a': HOOKS[0], 'page-b': HOOKS[1]}}}, ['page-a', 'page-b'])
    assert post['end_after_comment']
    a, b = [web.execution_variant(post, profile) for profile in ['page-a', 'page-b']]
    assert a['spun_caption'] != b['spun_caption']
    assert a['first_comment'] is None and a['pinned_comment'] != b['pinned_comment']
    with pytest.raises(ValueError, match='10–30'):
        web.snapshot_post({**post, 'read_more': {'preparation_id': record['id'], 'captions': {'page-a': 'Too short'}}}, ['page-a', 'page-b'])


def test_hook_generation_checks_length_duplicates_and_new_numbers():
    provider = Mock(return_value={'hooks': ['Too short', HOOKS[0], HOOKS[0], 'There are 999 new parks opening in the neighborhood this coming weekend.', HOOKS[1]]})
    req = request(unique_captions=True)
    assert web.generate_hooks(req, provider) == HOOKS
    context = req.provider_context()
    assert 'api_key' not in context and 'private-provider-key' not in json.dumps(context)
    assert provider.call_count == 1


def test_invalid_cover_never_uploads_local_files(monkeypatch):
    site(monkeypatch)
    value = request()
    value.article.cover_file = '../private.jpg'
    with pytest.raises(ValueError, match='shared media'): web.prepare(value)
    assert not FakeWebsite.calls


def test_batch_snapshot_preserves_destinations_and_deduplicates_submission(monkeypatch, tmp_path):
    from backend.routers import queue as router
    site(monkeypatch)
    prepared = web.prepare(request())
    state = {'daily_batches': []}
    monkeypatch.setattr(router, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(router, 'load_posting_queue', lambda: deepcopy(state))
    def save(value):
        state.update(deepcopy(value))
        return True
    monkeypatch.setattr(router, 'save_posting_queue', save)
    monkeypatch.setattr(router.android_jobs, 'phone_of', lambda _: None)
    payload = {'name': 'Read more batch', 'target_profiles': ['page-a', 'page-b'], 'start_now': False,
        'schedule_window': {'start_time': '09:00', 'end_time': '21:00'}, 'posts': [
        {'type': 'reel', 'media_file': 'video.mp4', 'base_caption': 'Original caption',
         'read_more': {'preparation_id': prepared['id']}}]}
    result = asyncio.run(router.create_batch(payload))
    assert result['total_executions'] == 2
    executions = state['daily_batches'][0]['posts'][0]['executions']
    for execution in executions:
        assert execution['first_comment'] == prepared['destinations'][execution['profile_id']]['link']
        assert execution['spun_caption'] == 'Original caption'
    repeated = asyncio.run(router.create_batch(payload))
    assert repeated['batch_id'] == result['batch_id']
    assert len(state['daily_batches']) == 1
    assert [item['first_comment'] for item in router.get_queue()['executions']] == [item['first_comment'] for item in sorted(executions, key=lambda item: item['scheduled_at'])]


def test_regenerating_hook_keeps_website_article_and_links(monkeypatch):
    site(monkeypatch)
    monkeypatch.setattr(web, 'generate_hooks', lambda _: HOOKS)
    prepared = web.prepare(request(unique_captions=True))
    calls = len(FakeWebsite.calls)
    observed = []
    def generate(context):
        observed.extend(context._previous)
        return ['Neighbors examine the council proposal and consider how the changes could shape their local park.']
    monkeypatch.setattr(web, 'generate_hooks', generate)
    result = web.regenerate_hook(web.RegenerateHook(preparation_id=prepared['id'], profile_id='page-a', other_captions=HOOKS))
    assert 10 <= len(result['caption'].split()) <= 30
    assert observed == HOOKS
    assert len(FakeWebsite.calls) == calls
    assert web.read_preparation(prepared['id'])[0]['destinations'] == prepared['destinations']


def test_prompt_reads_what_the_number_check_reads_and_only_recent_hooks():
    far_number = 'x' * 4100 + ' 777 homes'
    req = request(unique_captions=True, article={'title': 'Council considers park changes', 'content': far_number, 'cover_id': '1'})
    req._previous = [f'hook {index}' for index in range(100)]
    context = req.provider_context()
    assert '777' not in context['article'] and len(context['article']) == 4000
    assert context['previous_hooks'] == req._previous[-30:]


def test_retry_asks_only_for_the_hooks_still_missing():
    wanted = []
    def provider(value, **_):
        wanted.append(len(value.provider_context()['profiles']))
        return {'hooks': HOOKS[len(wanted) - 1:len(wanted)]}
    assert web.generate_hooks(request(unique_captions=True), provider) == HOOKS
    assert wanted == [2, 1]


def test_adding_an_account_only_writes_that_accounts_hook(monkeypatch):
    site(monkeypatch)
    asked = []
    def generate(value):
        asked.append((value.profiles, value._previous))
        return [HOOKS[1]]
    monkeypatch.setattr(web, 'generate_hooks', generate)
    prepared = web.prepare(request(unique_captions=True, known_captions={'page-a': HOOKS[0]}))
    assert asked == [(['page-b'], [HOOKS[0]])]
    assert prepared['destinations']['page-a']['caption'] == HOOKS[0]
    assert prepared['destinations']['page-b']['caption'] == HOOKS[1]


def test_all_known_captions_make_no_ai_request(monkeypatch):
    site(monkeypatch)
    monkeypatch.setattr(web, 'generate_hooks', Mock(side_effect=AssertionError('No AI needed')))
    prepared = web.prepare(request(unique_captions=True, known_captions={'page-a': HOOKS[0], 'page-b': HOOKS[1]}))
    assert [prepared['destinations'][name]['caption'] for name in ('page-a', 'page-b')] == HOOKS


def test_supplied_caption_only_matters_without_unique_captions(monkeypatch):
    site(monkeypatch)
    monkeypatch.setattr(web, 'generate_hooks', lambda _: HOOKS)
    assert web.fingerprint(request(unique_captions=True)) == web.fingerprint(request(unique_captions=True, known_captions={'page-a': HOOKS[0]}))
    record = web.prepare(request(unique_captions=True))
    post = web.snapshot_post({'type': 'reel', 'media_file': 'video.mp4', 'base_caption': 'Edited after preparing',
                              'read_more': {'preparation_id': record['id']}}, ['page-a', 'page-b'])
    assert web.execution_variant(post, 'page-a')['spun_caption'] == HOOKS[0]


def captions_request(**changes):
    return web.PrepareCaptions(**{'caption': 'Local residents discuss the council park proposal for shared public space.',
                                  'profiles': ['page-a', 'page-b'], 'post_type': 'reel', 'media_file': 'video.mp4', **changes})


def test_captions_alone_never_contact_a_website(monkeypatch):
    monkeypatch.setattr(web, 'Website', Mock(side_effect=AssertionError('No website')))
    monkeypatch.setattr(web, 'connection', Mock(side_effect=AssertionError('No connection')))
    monkeypatch.setattr(web, 'generate_hooks', lambda value: HOOKS[:len(value.profiles)])
    record = web.prepare_captions(captions_request())
    assert record['ready'] and 'article' not in record
    assert [record['destinations'][name] for name in ('page-a', 'page-b')] == [{'caption': HOOK, 'link': ''} for HOOK in HOOKS]
    assert web.read_preparation(record['id'])[0]['captions'] == HOOKS


def test_captions_prefer_the_article_and_need_some_text(monkeypatch):
    seen = []
    def generate(value):
        seen.append(value.article.content)
        return HOOKS
    monkeypatch.setattr(web, 'generate_hooks', generate)
    web.prepare_captions(captions_request(title='Park', content='The full article text about the park.'))
    assert seen == ['The full article text about the park.']
    with pytest.raises(ValueError, match='caption or article'):
        web.prepare_captions(captions_request(caption='  '))


def test_captions_alone_keep_known_captions_without_asking_the_ai(monkeypatch):
    monkeypatch.setattr(web, 'generate_hooks', Mock(side_effect=AssertionError('No AI needed')))
    record = web.prepare_captions(captions_request(known_captions={'page-a': HOOKS[0], 'page-b': HOOKS[1]}))
    assert record['captions'] == HOOKS


def test_captions_alone_reach_the_queue_with_the_posts_own_comment(monkeypatch):
    monkeypatch.setattr(web, 'generate_hooks', lambda _: HOOKS)
    record = web.prepare_captions(captions_request())
    base = {'type': 'reel', 'media_file': 'video.mp4', 'base_caption': 'Edited later', 'first_comment': 'https://own.example/link',
            'read_more': {'preparation_id': record['id']}}
    post = web.snapshot_post(base, ['page-a', 'page-b'])
    assert post['ai_spin'] is False and post['first_comment'] == 'https://own.example/link'
    assert web.execution_variant(post, 'page-a') == {'spun_caption': HOOKS[0], 'website_preparation_id': record['id']}
    with pytest.raises(ValueError, match='10–30'):
        web.snapshot_post({**base, 'read_more': {'preparation_id': record['id'], 'captions': {'page-a': 'Too short'}}}, ['page-a', 'page-b'])
    with pytest.raises(ValueError, match='changed'):
        web.snapshot_post(base, ['page-a'])


def test_live_captions_alone_keep_the_posts_pinned_comment(monkeypatch):
    monkeypatch.setattr(web, 'generate_hooks', lambda _: HOOKS)
    record = web.prepare_captions(captions_request(post_type='live'))
    post = web.snapshot_post({'type': 'live', 'media_file': 'video.mp4', 'pinned_comment': 'Own comment', 'read_more': {'preparation_id': record['id']}}, ['page-a', 'page-b'])
    assert post['pinned_comment'] == 'Own comment' and 'end_after_comment' not in post
    assert 'pinned_comment' not in web.execution_variant(post, 'page-a')


def test_regenerating_a_caption_works_without_a_website(monkeypatch):
    monkeypatch.setattr(web, 'Website', Mock(side_effect=AssertionError('No website')))
    monkeypatch.setattr(web, 'generate_hooks', lambda value: ['Neighbors examine the council proposal and how the changes could shape their local park.'])
    record = web.prepare_captions(captions_request())
    result = web.regenerate_hook(web.RegenerateHook(preparation_id=record['id'], profile_id='page-a', other_captions=[]))
    assert 'Neighbors' in result['caption']
