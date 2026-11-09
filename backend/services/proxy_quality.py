"""Automatic proxy folders, independent of profile and UI code."""
import math

FOLDERS = ('Used', 'Available', 'Poor proxy')
MIN_SPEED_MBPS = 1.0

# Two samples in one direction that differ by more than this make the proxy unstable.
STABLE_RATIO = 0.6
# good:    download and upload both got through, both at least MIN_SPEED_MBPS
# slow:    both got through, at least one under MIN_SPEED_MBPS (shown in yellow, still Available)
# timeout: one direction got nothing through
# dead:    the proxy cannot be reached or carries no traffic
GRADES = ('good', 'slow', 'timeout', 'dead')


def _mbps(samples):
    return [s['mbps'] for s in samples or [] if isinstance(s, dict) and isinstance(s.get('mbps'), (int, float))
            and not isinstance(s.get('mbps'), bool) and math.isfinite(s['mbps']) and s['mbps'] >= 0]


def _steady(values):
    return len(values) < 2 or min(values) >= STABLE_RATIO * max(values)


def rate_check(check):
    """Rates a stored proxy check. Only a timeout or an unreachable proxy is poor; speed only
    decides whether it is marked slow."""
    if not isinstance(check, dict) or not check.get('reachable'):
        return {'grade': 'dead', 'stable': None, 'download_mbps': None, 'upload_mbps': None}
    down, up = _mbps(check.get('download')), _mbps(check.get('upload'))
    down_typical = round(sum(down) / len(down), 2) if down else None
    up_typical = round(sum(up) / len(up), 2) if up else None
    if not down and not up:
        grade = 'dead' if not check.get('facebook_ok') else 'timeout'
    elif not down or not up:
        grade = 'timeout'
    elif min(down_typical, up_typical) < MIN_SPEED_MBPS:
        grade = 'slow'
    else:
        grade = 'good'
    return {'grade': grade, 'stable': _steady(down) and _steady(up),
            'download_mbps': down_typical, 'upload_mbps': up_typical}


def speed_quality(result):
    """Quality from an older single speed test: any measured speed both ways is good; a direction
    without a measurement timed out."""
    if not isinstance(result, dict):
        return 'untested'
    values = []
    for direction in ('download', 'upload'):
        sample = result.get(direction) or {}
        value = sample.get('mbps')
        values.append(sample.get('success') is True and isinstance(value, (int, float))
                      and not isinstance(value, bool) and math.isfinite(value) and value >= 0)
    return 'good' if all(values) else 'check_failed'


def normalize_holders(proxy):
    """`profile_ids` lists the accounts using the proxy; `profile_id` and `assigned` follow it.

    Pools saved before sharing hold a single `profile_id`, which becomes the only holder."""
    ids = proxy.get('profile_ids')
    if not isinstance(ids, list):
        ids = [proxy['profile_id']] if proxy.get('profile_id') else []
    ids = list(dict.fromkeys(pid for pid in ids if isinstance(pid, str) and pid))
    proxy['profile_ids'] = ids
    proxy['profile_id'] = ids[0] if ids else None
    proxy['assigned'] = bool(ids)
    return proxy


def is_poor(proxy):
    """A proxy that timed out, cannot be reached or was replaced, whichever folder it is in: one
    with accounts stays in Used, but no further account should join it."""
    return proxy.get('speed_quality') == 'check_failed' or bool(proxy.get('replaced_by'))


def normalize_proxy(proxy):
    normalize_holders(proxy)
    if isinstance(proxy.get('check'), dict):
        rating = rate_check(proxy['check'])
        proxy['rating'] = rating
        quality = 'check_failed' if rating['grade'] in ('timeout', 'dead') else 'good'
    else:
        proxy.pop('rating', None)
        quality = speed_quality(proxy.get('speed_test'))
    proxy['speed_quality'] = quality
    proxy.pop('last_speed_quality', None)
    # Poor proxy holds only proxies that time out or cannot be reached.
    # A proxy Webshare replaced no longer works, whatever its last check said.
    group = ('Used' if proxy['profile_ids'] else
             'Poor proxy' if quality == 'check_failed' or proxy.get('replaced_by') else 'Available')
    old = proxy.get('group')
    if old and old.casefold() not in {name.casefold() for name in FOLDERS}:
        proxy.setdefault('legacy_group', old)
    proxy['group'] = group
    return proxy
