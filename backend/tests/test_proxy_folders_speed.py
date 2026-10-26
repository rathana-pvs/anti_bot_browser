import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.routers import proxies
from backend.services import proxy_service
from backend.services.proxy_quality import normalize_proxy


def result(download=2, upload=2):
    return {direction: {'success': value is not None, 'mbps': value}
            for direction, value in [('download', download), ('upload', upload)]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / 'pool.json'
    monkeypatch.setattr(proxy_service, 'PROXY_POOL_FILE', path)
    proxy_service.save_proxy_pool([
        {'id': 'a', 'host': 'proxy.example', 'port': 1080, 'assigned': True,
         'profile_id': 'profile_001', 'password': 'secret', 'geo_source': 'unknown'},
        {'id': 'b', 'host': 'other.example', 'port': 1080, 'assigned': False, 'geo_source': 'unknown'},
    ])
    monkeypatch.setattr(proxies, 'test_proxy_ping', lambda *_: {'success': True, 'latency_ms': 10})
    monkeypatch.setattr(proxies, 'check_proxy_speed', lambda *_: {'success': True, 'mbps': 2})
    app = FastAPI()
    app.include_router(proxies.router)
    with TestClient(app) as session:
        yield session, path


@pytest.mark.parametrize('download,upload,folder,quality', [
    (2, 2, 'Available', 'good'), (1, 1, 'Available', 'good'),
    (.99, 2, 'Poor proxy', 'poor'), (2, .99, 'Poor proxy', 'poor'),
    (0, 2, 'Poor proxy', 'poor'), (.5, None, 'Poor proxy', 'poor'),
    (2, None, 'Poor proxy', 'check_failed'), (float('nan'), 2, 'Poor proxy', 'check_failed'),
    (float('inf'), 2, 'Poor proxy', 'check_failed'), (-1, 2, 'Poor proxy', 'check_failed'),
])
def test_classification_threshold(download, upload, folder, quality):
    proxy = normalize_proxy({'speed_test': result(download, upload)})
    assert proxy['group'] == folder
    assert proxy['speed_quality'] == quality


def test_assignment_and_release_reclassify_using_saved_speed(client):
    session, path = client
    proxy_service.update_proxy_metadata(['b'], speed_test=result(.5, 2))
    assigned = proxy_service.assign_proxy_by_id('profile_002', 'b')
    assert assigned['group'] == 'Used'
    assert assigned['speed_quality'] == 'poor'
    assert proxy_service.release_proxy('profile_002')
    pool = json.loads(path.read_text())
    assert pool[1]['group'] == 'Poor proxy'
    assert pool[1]['profile_id'] is None
    proxy_service.update_proxy_metadata(['b'], speed_test=result(2, 2))
    assert session.get('/api/proxies').json()[1]['group'] == 'Available'


def test_assigned_flags_take_priority():
    for fields in [{'assigned': True}, {'profile_id': 'profile_1'}]:
        assert normalize_proxy({**fields, 'speed_test': result(.1, .1)})['group'] == 'Used'


def test_failed_retest_does_not_promote_poor_proxy(client):
    session, _ = client
    proxy_service.update_proxy_metadata(['b'], speed_test=result(.5, 2))
    proxy_service.update_proxy_metadata(['b'], speed_test=result(2, None))
    proxy = session.get('/api/proxies').json()[1]
    assert proxy['group'] == 'Poor proxy'
    assert proxy['speed_quality'] == 'check_failed'
    proxy_service.update_proxy_metadata(['b'], speed_test=result(1, 1))
    assert session.get('/api/proxies').json()[1]['group'] == 'Available'


def test_untested_and_legacy_groups(client):
    session, path = client
    pool = json.loads(path.read_text())
    pool[1]['group'] = 'Cambodia residential'
    path.write_text(json.dumps(pool))
    proxy = session.get('/api/proxies').json()[1]
    assert proxy['group'] == 'Available' and proxy['speed_quality'] == 'untested'
    assert proxy['legacy_group'] == 'Cambodia residential'
    assert session.get('/api/proxies/groups').json() == ['Used', 'Available', 'Poor proxy']


@pytest.mark.parametrize('method,url,payload', [
    ('post', '/api/proxies/set-group', {'proxy_ids': ['a'], 'group': 'Available'}),
    ('post', '/api/proxies/set-group', {'proxy_ids': ['b'], 'group': 'Used'}),
    ('post', '/api/proxies/groups', {'name': 'New'}),
    ('put', '/api/proxies/groups/Used', {'name': 'Other'}),
    ('delete', '/api/proxies/groups/Used', None),
])
def test_automatic_folders_cannot_be_overridden(client, method, url, payload):
    session, path = client
    original = path.read_text()
    assert session.request(method, url, json=payload).status_code == 409
    assert path.read_text() == original


@pytest.mark.parametrize('endpoint', ['/api/proxies/b/speed-test', '/api/proxies/speed-test'])
def test_all_pool_speed_checks_persist_classification(client, monkeypatch, endpoint):
    session, path = client
    def measure(network, direction):
        return {'success': True, 'mbps': .5 if direction == 'upload' else 2}
    monkeypatch.setattr(proxies, 'check_proxy_speed', measure)
    response = session.post(endpoint, json={'mode': 'pool', 'proxy_id': 'b'})
    assert response.status_code == 200
    assert 'secret' not in response.text
    pool = json.loads(path.read_text())
    assert pool[1]['speed_test'] == response.json()
    assert pool[1]['group'] == 'Poor proxy'


def test_assignment_during_slow_test_is_preserved(client, monkeypatch):
    session, path = client
    def measure(network, direction):
        proxy_service.assign_proxy_by_id('profile_002', 'b')
        return {'success': True, 'mbps': .2}
    monkeypatch.setattr(proxies, 'check_proxy_speed', measure)
    assert session.post('/api/proxies/b/speed-test').status_code == 200
    proxy = json.loads(path.read_text())[1]
    assert proxy['group'] == 'Used' and proxy['profile_id'] == 'profile_002'
    assert proxy['speed_quality'] == 'poor'


def test_unknown_proxy_speed_returns_404(client):
    session, _ = client
    assert session.post('/api/proxies/missing/speed-test').status_code == 404


def test_new_import_is_available_and_untested(client):
    session, _ = client
    imported = proxy_service.import_proxies_from_text('new.example:1080:user:password')
    proxy = next(p for p in session.get('/api/proxies').json() if p['id'] in imported['added_ids'])
    assert proxy['group'] == 'Available' and proxy['speed_quality'] == 'untested'


def test_automatic_assignment_skips_poor_proxies(client):
    session, _ = client
    proxy_service.update_proxy_metadata(['b'], speed_test=result(.5, 2))
    assert proxy_service.assign_proxy('profile_002') is None
    proxy_service.update_proxy_metadata(['b'], speed_test=result(1, 1))
    assert proxy_service.assign_proxy('profile_002')['group'] == 'Used'


def test_first_timeout_moves_available_proxy_to_poor_and_retest_recovers(client):
    session, _ = client
    assert session.get('/api/proxies').json()[1]['group'] == 'Available'
    proxy_service.update_proxy_metadata(['b'], speed_test=result(4.45, None))
    failed = session.get('/api/proxies').json()[1]
    assert failed['group'] == 'Poor proxy' and failed['speed_quality'] == 'check_failed'
    proxy_service.assign_proxy_by_id('profile_002', 'b')
    assert session.get('/api/proxies').json()[1]['group'] == 'Used'
    proxy_service.release_proxy('profile_002')
    assert session.get('/api/proxies').json()[1]['group'] == 'Poor proxy'
    proxy_service.update_proxy_metadata(['b'], speed_test=result(2, 2))
    assert session.get('/api/proxies').json()[1]['group'] == 'Available'
