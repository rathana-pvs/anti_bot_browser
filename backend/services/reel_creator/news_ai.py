"""Gemini access and source-grounding helpers shared by the writer and narration."""
import json
import logging
import re
import time
import unicodedata
from hashlib import sha256
from typing import Literal

import httpx
from pydantic import ConfigDict, Field, SecretStr

from backend.services.ai.key_pool import POOL, limit_for
from backend.services.ai.request import AIConfig, keys_of

logger = logging.getLogger(__name__)
# Short writing (captions, hooks) needs the top of an article, not all of it. Checks still read what they were given.
SHORT_COPY_SOURCE_CHARS = 4000


def log_usage(action, model, body, out=None):
    """What one Gemini call cost, by action. Counts only: never the prompt, the answer or the key."""
    usage = body.get('usageMetadata') or {}
    if out is not None:
        out.update(input=usage.get('promptTokenCount') or 0, output=(usage.get('candidatesTokenCount') or 0) + (usage.get('thoughtsTokenCount') or 0))
    logger.info('Gemini usage action=%s model=%s input=%s output=%s thinking=%s total=%s', action, model, usage.get('promptTokenCount'),
                usage.get('candidatesTokenCount'), usage.get('thoughtsTokenCount'), usage.get('totalTokenCount'))


def normalize_hashtags(value):
    """Cosmetic provider formatting must not discard a usable news draft."""
    if isinstance(value,str):value=re.split(r',|\s+(?=[#＃])',value)
    if not isinstance(value,list):return []
    result=[];seen=set()
    for item in value:
        if not isinstance(item,str):continue
        text=unicodedata.normalize('NFKC',item).strip().lstrip('#').rstrip('.,;:!?')
        if re.search(r'://|www\.',text,re.I):continue
        text=re.sub(r'[\s-]+','_',text).strip('_')
        if not text or len(text)>59 or not any(unicodedata.category(c)[0] in ('L','N') for c in text):continue
        if any(c!='_' and unicodedata.category(c)[0] not in ('L','M','N') for c in text):continue
        tag='#'+text
        if tag.casefold() not in seen:result.append(tag);seen.add(tag.casefold())
        if len(result)==5:break
    return result


class NewsRequest(AIConfig):
    """An article to write from, and the AI to write with (Gemini unless the request names another provider)."""
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=80, max_length=120000)
    source_url: str = Field(default='', max_length=2048)
    source_title: str = Field(default='', max_length=300)
    publisher: str = Field(default='', max_length=100)
    language: Literal['English'] = 'English'


def source_fingerprint(text, source_url, language):
    return sha256(json.dumps([text, source_url, language], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def numbers(value):
    value = ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in value)
    return set(re.findall(r'\d+(?:[.,]\d+)*', value))


def provider_error(response, limit=240):
    """(the provider's own short error text, the whole error body) for an error response. Key-shaped text is removed."""
    try:
        body = json.loads(response.read()[:8000])
        error = body.get('error', {})
        text = error.get('message', '') if isinstance(error, dict) else str(error)
    except (ValueError, AttributeError, httpx.HTTPError):
        return '', {}
    text = re.sub(r'AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,}', '[key]', ' '.join(str(text).split()))
    return text[:limit].rstrip() + ('…' if len(text) > limit else ''), body


def provider_said(response, limit=240):
    """The provider's own short error text, for the operator. Anything shaped like a key is removed."""
    return provider_error(response, limit)[0]


def key_limit(response):
    """Raise KeyLimited when this error is about the key (rate limit, quota, rejected), so the next key is tried.
    Returns Google's own words otherwise."""
    said, body = provider_error(response)
    limited = limit_for(response.status_code, said, body)
    if limited:
        raise type(limited)(f'{limited} Gemini said: {said}' if said else str(limited), limited.kind, limited.retry_after)
    return said


def provider_generate(request, client_factory=httpx.Client, *, system, schema, action='news', context=None, prefix='', usage=None, images=()):
    """One Gemini call that answers in `schema`, with each of the request's keys in turn until one is not limited.
    `prefix` is stable text sent before the per-call `context`."""
    return POOL.run(keys_of(request), lambda key: gemini_answer(request, key, client_factory, system=system, schema=schema, action=action,
                                                                 context=context, prefix=prefix, usage=usage, images=images))


def gemini_answer(request, key, client_factory, *, system, schema, action, context, prefix, usage, images):
    if context is None:
        context = request.provider_context() if hasattr(request, 'provider_context') else request.model_dump(exclude={'api_key', 'api_keys', 'model', 'provider'})
    import base64
    pictures = [{'inlineData': {'mimeType': mime, 'data': base64.b64encode(data).decode()}} for mime, data in images]
    parts = ([{'text': prefix}] if prefix else []) + pictures + [{'text': json.dumps(context, ensure_ascii=False)}]
    payload = {'systemInstruction': {'parts': [{'text': system}]},
               'contents': [{'role': 'user', 'parts': parts}],
               'generationConfig': {'responseMimeType': 'application/json',
                                    'responseJsonSchema': schema.model_json_schema(),
                                    'maxOutputTokens': 5000}}
    started = time.monotonic()
    try:
        with client_factory(timeout=httpx.Timeout(55, connect=10), trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', f'https://generativelanguage.googleapis.com/v1beta/models/{request.model}:generateContent',
                               headers={'x-goog-api-key': key}, json=payload) as response:
                if response.status_code != 200:
                    messages = {401: 'Gemini rejected the API key. Check Settings.',
                                403: 'Gemini access was denied. Check the API key and model in Settings.',
                                404: 'The selected Gemini model is unavailable. Choose an available model in Settings.',
                                429: 'Gemini quota or rate limit reached. Wait or check your API quota.'}
                    said = key_limit(response)
                    message = messages.get(response.status_code, f'Gemini could not answer (error {response.status_code}).')
                    raise ValueError(f'{message} Gemini said: {said}' if said else message)
                data = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    data.extend(chunk)
                    if len(data) > 256000 or time.monotonic() - started > 65:
                        raise ValueError('The Gemini response exceeded the size or time limit. Try again.')
        body = json.loads(data)
        log_usage(action, request.model, body, usage)
        candidate = body.get('candidates', [{}])[0]
        reason = candidate.get('finishReason') or (body.get('promptFeedback') or {}).get('blockReason')
        if reason not in (None, 'STOP'):
            hint = {'MAX_TOKENS': 'It ran out of room for its answer; try a shorter article or another model.',
                    'SAFETY': 'It was stopped by its safety filter.'}.get(reason, f'It stopped early ({reason}).')
            raise ValueError(f'Gemini did not finish a usable answer. {hint}')
        parts = candidate.get('content', {}).get('parts', [])
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
        return json.loads(text)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError('Gemini did not return a usable answer. Check your model and key in Settings, then try again.') from None
