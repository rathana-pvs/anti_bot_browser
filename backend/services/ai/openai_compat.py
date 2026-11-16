"""OpenAI-compatible chat completions (OpenAI, OpenRouter, Ollama, LM Studio) asked for JSON in a schema."""
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)
MAX_BYTES = 256000
MAX_SECONDS = 95  # a model on this computer can be slow


def openai_generate(config, *, system, schema, context, prefix='', action, usage=None, client_factory=httpx.Client, **_):
    key = config.key()
    user = (prefix + '\n\n' if prefix else '') + json.dumps(context, ensure_ascii=False)
    payload = {'model': config.model,
               'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
               'response_format': {'type': 'json_schema', 'json_schema': {'name': schema.__name__, 'schema': schema.model_json_schema()}}}
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    started = time.monotonic()
    try:
        with client_factory(timeout=httpx.Timeout(90, connect=10), trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', f'{config.base_url}/chat/completions', headers=headers, json=payload) as response:
                if response.status_code != 200:
                    messages = {401: 'The AI provider rejected the API key. Check Settings.',
                                403: 'The AI provider denied access. Check the API key and model in Settings.',
                                404: 'The AI provider has no such model or address. Check Settings.',
                                429: 'The AI provider quota or rate limit was reached. Wait or check your plan.'}
                    from backend.services.reel_creator.news_ai import provider_said
                    said = provider_said(response)
                    message = messages.get(response.status_code, f'The AI provider could not answer (error {response.status_code}).')
                    raise ValueError(f'{message} It said: {said}' if said else message)
                data = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES or time.monotonic() - started > MAX_SECONDS:
                        raise ValueError('The AI answer exceeded the size or time limit. Try again.')
        body = json.loads(data)
        counts = body.get('usage') or {}
        if usage is not None:
            usage.update(input=counts.get('prompt_tokens') or 0, output=counts.get('completion_tokens') or 0)
        logger.info('AI usage provider=openai action=%s model=%s input=%s output=%s', action, config.model,
                    counts.get('prompt_tokens'), counts.get('completion_tokens'))
        choice = body['choices'][0]
        if choice.get('finish_reason') not in (None, 'stop'):
            raise ValueError('The AI did not finish a usable answer. Try clearer or shorter source text.')
        return json.loads(choice['message']['content'])
    except httpx.ConnectError:
        raise ValueError('Could not reach the AI provider. Check its address in Settings.') from None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError('The AI did not return a usable answer. Check the model in Settings and try again.') from None
