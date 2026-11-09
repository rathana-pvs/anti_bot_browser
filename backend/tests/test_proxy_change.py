import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import account_activity, automation_service, proxy_change, proxy_service
from backend.services import android_devices, docker_service

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    pool = tmp_path / 'proxy_pool.json'
    pool.write_text(json.dumps([
        {'id': 'cur', 'host': '1.1.1.1', 'port': 1, 'username': 'u', 'password': 'p', 'type': 'socks5', 'profile_ids': ['pc1'],
         'country_code': 'US', 'city': 'Dallas', 'region': 'Texas', 'timezone': 'America/Chicago', 'geo_source': 'ip_lookup',
         'replaced_by': 'new', 'replaced_by_address': '2.2.2.2:2'},
        {'id': 'new', 'host': '2.2.2.2', 'port': 2, 'username': 'u', 'password': 'p', 'type': 'socks5', 'country_code': 'US',
         'city': 'Miami', 'region': 'Florida', 'timezone': 'America/New_York', 'geo_source': 'ip_lookup', 'latency_ms': 90},
        {'id': 'de', 'host': '3.3.3.3', 'port': 3, 'username': 'u', 'password': 'p', 'type': 'socks5', 'country_code': 'DE',
         'city': 'Berlin', 'timezone': 'Europe/Berlin', 'geo_source': 'ip_lookup', 'latency_ms': 20},
        {'id': 'full', 'host': '4.4.4.4', 'port': 4, 'username': 'u', 'password': 'p', 'type': 'socks5', 'profile_ids': ['x'],
         'geo_source': 'unknown'},
    ]))
    monkeypatch.setattr(proxy_service, 'PROXY_POOL_FILE', pool)
    monkeypatch.setattr(proxy_service, 'account_limit', lambda: 1)
    monkeypatch.setattr(proxy_change, 'PROFILES_DIR', tmp_path)
    (tmp_path / 'pc1').mkdir()
    (tmp_path / 'pc1' / 'config.json').write_text(json.dumps({
        'id': 'pc1', 'network': {'mode': 'pool', 'proxy_id': 'cur', 'proxy_host': '1.1.1.1', 'proxy_port': 1},
        'requested_environment': {'timezone': 'America/Chicago'}}))
    state = {'container': 'stopped', 'last_active': NOW - timedelta(hours=1)}
    monkeypatch.setattr(docker_service, 'get_container_status', lambda pid: state['container'])
    monkeypatch.setattr(account_activity, 'last_active', lambda pid, config, now=None, **kw: state['last_active'])
    monkeypatch.setattr(automation_service, 'active_automation_tasks', {})
    monkeypatch.setattr(automation_service, 'current_scheduler_snapshot', lambda: {'leases': []})
    return state


def test_options_put_the_webshare_replacement_first_and_skip_replaced_and_full_proxies(setup):
    data = proxy_change.options('pc1', NOW)
    assert data['running_reason'] is None
    assert data['current']['replaced_by_address'] == '2.2.2.2:2'
    ids = [o['id'] for o in data['options']]
    assert ids == ['new', 'de']
    new, de = data['options']
    assert new['suggested'] and new['same_country']
    assert new['rest_hours'] < de['rest_hours']



def test_options_skip_a_timed_out_proxy_that_already_has_accounts(setup, monkeypatch):
    monkeypatch.setattr(proxy_service, 'account_limit', lambda: 3)
    pool = proxy_service.load_proxy_pool()
    stall = {'mbps': None, 'bytes': 0, 'error': 'Upload did not finish within 8 s'}
    for proxy in pool:
        if proxy['id'] == 'full':
            proxy['check'] = {'reachable': True, 'facebook_ok': True, 'download': [{'mbps': 7.0, 'bytes': 1}],
                              'upload': [stall, stall, stall]}
    proxy_service.save_proxy_pool(pool)
    full = next(p for p in proxy_service.load_proxy_pool() if p['id'] == 'full')
    assert full['group'] == 'Used' and full['speed_quality'] == 'check_failed'
    assert [o['id'] for o in proxy_change.options('pc1', NOW)['options']] == ['new', 'de']


@pytest.mark.parametrize('status', ['running', 'paused'])
def test_an_open_browser_blocks_the_change(setup, status):
    setup['container'] = status
    moved = []
    with pytest.raises(proxy_change.AccountRunning, match='browser is open'):
        proxy_change.change('pc1', 'new', lambda *a: moved.append(a))
    assert moved == []
    assert proxy_change.options('pc1', NOW)['running_reason']


def test_a_running_job_blocks_the_change(setup, monkeypatch):
    monkeypatch.setattr(automation_service, 'current_scheduler_snapshot', lambda: {'leases': [{'profile_id': 'pc1'}]})
    with pytest.raises(proxy_change.AccountRunning, match='job is running'):
        proxy_change.change('pc1', 'new', lambda *a: None)


def test_a_phone_job_blocks_a_phone_account(setup, tmp_path, monkeypatch):
    config = json.loads((tmp_path / 'pc1' / 'config.json').read_text())
    config['runtime'] = {'kind': android_devices.ANDROID_RUNTIME, 'device_serial': 'S1', 'android_user_id': 10}
    (tmp_path / 'pc1' / 'config.json').write_text(json.dumps(config))
    monkeypatch.setattr(android_devices, 'job_on', lambda serial: 'post:abc')
    with pytest.raises(proxy_change.AccountRunning, match='phone is running a job'):
        proxy_change.change('pc1', 'new', lambda *a: None)


def test_a_stopped_pc_account_changes_through_the_settings_path(setup):
    moved = []
    proxy_change.change('pc1', 'new', lambda pid, proxy_id: moved.append((pid, proxy_id)))
    assert moved == [('pc1', 'new')]


def test_only_a_real_network_change_counts():
    current = {'mode': 'pool', 'proxy_id': 'a', 'proxy_host': 'h', 'proxy_port': 1}
    assert not proxy_change.network_changes(current, {'mode': 'pool', 'proxy_id': 'a'})
    assert proxy_change.network_changes(current, {'mode': 'pool', 'proxy_id': 'b'})
    assert proxy_change.network_changes(current, {'mode': 'direct'})
    custom = {'mode': 'custom', 'proxy_host': 'h', 'proxy_port': 1, 'proxy_user': '', 'proxy_pass': ''}
    assert not proxy_change.network_changes(custom, {'mode': 'custom', 'host': 'h', 'port': 1})
    assert proxy_change.network_changes(custom, {'mode': 'custom', 'host': 'h', 'port': 2})
