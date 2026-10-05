from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend import config
from backend.models.profile import ProfileCreateRequest, ProfileUpdateRequest
from backend.routers import profiles
from backend.services import profile_service


@pytest.mark.parametrize('file_value', ['', 'Invalid/Timezone'])
def test_invalid_host_detection_reports_fallback(file_value):
    fake_path = SimpleNamespace(
        exists=lambda: True,
        read_text=lambda **_kwargs: file_value,
        is_symlink=lambda: False,
    )
    with patch.object(config, 'Path', return_value=fake_path), \
         patch.object(config.subprocess, 'check_output', side_effect=FileNotFoundError):
        assert config.detect_host_timezone() is None
        assert config.get_host_timezone() == 'America/Guatemala'


def test_valid_host_timezone_is_detected():
    fake_path = SimpleNamespace(exists=lambda: True, read_text=lambda **_kwargs: 'Asia/Phnom_Penh\n')
    with patch.object(config, 'Path', return_value=fake_path):
        assert config.detect_host_timezone() == 'Asia/Phnom_Penh'


def test_defaults_expose_fallback_and_curated_choices(monkeypatch):
    monkeypatch.setattr(profile_service, 'detect_host_timezone', lambda: None)
    defaults = profile_service.profile_defaults()
    assert defaults['host_timezone_detected'] is False
    assert defaults['default_environment']['timezone'] == 'America/Guatemala'
    choices = defaults['timezone_options']
    assert choices[:2] == ['America/Guatemala', 'Asia/Phnom_Penh']
    assert len(choices) == len(set(choices))
    for zone in choices:
        profile_service.validate_timezone(zone)
    assert {'America/New_York', 'America/Phoenix', 'America/Adak', 'Pacific/Honolulu'} <= set(choices)
    assert 'Europe/London' not in choices


def test_confirmed_manual_timezone_survives_failed_host_detection(monkeypatch):
    monkeypatch.setattr(profile_service, 'detect_host_timezone', lambda: None)
    with pytest.raises(ValueError, match='Confirm a timezone'):
        profile_service.resolve_requested_environment({'timezone_policy': 'host'}, network_mode='direct')
    for zone in ('America/Guatemala', 'Asia/Phnom_Penh', 'America/New_York'):
        resolved = profile_service.resolve_requested_environment(
            {'timezone_policy': 'manual', 'timezone': zone}, network_mode='direct',
        )
        assert resolved['timezone'] == zone
        assert resolved['timezone_policy'] == 'manual'


def test_detected_host_requires_no_confirmation(monkeypatch):
    monkeypatch.setattr(profile_service, 'detect_host_timezone', lambda: 'Asia/Phnom_Penh')
    defaults = profile_service.profile_defaults()
    assert defaults['host_timezone_detected'] is True
    resolved = profile_service.resolve_requested_environment({'timezone_policy': 'host'}, network_mode='direct')
    assert resolved['timezone'] == 'Asia/Phnom_Penh'


def test_profile_writes_block_fallback_until_manual_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_service, 'detect_host_timezone', lambda: None)
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path)
    monkeypatch.setattr(profiles, 'allocate_profile_identity', lambda: ('profile_001', 5901, 6081))
    monkeypatch.setattr(profiles, 'release_proxy', lambda _profile: None)
    with pytest.raises(HTTPException, match='Confirm a timezone') as error:
        profiles.create_profile(ProfileCreateRequest(name='Test'))
    assert error.value.status_code == 400
    assert not (tmp_path / 'profile_001').exists()

    created = profiles.create_profile(ProfileCreateRequest(
        name='Test', requested_environment={'timezone_policy': 'manual', 'timezone': 'Asia/Phnom_Penh'},
    ))
    assert created['fingerprint']['timezone'] == 'Asia/Phnom_Penh'
    with pytest.raises(HTTPException, match='Confirm a timezone'):
        profiles.update_profile('profile_001', ProfileUpdateRequest(
            requested_environment={'timezone_policy': 'host'},
        ))
    updated = profiles.update_profile('profile_001', ProfileUpdateRequest(
        requested_environment={'timezone_policy': 'manual', 'timezone': 'America/Guatemala'},
    ))
    assert updated['fingerprint']['timezone'] == 'America/Guatemala'


def test_migration_preserves_stored_timezone_when_detection_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_service, 'detect_host_timezone', lambda: None)
    legacy = {
        'id': 'profile_001', 'network': {'proxy_host': ''},
        'fingerprint': {'timezone': 'Asia/Phnom_Penh'},
    }
    migrated, _ = profile_service.migrate_profile(legacy, tmp_path, persist=False)
    assert migrated['requested_environment']['timezone'] == 'Asia/Phnom_Penh'
