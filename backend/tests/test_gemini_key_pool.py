"""Several Gemini keys: a limited key rests and the same request moves on to the next one."""

import httpx
import pytest
from pydantic import BaseModel

from backend.services.ai import AIConfig, generate_json
from backend.services.ai.key_pool import POOL, KeyLimited, KeyPool, KeysExhausted, seconds_until_quota_reset


class Answer(BaseModel):
    words: str


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def gemini(replies, seen):
    """A fake Gemini that answers each key with its own (status, body)."""
    def factory(**kwargs):
        def handler(request):
            key = request.headers['x-goog-api-key']
            seen.append(key)
            status, body = replies[key]
            return httpx.Response(status, json=body)
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=kwargs.get('timeout'))
    return factory


OK = (200, {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"words": "ok"}'}]}}]})
RATE = (429, {'error': {'message': 'Resource has been exhausted (e.g. check quota).',
                        'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '37s'}]}})
DAILY = (429, {'error': {'message': 'Quota exceeded for metric: generate_content_free_tier_requests, limit: 250',
                         'details': [{'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}})
REJECTED = (400, {'error': {'message': 'API key not valid. Please pass a valid API key.'}})
BAD_MODEL = (404, {'error': {'message': 'models/gemini-x is not found.'}})


def ask(keys, replies, seen):
    config = AIConfig(model='gemini-3.8-flash', api_keys=keys)
    return generate_json(config, system='S', schema=Answer, context={}, action='t', client_factory=gemini(replies, seen))


def test_a_rate_limited_key_hands_the_same_request_to_the_next():
    seen = []
    assert ask(['a', 'b'], {'a': RATE, 'b': OK}, seen) == {'words': 'ok'}
    assert seen == ['a', 'b']
    assert POOL.status('a')['state'] == 'rate' and 30 <= POOL.status('a')['seconds_left'] <= 37
    assert POOL.status('b') == {'state': 'ready', 'seconds_left': None}
    seen.clear()
    ask(['a', 'b'], {'a': OK, 'b': OK}, seen)
    assert seen == ['b']  # the resting key is not asked again


def test_daily_quota_and_rejected_keys_rest_longer():
    seen = []
    ask(['a', 'b', 'c'], {'a': DAILY, 'b': REJECTED, 'c': OK}, seen)
    assert POOL.status('a')['state'] == 'quota' and POOL.status('a')['seconds_left'] > 60
    assert POOL.status('b') == {'state': 'rejected', 'seconds_left': None}


def test_errors_another_key_would_not_fix_are_not_retried():
    seen = []
    with pytest.raises(ValueError, match='model is unavailable'):
        ask(['a', 'b'], {'a': BAD_MODEL, 'b': OK}, seen)
    assert seen == ['a'] and POOL.status('a')['state'] == 'ready'


def test_when_every_key_rests_the_message_says_when_one_is_back():
    seen = []
    with pytest.raises(KeysExhausted, match=r'All 2 Gemini keys are cooling down after a rate limit\. The soonest is ready in 3\d s'):
        ask(['a', 'b'], {'a': RATE, 'b': RATE}, seen)
    with pytest.raises(KeysExhausted, match='All 2 Gemini keys'):
        ask(['a', 'b'], {'a': OK, 'b': OK}, seen)
    assert seen == ['a', 'b']  # resting keys are not called at all
    with pytest.raises(KeysExhausted, match='Gemini rejected the API key'):
        ask(['z'], {'z': REJECTED}, [])
    with pytest.raises(KeysExhausted, match='Add your Gemini API key'):
        ask([], {}, [])


def test_keys_take_turns_and_rests_end():
    clock = Clock()
    pool = KeyPool(clock)
    used = []
    for _ in range(4):
        clock.now += 1
        pool.run(['a', 'b'], lambda key: used.append(key))
    assert used == ['a', 'b', 'a', 'b']
    pool.rest('a', KeyLimited('x', 'rate', 10))
    assert pool.status('a') == {'state': 'rate', 'seconds_left': 10}
    clock.now += 11
    assert pool.status('a')['state'] == 'ready'
    pool.rest('a', KeyLimited('x', 'rejected'))
    pool.forget(['a'])  # a Test clears it
    assert pool.status('a')['state'] == 'ready'


def test_quota_rests_until_midnight_pacific():
    # 2026-11-22 23:00 Pacific (PST, UTC-8) is 07:00 UTC the next day: one hour to go.
    from datetime import datetime, timezone
    now = datetime(2026, 11, 23, 7, 0, tzinfo=timezone.utc).timestamp()
    assert seconds_until_quota_reset(now) == 3600


def test_voiceover_moves_to_the_next_key_too():
    from backend.services.ai.speech import synthesize
    import base64
    seen = []
    audio = (200, {'steps': [{'type': 'model_output', 'content': [{'type': 'audio', 'data': base64.b64encode(b'\0\0' * 2400).decode()}]}]})
    wav = synthesize(AIConfig(model='gemini-3.8-flash', api_keys=['a', 'b']), 'Hi.', client_factory=gemini({'a': RATE, 'b': audio}, seen))
    assert wav[:4] == b'RIFF' and seen == ['a', 'b']


def test_key_status_route_and_test_clears_a_rest():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import reel_creator
    app = FastAPI()
    app.include_router(reel_creator.router)
    POOL.rest('a', KeyLimited('x', 'rejected'))
    body = TestClient(app).post('/api/reel-creator/ai-keys/status', json={'api_keys': ['a', 'b']}).json()
    assert [k['state'] for k in body['keys']] == ['rejected', 'ready']
