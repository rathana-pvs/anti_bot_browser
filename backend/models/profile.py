from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NetworkIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: Literal["direct", "pool", "custom"] = "direct"
    proxy_id: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str = ""
    password: str = ""

    @model_validator(mode="after")
    def validate_mode_fields(self):
        if self.mode == "pool" and not self.proxy_id:
            raise ValueError("proxy_id is required for pool mode")
        if self.mode == "custom" and (not self.host or not self.host.strip() or not self.port):
            raise ValueError("host and port are required for custom mode")
        return self


class RequestedEnvironmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    screen_resolution: str = Field(default="1920x1080", pattern=r"^\d{3,4}x\d{3,4}$")
    timezone_policy: Literal["host", "proxy", "manual"] = "host"
    timezone: str | None = None
    language: str = Field(default="en-US", min_length=2, max_length=32)
    user_agent_policy: Literal["browser_default"] = "browser_default"
    user_agent: None = None
    rendering_mode: Literal["host_gpu", "software"] = "host_gpu"

    @model_validator(mode="after")
    def validate_policy_fields(self):
        if self.timezone_policy == "manual" and not self.timezone:
            raise ValueError("timezone is required for manual timezone policy")
        return self


class ResourceLimitsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_limit: float = Field(default=4, ge=0.5, le=32)
    memory_mb: int = Field(default=4096, ge=512, le=131072)


class AutomationPreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reel_template: Literal["auto", "t1", "t2", "t3"] = "auto"


class ProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    group: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=120)
    network: NetworkIntent = Field(default_factory=NetworkIntent)
    requested_environment: RequestedEnvironmentInput = Field(default_factory=RequestedEnvironmentInput)
    resources: ResourceLimitsInput = Field(default_factory=ResourceLimitsInput)
    behavior_mode: Literal["fast", "medium", "slow"] = "medium"
    automation: AutomationPreferencesInput = Field(default_factory=AutomationPreferencesInput)
    account: dict[str, Any] | None = None


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    group: str | None = Field(default=None, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    network: NetworkIntent | None = None
    requested_environment: RequestedEnvironmentInput | None = None
    resources: ResourceLimitsInput | None = None
    behavior_mode: Literal["fast", "medium", "slow"] | None = None
    automation: AutomationPreferencesInput | None = None
    account: dict[str, Any] | None = None
