"""Provider-neutral AI access: one request shape and one JSON call for Gemini, Claude and OpenAI-compatible APIs."""
from .generate import generate_json
from .request import AIConfig

__all__ = ['AIConfig', 'generate_json']
