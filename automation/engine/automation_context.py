"""Shared state for one fixed automation pipeline run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .module_contract import ModuleResult


@dataclass
class AutomationContext:
    """Mutable, run-local context passed between pipeline modules.

    Module outputs are namespaced by module and also copied into ``outputs`` for
    downstream consumers. A duplicate output key must keep the same value so one
    module cannot silently replace another module's decision.
    """

    job_id: str
    profile_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    module_results: dict[str, ModuleResult] = field(default_factory=dict)
    behavior: Any = None
    publish_attempted: bool = False

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        profile_id: str,
        inputs: Mapping[str, Any] | None = None,
        profile: Mapping[str, Any] | None = None,
        behavior: Any = None,
    ) -> "AutomationContext":
        return cls(
            job_id=str(job_id),
            profile_id=str(profile_id),
            inputs=dict(inputs or {}),
            profile=dict(profile or {}),
            behavior=behavior,
        )

    def record_result(self, module_id: str, result: ModuleResult) -> None:
        if module_id in self.module_results:
            raise ValueError(f"Module result already recorded: {module_id}")
        for key, value in result.outputs.items():
            if key in self.outputs and self.outputs[key] != value:
                raise ValueError(
                    f"Module {module_id!r} attempted to replace output {key!r}"
                )
        self.module_results[module_id] = result
        self.outputs.update(result.outputs)

