import subprocess
from types import SimpleNamespace

import pytest

from backend.services import proxy_service as service

NETWORK = {'mode': 'pool', 'proxy_host': 'proxy.example.test', 'proxy_port': 8080,
           'proxy_type': 'http', 'proxy_user': 'sample-user', 'proxy_pass': 'sample-pass'}


def stub(monkeypatch, output, returncode=0):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=output, returncode=returncode)
    monkeypatch.setattr(service.subprocess, 'run', run)
    return calls


def test_facebook_first_byte_and_explicit_proxy(monkeypatch):
    calls = stub(monkeypatch, '200 0.516')
    result = service.check_facebook_response(NETWORK)
    assert result['success'] and result['response_ms'] == 516
    argv, options = calls[0]
    assert argv == ['curl', '--disable', '--config', '-']
    assert 'sample-pass' not in ' '.join(argv)
    assert 'proxy = "http://proxy.example.test:8080"' in options['input']
    assert 'noproxy = ""' in options['input']
    assert 'https://www.facebook.com/' in options['input']
    assert 'head\n' in options['input']
    assert 'location' not in options['input']


@pytest.mark.parametrize('scheme,expected', [('socks5','socks5h'),('http','http'),('https','https')])
def test_proxy_protocol(monkeypatch, scheme, expected):
    calls = stub(monkeypatch, '302 0.1')
    assert service.check_facebook_response({**NETWORK,'proxy_type':scheme})['success']
    assert f'{expected}://proxy.example.test:8080' in calls[0][1]['input']


@pytest.mark.parametrize('direction,size', [('download',5000000),('upload',2000000)])
def test_speed_conversion_and_byte_limit(monkeypatch, direction, size):
    calls = stub(monkeypatch, f'200 {size} 2')
    result = service.check_proxy_speed(NETWORK, direction)
    assert result['success'] and result['mbps'] == size * 8 / 2 / 1000000
    config = calls[0][1]['input']
    assert 'max-time = 20' in config and 'noproxy = ""' in config
    assert 'https://speed.cloudflare.com/__' in config
    assert ('data-binary' in config) == (direction == 'upload')


def test_download_rejects_incomplete_measurement(monkeypatch):
    stub(monkeypatch, '200 200 1')
    result = service.check_proxy_speed(NETWORK, 'download')
    assert not result['success'] and result['mbps'] is None


def test_facebook_http_error_keeps_timing(monkeypatch):
    stub(monkeypatch, '403 0.2')
    result = service.check_facebook_response(NETWORK)
    assert not result['success'] and result['response_ms'] == 200
    assert '403' in result['error']


def test_proxy_failure_never_retries_direct(monkeypatch):
    calls = stub(monkeypatch, '000 0', 7)
    assert not service.check_facebook_response(NETWORK)['success']
    assert len(calls) == 1


@pytest.mark.parametrize('method', [service.check_facebook_response, lambda n: service.check_proxy_speed(n,'upload')])
def test_direct_connection_rejected(monkeypatch, method):
    stub(monkeypatch, '200 1')
    with pytest.raises(ValueError):
        method({'mode':'direct'})


def test_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('curl',18)
    monkeypatch.setattr(service.subprocess,'run',timeout)
    assert 'timed out' in service.check_facebook_response(NETWORK)['error']
    assert 'timed out' in service.check_proxy_speed(NETWORK,'upload')['error']


@pytest.mark.parametrize('kind', ['facebook','download','upload'])
def test_route_uses_saved_profile_network(tmp_path, monkeypatch, kind):
    import json
    from backend.routers import profiles
    monkeypatch.setattr(profiles,'PROFILES_DIR',tmp_path)
    profile = tmp_path/'profile_001'
    profile.mkdir()
    (profile/'config.json').write_text(json.dumps({'network': NETWORK}))
    def check(network, *args):
        assert network == NETWORK
        assert args == (() if kind == 'facebook' else (kind,))
        return {'success': True}
    monkeypatch.setattr(profiles,'check_facebook_response' if kind == 'facebook' else 'check_proxy_speed',check)
    result = profiles.check_profile_facebook_response('profile_001') if kind == 'facebook' else profiles.check_profile_proxy_speed('profile_001',kind)
    assert result['success'] and result['last_checked']
