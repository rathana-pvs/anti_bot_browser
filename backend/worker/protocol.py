"""Version 1 worker messages. No arbitrary local paths or execution arguments."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL_VERSION = 1


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def envelope(kind: str, payload: dict, **fields) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "type": kind,
            "message_id": uuid4().hex, "payload": payload, **fields}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Media(StrictModel):
    asset_id: str = Field(min_length=1, max_length=128)
    download_url: str = Field(min_length=1, max_length=8192)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=1024 * 1024 * 1024)
    extension: Literal[".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"]


class Job(StrictModel):
    account_id: str = Field(min_length=1, max_length=128)
    task: Literal["photo", "reel"]
    caption: str = Field(default="", max_length=20000)
    first_comment: str | None = Field(default=None, max_length=8000)
    media: list[Media] = Field(min_length=1, max_length=1)

    @field_validator("media")
    @classmethod
    def unique_assets(cls, value):
        if len({asset.asset_id for asset in value}) != len(value):
            raise ValueError("Duplicate media asset IDs")
        return value


class Message(StrictModel):
    protocol_version: Literal[1]
    type: str = Field(min_length=1, max_length=64)
    message_id: str = Field(min_length=1, max_length=128)
    reply_to: str | None = Field(default=None, max_length=128)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=128)
    lease_token: str | None = Field(default=None, min_length=1, max_length=256)
    payload: dict = Field(default_factory=dict)

    def attempt(self):
        if not self.attempt_id or not self.lease_token:
            raise ValueError("Attempt identity and lease token required")
        return self.attempt_id, self.lease_token
