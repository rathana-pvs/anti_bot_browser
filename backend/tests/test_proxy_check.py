import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import proxy_check, proxy_service
from backend.services.proxy_quality import rate_check


def check(down=(8.0, 8.0), up=(3.0, 3.0), facebook_ms=400, reachable=True, exit_ip='1.1.1.1', at=None):
    return {'checked_at': at or datetime.now(timezone.utc).isoformat(), 'reachable': reachable, 'connect_ms': 250,
            'facebook_ok': reachable and facebook_ms is not None, 'facebook_ms': facebook_ms, 'exit_ip': exit_ip,
            'download': [{'mbps': v, 'bytes': 100000} for v in down], 'upload': [{'mbps': v, 'bytes': 100000} for v in up]}


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / 'proxy_pool.json'
    path.write_text(json.dumps([{'id': f'p{i}', 'host': f'10.0.0.{i}', 'port': 1080, 'username': 'u', 'password': 'pw',
                                 'type': 'socks5', 'geo_source': 'unknown'} for i in range(1, 7)]))
    monkeypatch.setattr(proxy_service, 'PROXY_POOL_FILE', path)
    return lambda: {p['id']: p for p in json.loads(path.read_text())}


# --- timed samples ---

def test_a_sample_cut_off_by_the_time_limit_still_gives_a_speed():
    # 750 KB in 3 s after the first byte at 0.5 s: 2.4 Mbps.
    sample = proxy_check.parse_sample('download', 28, '200 750000 0 0.4 0.5 3.0')
    assert sample == {'mbps': 2.4, 'bytes': 750000, 'error': None}


def test_upload_time_starts_when_sending_starts():
    sample = proxy_check.parse_sample('upload', 28, '100 0 500000 1.0 1.1 3.0')
    assert sample['mbps'] == 2.0


def test_an_upload_cut_off_before_cloudflare_answers_still_counts():
    # No response yet when the time limit hits: curl reports status 0 but has sent 375 KB.
    assert proxy_check.parse_sample('upload', 28, '000 0 375000 1.5 0 6.0')['mbps'] == 0.67


def test_a_sample_that_got_nothing_through_is_a_timeout():
    sample = proxy_check.parse_sample('download', 28, '000 0 0 1.8 0 5.0')
    assert sample['mbps'] is None and sample['error'] == 'Timed out before any data got through'


@pytest.mark.parametrize('rc,out,error', [
    (97, '000 0 0 0 0 0.8', 'No traffic got through this proxy'),
    (28, '200 1000 0 0.4 0.5 3.0', 'Too little got through to measure'),
    (0, '403 900000 0 0.1 0.2 1.0', 'Cloudflare returned HTTP 403'),
    (0, 'garbage', 'Speed sample returned no measurement'),
])
def test_samples_that_cannot_be_measured_say_why(rc, out, error):
    sample = proxy_check.parse_sample('download', rc, out)
    assert sample['mbps'] is None and sample['error'] == error


# --- ratings ---

def test_only_a_timeout_or_an_unreachable_proxy_is_poor():
    assert rate_check(check(down=(8.0, 8.0), up=(3.0, 3.0)))['grade'] == 'good'
    assert rate_check(check(down=(8.0, 8.0), up=(0.3, 0.5)))['grade'] == 'slow'
    assert rate_check(check(down=(5.0,), up=()))['grade'] == 'timeout'
    assert rate_check(check(reachable=False, down=(), up=()))['grade'] == 'dead'
    # A slow Facebook response no longer counts against the proxy.
    assert rate_check(check(facebook_ms=6000))['grade'] == 'good'


def test_samples_far_apart_mark_the_proxy_unstable():
    assert rate_check(check(up=(9.0, 4.2)))['stable'] is False


# --- saving ---

def test_saving_keeps_history_and_notices_a_new_exit_ip(pool):
    proxy_check.save_check('p1', check(exit_ip='1.1.1.1'))
    saved = proxy_check.save_check('p1', check(exit_ip='2.2.2.2'))
    assert saved['check']['ip_changed'] is True
    assert [h['exit_ip'] for h in saved['check']['history']] == ['1.1.1.1', '2.2.2.2']
    # Screens that read the older result shape keep working.
    assert saved['speed_test']['upload'] == {'success': True, 'mbps': 3.0, 'bytes': 200000, 'provider': 'Cloudflare', 'error': None}
    assert saved['group'] == 'Available' and saved['speed_quality'] == 'good'


def test_a_dead_proxy_moves_to_poor(pool):
    saved = proxy_check.save_check('p1', check(reachable=False, down=(), up=()))
    assert saved['rating']['grade'] == 'dead' and saved['group'] == 'Poor proxy'


# --- runs ---

def _wait(runner, timeout=5):
    deadline = time.monotonic() + timeout
    while runner.status()['state'] == 'running':
        assert time.monotonic() < deadline, runner.status()
        time.sleep(0.01)
    return runner.status()


def test_a_run_checks_several_proxies_at_once(pool):
    peak, running, lock = [0], [0], threading.Lock()

    def measure(proxy):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return check()

    runner = proxy_check.CheckRun()
    runner.start(at_once=3, measure_fn=measure, baseline_fn=lambda: {'upload_mbps': 20})
    status = _wait(runner)
    assert status['state'] == 'done' and status['done'] == 6 and status['grades']['good'] == 6
    assert peak[0] == 3 and status['baseline'] == {'upload_mbps': 20}
    assert all(p['check'] for p in pool().values())


def test_each_proxy_is_checked_once_even_when_slow(pool):
    calls = []

    def measure(proxy):
        calls.append(proxy['id'])
        return check(up=(0.5, 0.5)) if proxy['id'] == 'p1' else check()

    runner = proxy_check.CheckRun()
    runner.start(at_once=3, measure_fn=measure, baseline_fn=lambda: None)
    status = _wait(runner)
    assert sorted(calls) == ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']
    assert status['grades'] == {'good': 5, 'slow': 1, 'timeout': 0, 'dead': 0}
    assert pool()['p1']['group'] == 'Available'


def test_proxies_checked_in_the_last_day_are_skipped_unless_forced(pool):
    proxy_check.save_check('p1', check())
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    proxy_check.save_check('p2', check(at=old))
    runner = proxy_check.CheckRun()
    assert runner.start(at_once=2, measure_fn=lambda p: check(), baseline_fn=lambda: None)['skipped'] == 1
    assert _wait(runner)['done'] == 5
    runner.start(force=True, at_once=2, measure_fn=lambda p: check(), baseline_fn=lambda: None)
    assert _wait(runner)['done'] == 6


def test_stop_lets_running_checks_finish_and_starts_no_more(pool):
    started = []

    def measure(proxy):
        started.append(proxy['id'])
        time.sleep(0.1)
        return check()

    runner = proxy_check.CheckRun()
    runner.start(at_once=2, measure_fn=measure, baseline_fn=lambda: None)
    while len(started) < 2:
        time.sleep(0.01)
    runner.stop()
    status = _wait(runner)
    assert status['state'] == 'stopped' and len(started) == 2 and status['done'] == 2


def test_only_one_run_at_a_time(pool):
    runner = proxy_check.CheckRun()
    runner.start(at_once=1, measure_fn=lambda p: time.sleep(0.05) or check(), baseline_fn=lambda: None)
    with pytest.raises(RuntimeError):
        runner.start(measure_fn=lambda p: check(), baseline_fn=lambda: None)
    runner.stop()
    _wait(runner)


def test_a_failing_check_does_not_stop_the_run(pool):
    def measure(proxy):
        if proxy['id'] == 'p3':
            raise OSError('boom')
        return check()

    runner = proxy_check.CheckRun()
    runner.start(at_once=2, measure_fn=measure, baseline_fn=lambda: None)
    status = _wait(runner)
    assert status['done'] == 6 and status['grades']['good'] == 5 and status['error'] == 'boom'


def test_a_slow_proxy_that_works_stays_available(pool):
    proxy_check.save_check('p1', check(up=(0.3, 0.4)))            # slow, but working
    proxy_check.save_check('p2', check(down=(5.0,), up=()))       # upload timed out with nothing measured
    proxy_check.save_check('p3', check(reachable=False, down=(), up=()))
    current = pool()
    assert current['p1']['group'] == 'Available' and current['p1']['rating']['grade'] == 'slow'
    assert current['p2']['group'] == 'Poor proxy' and current['p2']['rating']['grade'] == 'timeout'
    assert current['p3']['group'] == 'Poor proxy'


def test_a_run_always_checks_poor_proxies_again(pool):
    proxy_check.save_check('p1', check())                                   # working: skipped
    proxy_check.save_check('p2', check(down=(5.0,), up=()))                 # timed out: checked again
    proxy_check.save_check('p3', check(reachable=False, down=(), up=()))    # dead: checked again
    runner = proxy_check.CheckRun()
    assert runner.start(at_once=2, measure_fn=lambda p: check(), baseline_fn=lambda: None)['skipped'] == 1
    _wait(runner)
    assert pool()['p2']['group'] == 'Available' and pool()['p3']['group'] == 'Available'
