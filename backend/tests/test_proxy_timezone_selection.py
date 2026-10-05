import pytest
from fastapi import HTTPException

from backend.models.profile import NetworkIntent, ProfileCreateRequest, ProfileUpdateRequest
from backend.routers import profiles
from backend.services import profile_service, proxy_service


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(proxy_service, 'PROXY_POOL_FILE', tmp_path / 'pool.json')
    monkeypatch.setattr(profiles, 'allocate_profile_identity', lambda: ('profile_001', 5901, 6081))
    monkeypatch.setattr(profile_service, 'detect_host_timezone', lambda: 'Asia/Phnom_Penh')
    return tmp_path


@pytest.mark.parametrize('mode', ['pool', 'custom'])
def test_proxy_exit_timezone_overrides_host_and_stale_metadata(isolated_profiles, monkeypatch, mode):
    proxy_service.save_proxy_pool([{
        'id': 'proxy_1', 'host': 'proxy.example', 'port': 1080,
        'username': 'user', 'password': 'pass', 'timezone': 'America/Chicago',
        'geo_source': 'stored', 'assigned': False,
    }])
    observed = []

    def lookup(proxy):
        observed.append(proxy)
        return {'timezone': 'America/Los_Angeles'}

    monkeypatch.setattr(profiles, 'lookup_proxy_geography', lookup)
    intent = (NetworkIntent(mode='pool', proxy_id='proxy_1') if mode == 'pool' else
              NetworkIntent(mode='custom', host='proxy.example', port=1080, username='user', password='pass'))
    preview = profiles.detect_network_timezone(intent)
    assert preview['detected'] is True
    assert preview['timezone'] == 'America/Los_Angeles'
    assert preview['source'] == 'proxy'
    created = profiles.create_profile(ProfileCreateRequest(name='Test', network=intent))
    assert created['requested_environment']['timezone_policy'] == 'proxy'
    assert created['fingerprint']['timezone'] == 'America/Los_Angeles'
    assert observed[-1] == {'host': 'proxy.example', 'port': 1080, 'username': 'user', 'password': 'pass'}

    monkeypatch.setattr(profiles, 'lookup_proxy_geography', lambda _proxy: {'timezone': 'America/New_York'})
    updated = profiles.update_profile('profile_001', ProfileUpdateRequest(requested_environment={'language': 'en-US'}))
    assert updated['fingerprint']['timezone'] == 'America/New_York'
    assert updated['restart_required'] is True


def test_lookup_failure_prompts_for_manual_fallback(isolated_profiles, monkeypatch):
    def unavailable(_proxy):
        raise RuntimeError('Lookup unavailable')

    monkeypatch.setattr(profiles, 'lookup_proxy_geography', unavailable)
    intent = NetworkIntent(mode='custom', host='proxy.example', port=1080)
    preview = profiles.detect_network_timezone(intent)
    assert preview['detected'] is False
    # The computer's Cambodian timezone must not replace an unknown proxy timezone.
    assert preview['timezone'] == 'America/Guatemala'
    with pytest.raises(HTTPException) as error:
        profiles.create_profile(ProfileCreateRequest(name='Test', network=intent))
    assert error.value.status_code == 400
    created = profiles.create_profile(ProfileCreateRequest(
        name='Test', network=intent,
        requested_environment={'timezone_policy': 'manual', 'timezone': 'America/Guatemala'},
    ))
    assert created['fingerprint']['timezone'] == 'America/Guatemala'
    assert created['requested_environment']['timezone_policy'] == 'manual'


def test_stale_pool_timezone_cannot_hide_failed_lookup(isolated_profiles, monkeypatch):
    proxy_service.save_proxy_pool([{
        'id': 'proxy_1', 'host': 'proxy.example', 'port': 1080, 'assigned': False,
        'timezone': 'America/New_York', 'geo_source': 'stored',
    }])
    monkeypatch.setattr(profiles, 'lookup_proxy_geography', lambda _proxy: {'timezone': 'Invalid/Zone'})
    with pytest.raises(HTTPException):
        profiles.create_profile(ProfileCreateRequest(
            name='Test', network={'mode': 'pool', 'proxy_id': 'proxy_1'},
            requested_environment={'timezone_policy': 'proxy', 'timezone': 'America/New_York'},
        ))
    assert proxy_service.load_proxy_pool()[0]['assigned'] is False
    assert not (isolated_profiles / 'profiles' / 'profile_001' / 'config.json').exists()
