"""Provider-neutral AI access: request checks, Claude and OpenAI-compatible adapters, and the writer on top of them."""
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from backend.services.ai import AIConfig, generate_json


class Answer(BaseModel):
    words: str


def config(**values):
    return AIConfig(**{'provider': 'claude', 'api_key': 'test-key', 'model': 'claude-sonnet-5-5', **values})


def test_models_must_match_their_provider():
    assert config().model == 'claude-sonnet-5-5'
    assert AIConfig(model='gemini-3.8-flash', api_key='k').provider == 'gemini'
    with pytest.raises(ValidationError):
        config(model='gemini-3.8-flash')
    with pytest.raises(ValidationError):
        AIConfig(model='claude-opus-5-5', api_key='k')  # Gemini is the default provider
    with pytest.raises(ValidationError):
        config(base_url='https://example.com/v1')  # only an OpenAI-compatible provider takes an address


@pytest.mark.parametrize('url, ok', [
    ('', True), ('https://openrouter.ai/api/v1/', True), ('http://localhost:11434/v1', True), ('http://127.0.0.1:1234/v1', True),
    ('http://example.com/v1', False), ('ftp://localhost/v1', False), ('https://user:pass@example.com/v1', False),
    ('https://example.com/v1?key=1', False)])
def test_openai_addresses_are_https_or_this_computer(url, ok):
    values = dict(provider='openai', api_key='k', model='gpt-test', base_url=url)
    if ok:
        assert not AIConfig(**values).base_url.endswith('/')
    else:
        with pytest.raises(ValidationError):
            AIConfig(**values)


def test_a_local_model_needs_no_key_but_a_remote_one_does():
    assert AIConfig(provider='openai', model='llama3', base_url='http://localhost:11434/v1').key() == ''
    with pytest.raises(ValueError, match='API key'):
        AIConfig(provider='openai', model='gpt-test').key()
    with pytest.raises(ValueError, match='Claude API key'):
        config(api_key='').key()


class FakeClaude:
    def __init__(self, response=None, error=None):
        self.calls, self.response, self.error = [], response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def claude_reply(text, stop='end_turn'):
    usage = SimpleNamespace(input_tokens=120, output_tokens=40, cache_read_input_tokens=900, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type='thinking', thinking=''), SimpleNamespace(type='text', text=text)], usage=usage)


def test_claude_call_caches_the_prefix_and_asks_for_the_schema(caplog):
    fake, usage = FakeClaude(claude_reply('{"words": "hello"}')), {}
    with caplog.at_level('INFO'):
        result = generate_json(config(), system='SYSTEM', schema=Answer, context={'q': 1}, prefix='ARTICLE TEXT',
                               action='direct', usage=usage, client_factory=lambda key: fake)
    assert result == {'words': 'hello'}
    call = fake.calls[0]
    assert call['system'][0]['cache_control'] == {'type': 'ephemeral'}
    assert call['messages'][0]['content'][0] == {'type': 'text', 'text': 'ARTICLE TEXT', 'cache_control': {'type': 'ephemeral'}}
    assert json.loads(call['messages'][0]['content'][1]['text']) == {'q': 1}
    assert call['output_config']['format']['type'] == 'json_schema' and call['output_config']['effort'] == 'low'
    assert call['fallbacks'] == 'default' and call['betas'] == ['server-side-fallback-2026-07-01']
    assert usage == {'input': 1020, 'output': 40}
    assert 'ARTICLE TEXT' not in caplog.text and 'hello' not in caplog.text and 'cache_read=900' in caplog.text


def test_claude_models_without_server_fallback_are_sent_without_it():
    fake = FakeClaude(claude_reply('{"words": "x"}'))
    generate_json(config(model='claude-haiku-5-5'), system='S', schema=Answer, context={}, action='t', client_factory=lambda key: fake)
    assert 'fallbacks' not in fake.calls[0] and 'betas' not in fake.calls[0]


@pytest.mark.parametrize('stop, message', [('refusal', 'declined'), ('max_tokens', 'did not finish')])
def test_claude_stops_are_explained(stop, message):
    fake = FakeClaude(claude_reply('{}', stop))
    with pytest.raises(ValueError, match=message):
        generate_json(config(), system='S', schema=Answer, context={}, action='t', client_factory=lambda key: fake)


def test_claude_errors_are_plain_messages():
    import anthropic
    import httpx2
    request = httpx2.Request('POST', 'https://api.anthropic.com/v1/messages')
    cases = [(anthropic.AuthenticationError('bad', response=httpx2.Response(401, request=request), body=None), 'rejected the API key'),
             (anthropic.RateLimitError('slow', response=httpx2.Response(429, request=request), body=None), 'rate limit'),
             (anthropic.NotFoundError('no', response=httpx2.Response(404, request=request), body=None), 'unavailable'),
             (anthropic.APIConnectionError(request=request), 'Could not reach Claude')]
    for error, message in cases:
        with pytest.raises(ValueError, match=message):
            generate_json(config(), system='S', schema=Answer, context={}, action='t', client_factory=lambda key: FakeClaude(error=error))


def openai_client(status=200, body=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body or {})
    return lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k == 'timeout'})


def test_openai_compatible_call_sends_the_schema_and_reads_json():
    seen, usage = [], {}
    body = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"words": "hi"}'}}], 'usage': {'prompt_tokens': 50, 'completion_tokens': 5}}
    cfg = AIConfig(provider='openai', api_key='k', model='gpt-test', base_url='http://localhost:11434/v1')
    assert generate_json(cfg, system='S', schema=Answer, context={'a': 1}, prefix='P', action='t', usage=usage,
                         client_factory=openai_client(body=body, seen=seen)) == {'words': 'hi'}
    sent = json.loads(seen[0].content)
    assert str(seen[0].url) == 'http://localhost:11434/v1/chat/completions' and seen[0].headers['authorization'] == 'Bearer k'
    assert sent['response_format']['json_schema']['name'] == 'Answer' and sent['messages'][1]['content'].startswith('P\n\n')
    assert usage == {'input': 50, 'output': 5}


def test_openai_compatible_errors_are_plain_messages():
    cfg = AIConfig(provider='openai', api_key='k', model='gpt-test')
    with pytest.raises(ValueError, match='rejected the API key'):
        generate_json(cfg, system='S', schema=Answer, context={}, action='t', client_factory=openai_client(status=401))
    cut = {'choices': [{'finish_reason': 'length', 'message': {'content': '{"wo'}}]}
    with pytest.raises(ValueError, match='did not finish'):
        generate_json(cfg, system='S', schema=Answer, context={}, action='t', client_factory=openai_client(body=cut))


def test_the_writer_accepts_any_provider():
    from backend.services.reel_creator.writer import WriteRequest
    article = 'The city council voted 7 to 2 on Tuesday to approve a new flood relief plan covering three provinces.'
    request = WriteRequest(provider='claude', api_key='k', model='claude-opus-5-5', text=article)
    assert request.provider_context()['text'] == article
    with pytest.raises(ValidationError):
        WriteRequest(provider='claude', api_key='k', model='gemini-3.8-flash', text=article)
