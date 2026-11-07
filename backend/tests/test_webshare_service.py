import json
import stat
import time

import pytest

from backend.services import proxy_service, webshare_service


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / 'proxy_pool.json'
    path.write_text(json.dumps([
        {'id': 'old', 'host': '1.1.1.1', 'port': 6000, 'username': 'u', 'password': 'pw', 'type': 'socks5',
         'geo_source': 'unknown', 'profile_ids': ['pc1', 'phone1'], 'profile_id': 'pc1', 'assigned': True},
        {'id': 'other', 'host': '9.9.9.9', 'port': 6000, 'username': 'u', 'password': 'pw', 'type': 'socks5',
         'geo_source': 'unknown'},
    ]))
    monkeypatch.setattr(proxy_service, 'PROXY_POOL_FILE', path)
    monkeypatch.setattr(proxy_service, 'account_limit', lambda: 5)
    monkeypatch.setattr(proxy_service, 'refresh_proxy_statuses', lambda ids: {})
    monkeypatch.setattr(webshare_service, 'KEY_FILE', tmp_path / 'webshare.json')
    return lambda: {p['id']: p for p in json.loads(path.read_text())}


class FakeWebshare:
    def __init__(self, state='completed'):
        self.calls = []
        self.state = state

    def __call__(self, method, path, key=None, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == '/v2/proxy/list/':
            ip = kwargs['params'].get('proxy_address')
            rows = {'1.1.1.1': {'proxy_address': '1.1.1.1', 'port': 6000, 'username': 'u', 'password': 'pw', 'country_code': 'US'},
                    '2.2.2.2': {'proxy_address': '2.2.2.2', 'port': 7000, 'username': 'u', 'password': 'pw2', 'country_code': 'US'}}
            return {'results': [rows[ip]] if ip in rows else []}
        if path == '/v3/proxy/replace/':
            return {'id': 42, 'state': 'validating'}
        if path == '/v3/proxy/replace/42/':
            return {'id': 42, 'state': self.state, 'error': 'not enough proxies' if self.state == 'failed' else None}
        if path == '/v2/proxy/list/replaced/':
            return {'results': [{'proxy': '1.1.1.1', 'proxy_port': 6000, 'replaced_with': '2.2.2.2', 'replaced_with_port': 7000}]}
        raise AssertionError(path)


def test_key_is_saved_owner_only_and_never_returned(pool):
    status = webshare_service.set_api_key('  abcdefgh1234 ', verify=lambda key: None)
    assert status == {'webshare_key_set': True, 'webshare_key_hint': '…1234'}
    assert webshare_service.api_key() == 'abcdefgh1234'
    assert stat.S_IMODE(webshare_service.KEY_FILE.stat().st_mode) == 0o600
    assert webshare_service.set_api_key('', verify=lambda key: None)['webshare_key_set'] is False
    assert not webshare_service.KEY_FILE.exists()


def test_a_key_webshare_rejects_is_not_saved(pool):
    def reject(key):
        raise webshare_service.WebshareError('Webshare rejected the API key.')
    with pytest.raises(webshare_service.WebshareError):
        webshare_service.set_api_key('badkey123', verify=reject)
    assert webshare_service.api_key() is None


def test_replace_keeps_accounts_on_the_old_proxy_and_leaves_the_new_one_available(pool, monkeypatch):
    fake = FakeWebshare()
    monkeypatch.setattr(webshare_service, '_request', fake)
    result = webshare_service.replace_one('old', sleep=lambda s: None)
    body = next(kw['json'] for m, p, kw in fake.calls if p == '/v3/proxy/replace/')
    assert body['to_replace'] == {'type': 'ip_address', 'ip_addresses': ['1.1.1.1'], 'count': 1}
    assert body['replace_with'] == [{'type': 'country', 'country_code': 'US', 'count': 1}]
    assert result['old'] == '1.1.1.1:6000' and result['new'] == '2.2.2.2:7000'
    assert result['accounts'] == ['pc1', 'phone1']
    proxies = pool()
    new = proxies[result['new_id']]
    assert (new['host'], new['port'], new['password'], new['type']) == ('2.2.2.2', 7000, 'pw2', 'socks5')
    assert proxy_service.holders(new) == [] and new['group'] == 'Available'
    old = proxies['old']
    assert proxy_service.holders(old) == ['pc1', 'phone1']
    assert old['replaced_by'] == result['new_id'] and old['replaced_by_address'] == '2.2.2.2:7000'


def test_a_replaced_proxy_takes_no_new_account_but_its_accounts_can_leave(pool, monkeypatch):
    monkeypatch.setattr(webshare_service, '_request', FakeWebshare())
    result = webshare_service.replace_one('old', sleep=lambda s: None)
    with pytest.raises(ValueError, match='was replaced at Webshare'):
        proxy_service.assign_proxy_by_id('pc9', 'old')
    assert proxy_service.assign_proxy('pc9')['id'] != 'old'
    # An account already on it keeps it until moved, then moving works as usual.
    proxy_service.assign_proxy_by_id('pc1', 'old')
    proxy_service.assign_proxy_by_id('pc1', result['new_id'])
    proxy_service.assign_proxy_by_id('phone1', 'other')
    assert proxy_service.holders(pool()['old']) == [] and pool()['old']['group'] == 'Poor proxy'


def test_a_replaced_proxy_with_no_account_is_deleted(pool, monkeypatch):
    monkeypatch.setattr(webshare_service, '_request', FakeWebshare())
    proxy_service.update_proxy_metadata(['old'], profile_ids=[], profile_id=None, assigned=False)
    webshare_service.replace_one('old', sleep=lambda s: None)
    assert 'old' not in pool()


def test_an_already_replaced_proxy_is_not_replaced_again(pool, monkeypatch):
    fake = FakeWebshare()
    monkeypatch.setattr(webshare_service, '_request', fake)
    webshare_service.replace_one('old', sleep=lambda s: None)
    calls = len(fake.calls)
    with pytest.raises(webshare_service.WebshareError, match='Already replaced'):
        webshare_service.replace_one('old', sleep=lambda s: None)
    assert len(fake.calls) == calls


def test_a_failed_webshare_replacement_changes_nothing(pool, monkeypatch):
    monkeypatch.setattr(webshare_service, '_request', FakeWebshare(state='failed'))
    before = pool()
    with pytest.raises(webshare_service.WebshareError, match='not enough proxies'):
        webshare_service.replace_one('old', sleep=lambda s: None)
    assert pool() == before


def test_a_proxy_not_on_webshare_is_refused(pool, monkeypatch):
    monkeypatch.setattr(webshare_service, '_request', FakeWebshare())
    with pytest.raises(webshare_service.WebshareError, match="isn't one of your Webshare proxies"):
        webshare_service.replace_one('other', sleep=lambda s: None)


class AlreadySwapped(FakeWebshare):
    """1.1.1.1 was swapped to 3.3.3.3 and then to 2.2.2.2 before this run, over two pages."""

    def __call__(self, method, path, key=None, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == '/v2/proxy/list/':
            ip = kwargs['params'].get('proxy_address')
            row = {'proxy_address': '2.2.2.2', 'port': 7000, 'username': 'u', 'password': 'pw2', 'country_code': 'US'}
            return {'results': [row] if ip == '2.2.2.2' else []}
        if path == '/v2/proxy/list/replaced/':
            if kwargs['params']['page'] == 1:
                return {'next': 'more', 'results': [
                    {'proxy': '3.3.3.3', 'proxy_port': 6500, 'replaced_with': '2.2.2.2', 'replaced_with_port': 7000,
                     'created_at': '2026-11-07T14:00:00Z'}]}
            return {'next': None, 'results': [
                {'proxy': '1.1.1.1', 'proxy_port': 6000, 'replaced_with': '3.3.3.3', 'replaced_with_port': 6500,
                 'created_at': '2026-11-07T13:50:00Z'}]}
        raise AssertionError(path)


def test_a_proxy_webshare_already_swapped_is_caught_up_without_a_new_swap(pool, monkeypatch):
    fake = AlreadySwapped()
    monkeypatch.setattr(webshare_service, '_request', fake)
    result = webshare_service.replace_one('old', sleep=lambda s: None)
    assert not any(p == '/v3/proxy/replace/' for m, p, kw in fake.calls)  # nothing new is swapped
    assert result['already_replaced'] is True and result['new'] == '2.2.2.2:7000'
    proxies = pool()
    assert proxies[result['new_id']]['password'] == 'pw2'
    assert proxies['old']['replaced_by'] == result['new_id']


def test_a_swapped_proxy_with_no_account_is_removed_once_caught_up(pool, monkeypatch):
    monkeypatch.setattr(webshare_service, '_request', AlreadySwapped())
    proxy_service.update_proxy_metadata(['old'], profile_ids=[], profile_id=None, assigned=False)
    result = webshare_service.replace_one('old', sleep=lambda s: None)
    assert 'old' not in pool() and result['new_id'] in pool()


def test_run_continues_after_one_proxy_fails(pool):
    webshare_service.set_api_key('abcdefgh1234', verify=lambda key: None)
    run = webshare_service.ReplaceRun()

    def replace(pid):
        if pid == 'bad':
            raise webshare_service.WebshareError('nope')
        return {'proxy_id': pid, 'old': 'a', 'new': 'b'}

    run.start(['bad', 'good'], replace_fn=replace)
    for _ in range(100):
        if run.status()['state'] == 'finished':
            break
        time.sleep(0.01)
    status = run.status()
    assert status['done'] == 2
    assert [r['error'] for r in status['results']] == ['nope', None]


def test_run_needs_a_key(pool):
    with pytest.raises(webshare_service.WebshareError):
        webshare_service.ReplaceRun().start(['old'])
