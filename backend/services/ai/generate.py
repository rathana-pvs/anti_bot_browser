"""One JSON answer from whichever provider the request names."""


def generate_json(config, *, system, schema, context, prefix='', action, effort='low', usage=None, client_factory=None):
    """The answer as a dict shaped by `schema` (a Pydantic model the caller validates).

    `prefix` is stable text (an article) sent before `context`, so providers that cache can reuse it.
    `usage`, when given, is filled with input and output token counts. Nothing else of the call is kept.
    """
    common = dict(system=system, schema=schema, context=context, prefix=prefix, action=action, usage=usage)
    if config.provider == 'claude':
        from .claude import claude_generate
        return claude_generate(config, effort=effort, client_factory=client_factory, **common)
    if config.provider == 'openai':
        from .openai_compat import openai_generate
        return openai_generate(config, **common, **({'client_factory': client_factory} if client_factory else {}))
    from backend.services.reel_creator.news_ai import provider_generate
    return provider_generate(config, *([client_factory] if client_factory else []), **common)
