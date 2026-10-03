"""Contracts shared by modules in the fixed automation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


SUCCESS = "success"
SKIPPED = "skipped"
FAILED_SAFE = "failed_safe"
UNCERTAIN = "uncertain"
NEEDS_REVIEW = "needs_review"

MODULE_OUTCOMES = frozenset({
    SUCCESS,
    SKIPPED,
    FAILED_SAFE,
    UNCERTAIN,
    NEEDS_REVIEW,
})
STOP_OUTCOMES = frozenset({FAILED_SAFE, UNCERTAIN, NEEDS_REVIEW})


@dataclass(frozen=True)
class ModuleResult:
    """Normalized result returned by every pipeline module."""

    outcome: str
    reason: str = ""
    outputs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in MODULE_OUTCOMES:
            raise ValueError(f"Unsupported module outcome: {self.outcome!r}")
        if not isinstance(self.reason, str):
            raise TypeError("Module result reason must be a string")
        if not isinstance(self.outputs, Mapping):
            raise TypeError("Module result outputs must be a mapping")
        object.__setattr__(self, "outputs", dict(self.outputs))

    @property
    def should_stop(self) -> bool:
        return self.outcome in STOP_OUTCOMES

    @classmethod
    def success(cls, reason: str = "", **outputs: Any) -> "ModuleResult":
        return cls(SUCCESS, reason, outputs)

    @classmethod
    def skipped(cls, reason: str, **outputs: Any) -> "ModuleResult":
        return cls(SKIPPED, reason, outputs)


@runtime_checkable
class AutomationModule(Protocol):
    """Small interface implemented by each fixed-flow module."""

    module_id: str

    def enabled(self, context: Any) -> bool:
        """Return whether this optional module applies to the current job."""

    def run(self, context: Any) -> ModuleResult:
        """Perform this module's responsibility and return a normalized result."""

