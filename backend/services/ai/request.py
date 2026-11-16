"""Which AI to ask, and with what key. Keys come with each request from the desktop settings and are never stored here."""
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Provider = Literal['gemini', 'claude', 'openai']
MODEL_PATTERNS = {
    'gemini': r'gemini-[a-zA-Z0-9.-]{1,80}',
    'claude': r'claude-[a-z0-9-]{1,80}',
    'openai': r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,119}',
}
NAMES = {'gemini': 'Gemini', 'claude': 'Claude', 'openai': 'The AI provider'}
OPENAI_DEFAULT_URL = 'https://api.openai.com/v1'
LOOPBACK_NAMES = ('localhost',)


def loopback(host):
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def checked_base_url(value):
    """An OpenAI-compatible endpoint: https anywhere, plain http only on this computer (Ollama, LM Studio)."""
    url = (value or OPENAI_DEFAULT_URL).strip().rstrip('/')
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if parts.scheme not in ('https', 'http') or not host or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Enter the provider address as https://host/v1, or http://localhost:port/v1 for a local model.')
    if parts.scheme == 'http' and not loopback(host):
        raise ValueError('Plain http is only allowed for a model running on this computer. Use https for other addresses.')
    return url


class AIConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Provider = 'gemini'
    api_key: SecretStr = SecretStr('')
    model: str = Field(max_length=120)
    # OpenAI-compatible providers only.
    base_url: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def provider_fields(self):
        if not re.fullmatch(MODEL_PATTERNS[self.provider], self.model):
            raise ValueError(f'Choose a {NAMES[self.provider].removeprefix("The ")} model in Settings.')
        if self.provider == 'openai':
            self.base_url = checked_base_url(self.base_url)
        elif self.base_url:
            raise ValueError('Only an OpenAI-compatible provider takes an address.')
        return self

    @property
    def name(self):
        return NAMES[self.provider]

    def key(self):
        """The API key, or a plain message when it is missing. A local model may run without one."""
        key = self.api_key.get_secret_value().strip()
        if not key and not (self.provider == 'openai' and loopback(urlsplit(self.base_url).hostname or '')):
            raise ValueError(f'Add your {self.name.removeprefix("The ")} API key in Settings first.')
        return key
