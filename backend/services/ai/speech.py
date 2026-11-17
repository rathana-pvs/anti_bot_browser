"""Voiceover through Gemini text-to-speech (the Interactions API), as WAV bytes.

Docs: https://aistudio.google.com/docs/speech-generation (checked 2026-11-16). The reply carries base64 audio in
steps[].content[] of type "audio"; unary replies are WAV (24 kHz, mono, 16-bit) unless asked otherwise.
"""
import base64
import io
import json
import logging
import time
import wave

import httpx

logger = logging.getLogger(__name__)
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/interactions'
MODELS = ('gemini-3.8-flash-lite-tts', 'gemini-3.8-flash-tts')
DEFAULT_MODEL = MODELS[0]
VOICES = ('Kore', 'Puck', 'Charon', 'Aoede', 'Fenrir', 'Leda', 'Orus', 'Zephyr', 'Schedar', 'Sulafat')
MAX_TEXT = 600         # characters per scene's narration
MAX_BYTES = 12_000_000
SAMPLE_RATE = 24000


def pcm_to_wav(pcm, rate=SAMPLE_RATE):
    out = io.BytesIO()
    with wave.open(out, 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm)
    return out.getvalue()


def wav_seconds(data):
    with wave.open(io.BytesIO(data)) as file:
        return file.getnframes() / file.getframerate()


def synthesize(config, text, voice='Kore', model=DEFAULT_MODEL, style='', client_factory=httpx.Client):
    """The narration as WAV bytes. English only for now. Raises ValueError with a plain message."""
    if config.provider != 'gemini':
        raise ValueError('Voiceover needs a Gemini key for now. Add one in Settings → AI models.')
    if model not in MODELS:
        raise ValueError('Choose an available voice model.')
    if voice not in VOICES:
        raise ValueError('Choose one of the available voices.')
    text = ' '.join(text.split())
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f'Narration must be 1 to {MAX_TEXT} characters per scene.')
    content = {'type': 'text', 'text': text}
    if style:
        content['annotations'] = [{'type': 'speech_metadata', 'style': style[:80]}]
    payload = {'model': model, 'input': [{'type': 'user_input', 'content': [content]}], 'response_format': {'type': 'audio'},
               'generation_config': {'speech_config': [{'voice': voice}]}}
    started = time.monotonic()
    try:
        with client_factory(timeout=httpx.Timeout(90, connect=10), trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', ENDPOINT, headers={'x-goog-api-key': config.key()}, json=payload) as response:
                if response.status_code != 200:
                    from backend.services.reel_creator.news_ai import provider_said
                    said = provider_said(response)
                    messages = {401: 'Gemini rejected the API key. Check Settings.', 403: 'Gemini access to voices was denied. Check the key.',
                                404: 'The voice model is unavailable for this key.', 429: 'Gemini quota or rate limit reached. Wait and try again.'}
                    if response.status_code == 400 and 'api key' in said.lower():
                        raise ValueError(messages[401])
                    message = messages.get(response.status_code, f'Gemini could not make the voiceover (error {response.status_code}).')
                    raise ValueError(f'{message} Gemini said: {said}' if said else message)
                data = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES or time.monotonic() - started > 120:
                        raise ValueError('The voiceover was too large or too slow. Try shorter text.')
        body = json.loads(data)
        audio = [c for step in body.get('steps', []) if step.get('type') == 'model_output'
                 for c in step.get('content', []) if c.get('type') == 'audio' and c.get('data')]
        if not audio:
            raise ValueError('Gemini returned no audio. Try again.')
        raw = base64.b64decode(audio[-1]['data'])
        wav = raw if raw[:4] == b'RIFF' else pcm_to_wav(raw)
        seconds = wav_seconds(wav)
    except (httpx.HTTPError, KeyError, TypeError, AttributeError, json.JSONDecodeError, wave.Error, ValueError) as exc:
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            raise
        raise ValueError('Gemini did not return usable audio. Try again.') from None
    logger.info('Voice usage model=%s characters=%s seconds=%.1f', model, len(text), seconds)  # counts only, never the text
    return wav
