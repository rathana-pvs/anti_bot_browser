import subprocess
from types import SimpleNamespace

import pytest

from backend.services import proxy_service as service

NETWORK = {'mode': 'pool', 'proxy_host': 'proxy.example.test', 'proxy_port': 8080,
           'proxy_type': 'http', 'proxy_user': 'sample-user', 'proxy_pass': 'sample-pass'}


@pytest.mark.parametrize('original', [None, '', '/opt/system-libs'])
def test_frozen_curl_restores_system_library_environment(monkeypatch, original):
    monkeypatch.setattr(service.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(service.sys, 'platform', 'linux')
    monkeypatch.setenv('LD_LIBRARY_PATH', '/tmp/_MEI-test')
    monkeypatch.setenv('PROXY_TEST_PRESERVED', 'yes')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    if original is not None:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    calls = stub(monkeypatch, '200 5000000 2')
    assert service.check_proxy_speed(NETWORK, 'download')['success']
    env = calls[0][1]['env']
    assert env.get('LD_LIBRARY_PATH') == (original or None)
    assert 'LD_LIBRARY_PATH_ORIG' not in env
    assert env['PROXY_TEST_PRESERVED'] == 'yes'
    assert service.os.environ['LD_LIBRARY_PATH'] == '/tmp/_MEI-test'


def test_source_curl_preserves_library_environment(monkeypatch):
    monkeypatch.setattr(service.sys, 'frozen', False, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/opt/custom-libs')
    assert service._curl_environment()['LD_LIBRARY_PATH'] == '/opt/custom-libs'


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


@pytest.mark.parametrize('direction', ['download', 'upload'])
def test_recovery_sample_can_measure_just_above_one_mbps_within_timeout(monkeypatch, direction):
    calls = stub(monkeypatch, '200 1000000 7')
    result = service.check_proxy_speed(NETWORK, direction, sample_bytes=1_000_000)
    assert result['success'] and 1 < result['mbps'] < 1.2
    assert result['bytes'] == 1_000_000
    if direction == 'download':
        assert '__down?bytes=1000000' in calls[0][1]['input']


@pytest.mark.parametrize('size', [0, 249999, 5000001, '1000000'])
def test_speed_sample_bounds(monkeypatch, size):
    calls = stub(monkeypatch, '200 1000000 7')
    with pytest.raises(ValueError, match='Speed sample'):
        service.check_proxy_speed(NETWORK, 'download', sample_bytes=size)
    assert not calls


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


@pytest.mark.parametrize('original', [None, '/opt/system-libs', ''])
@pytest.mark.parametrize('kind', ['download', 'upload', 'facebook', 'geography'])
def test_frozen_curl_uses_system_libraries(monkeypatch, original, kind):
    monkeypatch.setattr(service.sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/tmp/_MEI-test')
    if original is None:
        monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    else:
        monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', original)
    calls = stub(monkeypatch, '{"success":true,"ip":"1.1.1.1","timezone":{"id":"UTC"}}' if kind == 'geography' else '200 5000000 2')
    if kind == 'geography':
        service.lookup_proxy_geography({'host': 'proxy.example.test', 'port': 8080})
    elif kind == 'facebook':
        service.check_facebook_response(NETWORK)
    else:
        service.check_proxy_speed(NETWORK, kind)
    # Empty original paths are removed so bundled libraries cannot leak into curl.
    assert calls[0][1]['env'].get('LD_LIBRARY_PATH') == (original or None)
    assert service.os.environ['LD_LIBRARY_PATH'] == '/tmp/_MEI-test'


def test_dev_curl_preserves_library_path(monkeypatch):
    monkeypatch.setattr(service.sys, 'frozen', False, raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', '/opt/dev-libs')
    calls = stub(monkeypatch, '200 5000000 2')
    assert service.check_proxy_speed(NETWORK, 'download')['success']
    assert calls[0][1]['env']['LD_LIBRARY_PATH'] == '/opt/dev-libs'
