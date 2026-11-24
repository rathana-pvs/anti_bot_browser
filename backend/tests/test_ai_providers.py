"""AI access is Gemini only: request checks and the writer's request shape."""
import pytest
from pydantic import BaseModel, ValidationError

from backend.services.ai import AIConfig


class Answer(BaseModel):
    words: str


def test_only_gemini_models_are_accepted():
    assert AIConfig(model='gemini-3.8-flash', api_key='k').provider == 'gemini'
    for values in (dict(model='claude-opus-5-5'), dict(provider='claude', model='claude-sonnet-5-5'),
                   dict(provider='openai', model='gpt-test'), dict(model='gemini-3.8-flash', base_url='https://example.com/v1')):
        with pytest.raises(ValidationError):
            AIConfig(api_key='k', **values)


def test_keys_come_from_the_list_or_the_single_key():
    assert AIConfig(model='gemini-3.8-flash').keys() == []
    assert AIConfig(model='gemini-3.8-flash', api_key=' one ').keys() == ['one']
    assert AIConfig(model='gemini-3.8-flash', api_key='one', api_keys=['two', ' ', 'two', 'three']).keys() == ['two', 'three']


def test_the_writer_takes_a_gemini_request():
    from backend.services.reel_creator.writer import WriteRequest
    article = 'The city council voted 7 to 2 on Tuesday to approve a new flood relief plan covering three provinces.'
    request = WriteRequest(api_key='k', model='gemini-3.8-flash', text=article)
    assert request.provider_context()['text'] == article
    with pytest.raises(ValidationError):
        WriteRequest(api_key='k', model='claude-opus-5-5', text=article)
