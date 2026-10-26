"""Gemini access and source-grounding helpers shared by the writer and narration."""
import json
import re
import time
import unicodedata
from hashlib import sha256
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr


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


class NewsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr
    model: str = Field(pattern=r'^gemini-[a-zA-Z0-9.-]{1,80}$')
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


def provider_generate(request, client_factory=httpx.Client, *, system, schema):
    key = request.api_key.get_secret_value().strip()
    if not key:
        raise ValueError('Add your Gemini API key in Settings first.')
    context = request.provider_context() if hasattr(request, 'provider_context') else request.model_dump(exclude={'api_key', 'model'})
    payload = {'systemInstruction': {'parts': [{'text': system}]},
               'contents': [{'role': 'user', 'parts': [{'text': json.dumps(context, ensure_ascii=False)}]}],
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
                    raise ValueError(messages.get(response.status_code, 'Gemini could not generate this draft. Check your key/model or try again.'))
                data = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    data.extend(chunk)
                    if len(data) > 256000 or time.monotonic() - started > 65:
                        raise ValueError('The Gemini response exceeded the size or time limit. Try again.')
        body = json.loads(data)
        candidate = body.get('candidates', [{}])[0]
        if candidate.get('finishReason') not in (None, 'STOP'):
            raise ValueError('Gemini did not finish a usable news draft. Try clearer or shorter source text.')
        parts = candidate.get('content', {}).get('parts', [])
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
        return json.loads(text)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError('Gemini did not return a usable news draft. Check your model/key and try again.') from None
