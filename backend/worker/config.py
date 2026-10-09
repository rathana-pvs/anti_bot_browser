"""Explicit, local administrator-owned worker configuration."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from .protocol import StrictModel


class WorkerConfig(StrictModel):
    server_url: str
    worker_id: str = Field(min_length=1, max_length=128)
    credential: SecretStr
    account_profiles: dict[str, str] = Field(default_factory=dict)
    transfer_hosts: list[str] = Field(default_factory=list)
    heartbeat_seconds: float = Field(default=15, ge=1, le=60)
    acknowledgement_timeout: float = Field(default=45, ge=5, le=120)
    max_accepted_jobs: int = Field(default=1, ge=1, le=6)
    allow_loopback_development: bool = False

    @field_validator("account_profiles")
    @classmethod
    def safe_profiles(cls, value):
        for profile in value.values():
            if not profile or profile in (".", "..") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in profile):
                raise ValueError("Profile IDs must be safe directory identifiers")
        if len(set(value.values())) != len(value):
            raise ValueError("Each profile can map to only one cloud account")
        return value

    @field_validator("credential")
    @classmethod
    def credential_present(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("Worker credential is empty")
        return value

    def validate_connection(self):
        url = urlsplit(self.server_url)
        local = self.allow_loopback_development and url.hostname in ("127.0.0.1", "localhost", "::1")
        if url.scheme != "wss" and not (local and url.scheme == "ws"):
            raise ValueError("Worker coordination requires wss:// (loopback ws:// only in development)")
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Server URL must not contain credentials, query parameters, or fragments")
        return self

    @classmethod
    def load(cls, path: Path):
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Prefer a secret provided by the service environment to one in a file.
        if os.environ.get("WORKER_CREDENTIAL"):
            raw["credential"] = os.environ["WORKER_CREDENTIAL"]
        return cls.model_validate(raw).validate_connection()
