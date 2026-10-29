"""Automatic proxy folders, independent of profile and UI code."""
import math

FOLDERS = ('Used', 'Available', 'Poor proxy')
MIN_SPEED_MBPS = 1.0


def speed_quality(result):
    if not isinstance(result, dict):
        return 'untested'
    values = []
    for direction in ('download', 'upload'):
        sample = result.get(direction) or {}
        value = sample.get('mbps')
        valid = (sample.get('success') is True and isinstance(value, (int, float))
                 and not isinstance(value, bool) and math.isfinite(value) and value >= 0)
        if valid and value < MIN_SPEED_MBPS:
            return 'poor'
        values.append(valid)
    return 'good' if all(values) else 'check_failed'


def normalize_proxy(proxy):
    quality = speed_quality(proxy.get('speed_test'))
    proxy['speed_quality'] = quality
    # An inconclusive check cannot promote a previously slow proxy. A complete
    # passing retest is required to bring it back into Available.
    if quality in ('good', 'poor'):
        proxy['last_speed_quality'] = quality
    group = ('Used' if proxy.get('assigned') or proxy.get('profile_id') or proxy.get('phone_profile_id') else
             'Poor proxy' if quality == 'check_failed' or proxy.get('last_speed_quality') == 'poor' else 'Available')
    old = proxy.get('group')
    if old and old.casefold() not in {name.casefold() for name in FOLDERS}:
        proxy.setdefault('legacy_group', old)
    proxy['group'] = group
    return proxy
