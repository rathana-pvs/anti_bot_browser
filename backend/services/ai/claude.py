"""Claude through the official Anthropic SDK: one structured-output call, with the stable prefix cached."""
import json
import logging

logger = logging.getLogger(__name__)
# A policy decline is re-run on a suitable model inside the same call. Only these models take the "default" form.
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
FALLBACK_MODELS = frozenset({'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'})


def default_client(key):
    import anthropic
    return anthropic.Anthropic(api_key=key, timeout=anthropic.Timeout(120, connect=10), max_retries=1)


def log_usage(action, model, usage, out=None):
    """Counts only: never the prompt, the answer or the key."""
    counts = dict(input=getattr(usage, 'input_tokens', 0) or 0, output=getattr(usage, 'output_tokens', 0) or 0,
                  cache_read=getattr(usage, 'cache_read_input_tokens', 0) or 0,
                  cache_write=getattr(usage, 'cache_creation_input_tokens', 0) or 0)
    if out is not None:
        out.update(input=counts['input'] + counts['cache_read'] + counts['cache_write'], output=counts['output'])
    logger.info('Claude usage action=%s model=%s input=%s cache_read=%s cache_write=%s output=%s', action, model,
                counts['input'], counts['cache_read'], counts['cache_write'], counts['output'])


def claude_generate(config, *, system, schema, context, prefix='', action, effort='low', max_tokens=16000, usage=None, client_factory=None):
    import anthropic
    client = (client_factory or default_client)(config.key())
    user = ([{'type': 'text', 'text': prefix, 'cache_control': {'type': 'ephemeral'}}] if prefix else []) + \
        [{'type': 'text', 'text': json.dumps(context, ensure_ascii=False)}]
    extra = dict(betas=[FALLBACK_BETA], fallbacks='default') if config.model in FALLBACK_MODELS else {}
    try:
        response = client.beta.messages.create(
            model=config.model, max_tokens=max_tokens,
            system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
            messages=[{'role': 'user', 'content': user}],
            output_config={'effort': effort, 'format': {'type': 'json_schema', 'schema': anthropic.transform_schema(schema.model_json_schema())}},
            **extra)
    except anthropic.AuthenticationError:
        raise ValueError('Claude rejected the API key. Check Settings.') from None
    except anthropic.PermissionDeniedError:
        raise ValueError('Claude access was denied. Check the API key and model in Settings.') from None
    except anthropic.NotFoundError:
        raise ValueError('The selected Claude model is unavailable. Choose an available model in Settings.') from None
    except anthropic.RateLimitError:
        raise ValueError('Claude quota or rate limit reached. Wait or check your API plan.') from None
    except anthropic.BadRequestError as exc:
        said = ' '.join(str(getattr(exc, 'message', '')).split())[:240]
        raise ValueError(f'Claude could not take this request. Claude said: {said}' if said else 'Claude could not take this request.') from None
    except anthropic.APIStatusError:
        raise ValueError('Claude could not answer right now. Try again.') from None
    except anthropic.APIConnectionError:
        raise ValueError('Could not reach Claude. Check the internet connection and try again.') from None
    log_usage(action, config.model, response.usage, usage)
    if response.stop_reason == 'refusal':
        raise ValueError('Claude declined to write this. Try other source text.')
    if response.stop_reason == 'max_tokens':
        raise ValueError('Claude did not finish a usable answer. Try clearer or shorter source text.')
    text = ''.join(block.text for block in response.content if block.type == 'text')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ValueError('Claude did not return a usable answer. Try again.') from None
