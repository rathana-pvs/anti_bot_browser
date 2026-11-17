"""One JSON answer from whichever provider the request names."""
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5_000_000


def image_type(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    raise ValueError('Images for the AI must be PNG or JPEG.')


def checked_images(images):
    """(mime type, bytes) for each image, within the limits every provider accepts."""
    images = list(images or ())
    if len(images) > MAX_IMAGES or sum(len(i) for i in images) > MAX_IMAGE_BYTES:
        raise ValueError(f'Send at most {MAX_IMAGES} images, 5 MB in all, to the AI.')
    return [(image_type(data), data) for data in images]



def generate_json(config, *, system, schema, context, prefix='', action, effort='low', usage=None, client_factory=None, images=()):
    """The answer as a dict shaped by `schema` (a Pydantic model the caller validates).

    `prefix` is stable text (an article) sent before `context`, so providers that cache can reuse it.
    `usage`, when given, is filled with input and output token counts. Nothing else of the call is kept.
    """
    images = checked_images(images)
    common = dict(system=system, schema=schema, context=context, prefix=prefix, action=action, usage=usage, **({'images': images} if images else {}))
    if config.provider == 'claude':
        from .claude import claude_generate
        return claude_generate(config, effort=effort, client_factory=client_factory, **common)
    if config.provider == 'openai':
        from .openai_compat import openai_generate
        return openai_generate(config, **common, **({'client_factory': client_factory} if client_factory else {}))
    from backend.services.reel_creator.news_ai import provider_generate
    return provider_generate(config, *([client_factory] if client_factory else []), **common)
