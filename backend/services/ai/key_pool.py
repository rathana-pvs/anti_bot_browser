"""Several Gemini keys, used in turn: a key that hits a limit rests and the same request moves on to the next one.

What each key is doing lives in memory only, under a short hash of the key; the keys themselves are never stored.
A restart forgets every rest, which is harmless: the first call that meets a limit rests that key again.
"""
import hashlib
import math
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MAX_KEYS = 20
RATE_REST = 60            # seconds, when Google gives no wait of its own
MAX_RATE_REST = 15 * 60
QUOTA_DAY = ZoneInfo('America/Los_Angeles')  # Gemini daily quotas reset at midnight Pacific time


class KeyLimited(ValueError):
    """This key cannot answer right now; another key may. `kind` is 'rate', 'quota' or 'rejected'."""

    def __init__(self, message, kind, retry_after=None):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def seconds_until_quota_reset(now=None):
    now = datetime.fromtimestamp(now if now is not None else time.time(), QUOTA_DAY)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), QUOTA_DAY)
    return max(60.0, (midnight - now).total_seconds())


def retry_seconds(body):
    """Google's own suggested wait, from RetryInfo.retryDelay ("37s" or "1.5s"), if the error carries one."""
    error = body.get('error') if isinstance(body, dict) else None
    for detail in (error or {}).get('details') or []:
        delay = detail.get('retryDelay') if isinstance(detail, dict) else None
        match = re.fullmatch(r'(\d+(?:\.\d+)?)s', str(delay or ''))
        if match:
            return float(match.group(1))
    return None


def is_daily(said, body):
    """A per-day quota is used up (not a per-minute rate limit)."""
    text = said.lower()
    if 'per day' in text or 'perday' in text or 'daily' in text:
        return True
    error = body.get('error') if isinstance(body, dict) else None
    for detail in (error or {}).get('details') or []:
        for violation in (detail.get('violations') or []) if isinstance(detail, dict) else []:
            if 'perday' in str(violation.get('quotaId', '')).lower():
                return True
    return False


def limit_for(status, said, body):
    """The KeyLimited a Gemini error means, or None when another key would not help (bad model, bad input, safety)."""
    if status == 429:
        if is_daily(said, body):
            return KeyLimited('This Gemini key used its daily quota.', 'quota')
        return KeyLimited('This Gemini key reached its rate limit.', 'rate', retry_seconds(body))
    if status in (401, 403) or (status == 400 and 'api key' in said.lower()):
        return KeyLimited('Gemini rejected this API key.', 'rejected')
    return None


class KeysExhausted(ValueError):
    """No key could answer: none was given, or every one is resting. The message says which, plainly."""


class KeyPool:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.state = {}  # fingerprint -> {'kind', 'until', 'used'}

    def rest(self, key, limited):
        now = self.clock()
        if limited.kind == 'rejected':
            until = math.inf
        elif limited.kind == 'quota':
            until = now + seconds_until_quota_reset(now)
        else:
            until = now + min(MAX_RATE_REST, max(1.0, limited.retry_after or RATE_REST))
        with self.lock:
            entry = self.state.setdefault(fingerprint(key), {'used': 0.0})
            entry.update(kind=limited.kind, until=until)

    def answered(self, key):
        with self.lock:
            self.state[fingerprint(key)] = {'kind': None, 'until': 0.0, 'used': self.clock()}

    def forget(self, keys):
        with self.lock:
            for key in keys:
                self.state.pop(fingerprint(key), None)

    def status(self, key):
        """{'state': 'ready'|'rate'|'quota'|'rejected', 'seconds_left': int|None} for one key."""
        now = self.clock()
        with self.lock:
            entry = self.state.get(fingerprint(key)) or {}
        if not entry.get('kind') or entry.get('until', 0) <= now:
            return {'state': 'ready', 'seconds_left': None}
        left = entry['until'] - now
        return {'state': entry['kind'], 'seconds_left': None if math.isinf(left) else math.ceil(left)}

    def order(self, keys):
        """The keys that may be tried now, least recently used first, and the soonest rest end among the others."""
        now = self.clock()
        ready, resting = [], []
        with self.lock:
            for index, key in enumerate(keys):
                entry = self.state.get(fingerprint(key)) or {}
                if entry.get('kind') and entry.get('until', 0) > now:
                    resting.append(entry)
                else:
                    ready.append((entry.get('used', 0.0), index, key))
        return [key for _, _, key in sorted(ready)], resting

    def run(self, keys, call):
        """call(key) with each usable key in turn until one answers. Errors that are not about the key end it at once."""
        keys = unique(keys)
        if not keys:
            raise KeysExhausted('Add your Gemini API key in Settings first.')
        ready, resting = self.order(keys)
        for key in ready:
            try:
                result = call(key)
            except KeyLimited as limited:
                self.rest(key, limited)
                resting.append(self.state.get(fingerprint(key), {}))
                continue
            self.answered(key)
            return result
        raise KeysExhausted(all_resting_message(len(keys), resting, self.clock()))


def unique(keys):
    seen, out = set(), []
    for key in keys:
        key = (key.get_secret_value() if hasattr(key, 'get_secret_value') else str(key or '')).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def wait_words(seconds):
    if seconds < 90:
        return f'{max(1, math.ceil(seconds))} s'
    if seconds < 90 * 60:
        return f'{math.ceil(seconds / 60)} min'
    return f'{seconds / 3600:.1f} h'


def all_resting_message(count, resting, now):
    keys = 'Your Gemini key is' if count == 1 else f'All {count} Gemini keys are'
    waiting = [entry for entry in resting if entry.get('kind') != 'rejected']
    if not waiting:
        return ('Gemini rejected the API key. Check it in Settings → AI models.' if count == 1
                else f'Gemini rejected all {count} API keys. Check them in Settings → AI models.')
    kinds = {entry['kind'] for entry in waiting}
    state = {frozenset({'rate'}): 'cooling down after a rate limit', frozenset({'quota'}): 'out of daily quota'}.get(
        frozenset(kinds), 'out of quota or cooling down')
    soonest = wait_words(min(entry['until'] for entry in waiting) - now)
    when = f'It is ready again in {soonest}.' if count == 1 else f'The soonest is ready in {soonest}.'
    return f'{keys} {state}. {when} You can add another key in Settings → AI models.'


POOL = KeyPool()
