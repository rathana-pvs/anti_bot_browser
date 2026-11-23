"""Provider-neutral AI access: one request shape and one JSON call, answered by Gemini."""
from .generate import generate_json
from .request import AIConfig

__all__ = ['AIConfig', 'generate_json']
