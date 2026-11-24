"""Which AI to ask, and with what key. Keys come with each request from the desktop settings and are never stored here."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .key_pool import MAX_KEYS, unique

Provider = Literal['gemini']
MODEL_PATTERN = r'gemini-[a-zA-Z0-9.-]{1,80}'


class AIConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Provider = 'gemini'
    api_key: SecretStr = SecretStr('')
    # Every saved key, used in turn when one hits a limit. `api_key` alone still works.
    api_keys: list[SecretStr] = Field(default_factory=list, max_length=MAX_KEYS)
    model: str = Field(max_length=120)

    @model_validator(mode='after')
    def gemini_model(self):
        if not re.fullmatch(MODEL_PATTERN, self.model):
            raise ValueError('Choose a Gemini model in Settings.')
        return self

    @property
    def name(self):
        return 'Gemini'

    def keys(self):
        """Every key this request may use, in Settings order, without blanks or repeats."""
        return keys_of(self)


def keys_of(request):
    """The keys a request carries: `api_keys` when given, otherwise its single `api_key`."""
    many = getattr(request, 'api_keys', None) or []
    return unique(many) or unique([getattr(request, 'api_key', '')])
