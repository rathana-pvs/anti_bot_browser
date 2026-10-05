import json

import pytest
from fastapi import HTTPException

from backend.routers import profiles


@pytest.mark.parametrize('mode', ['pool', 'custom'])
def test_latency_uses_saved_profile_endpoint(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path)
    folder = tmp_path / 'profile_001'
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'network': {
        'mode': mode, 'proxy_host': 'proxy.example.test', 'proxy_port': 8080,
    }}))
    def check(host, port):
        assert (host, port) == ('proxy.example.test', 8080)
        return {'success': True, 'latency_ms': 0}
    monkeypatch.setattr(profiles, 'test_proxy_ping', check)
    result = profiles.check_profile_proxy_latency('profile_001')
    assert result['latency_ms'] == 0
    assert result['success'] is True
    assert result['last_checked']


def test_direct_profile_does_not_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path)
    folder = tmp_path / 'profile_001'
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'network': {'mode': 'direct'}}))
    monkeypatch.setattr(profiles, 'test_proxy_ping', lambda *_: pytest.fail('Direct connection was probed'))
    with pytest.raises(HTTPException) as error:
        profiles.check_profile_proxy_latency('profile_001')
    assert error.value.status_code == 400


def test_missing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path)
    with pytest.raises(HTTPException) as error:
        profiles.check_profile_proxy_latency('profile_001')
    assert error.value.status_code == 404


def test_failed_connection_reports_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path)
    folder = tmp_path / 'profile_001'
    folder.mkdir()
    (folder / 'config.json').write_text(json.dumps({'network': {'proxy_host': 'proxy.example.test', 'proxy_port': 8080}}))
    monkeypatch.setattr(profiles, 'test_proxy_ping', lambda *_: {'success': False, 'latency_ms': None, 'error': 'Connection timed out'})
    result = profiles.check_profile_proxy_latency('profile_001')
    assert result['success'] is False
    assert result['latency_ms'] is None
    assert result['error'] == 'Connection timed out'
