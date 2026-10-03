"""Bounded orchestrator for the shared Facebook publishing flow."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Mapping

from .automation_context import AutomationContext
from .module_contract import (
    AutomationModule,
    FAILED_SAFE,
    ModuleResult,
    NEEDS_REVIEW,
    SKIPPED,
    UNCERTAIN,
)


PIPELINE_ORDER = (
    "startup",
    "warming",
    "publish",
    "post_publish_prompt",
    "comment",
    "finalize",
)
FINALIZE_MODULE_ID = "finalize"


@dataclass(frozen=True)
class PipelineResult:
    """Final view of one orchestrator run."""

    outcome: str
    stopped_at: str | None
    module_results: Mapping[str, ModuleResult]
    outputs: Mapping[str, object]
    finalization_result: ModuleResult
    module_durations_ms: Mapping[str, float]


class FixedAutomationOrchestrator:
    """Execute the agreed module order and always invoke Finalize.

    Browser-facing modules stop after the first failure, uncertainty, review
    request, or unexpected exception. Remaining modules are recorded as skipped.
    Finalize then runs exactly once and cannot replace the original outcome.
    """

    def __init__(self, modules: Mapping[str, AutomationModule]):
        missing = [module_id for module_id in PIPELINE_ORDER if module_id not in modules]
        extra = sorted(set(modules) - set(PIPELINE_ORDER))
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing={','.join(missing)}")
            if extra:
                details.append(f"extra={','.join(extra)}")
            raise ValueError("Invalid fixed pipeline modules: " + " ".join(details))
        for module_id, module in modules.items():
            if getattr(module, "module_id", None) != module_id:
                raise ValueError(
                    f"Module mapping key {module_id!r} does not match module_id "
                    f"{getattr(module, 'module_id', None)!r}"
                )
        self.modules = dict(modules)

    def run(self, context: AutomationContext) -> PipelineResult:
        primary_result = ModuleResult.success("pipeline_completed")
        stopped_at: str | None = None
        durations: dict[str, float] = {}

        for module_id in PIPELINE_ORDER[:-1]:
            if stopped_at is not None:
                context.record_result(
                    module_id,
                    ModuleResult(SKIPPED, f"pipeline_stopped_at:{stopped_at}"),
                )
                durations[module_id] = 0.0
                continue

            module = self.modules[module_id]
            started = time.perf_counter()
            try:
                if not module.enabled(context):
                    result = ModuleResult(SKIPPED, "module_not_applicable")
                else:
                    result = module.run(context)
                    if not isinstance(result, ModuleResult):
                        raise TypeError(
                            f"Module {module_id!r} returned {type(result).__name__}; "
                            "expected ModuleResult"
                        )
            except Exception as exc:
                result = ModuleResult(
                    UNCERTAIN if context.publish_attempted else FAILED_SAFE,
                    f"unhandled_exception:{type(exc).__name__}",
                    {"pipeline_exception": str(exc)},
                )
            durations[module_id] = round((time.perf_counter() - started) * 1000.0, 3)

            context.record_result(module_id, result)
            if result.should_stop:
                primary_result = result
                stopped_at = module_id

        context.environment["module_durations_ms"] = durations
        finalizer_started = time.perf_counter()
        finalization_result = self._run_finalizer(context)
        durations[FINALIZE_MODULE_ID] = round(
            (time.perf_counter() - finalizer_started) * 1000.0,
            3,
        )
        context.record_result(FINALIZE_MODULE_ID, finalization_result)

        if stopped_at is None and finalization_result.should_stop:
            primary_result = finalization_result
            stopped_at = FINALIZE_MODULE_ID

        return PipelineResult(
            outcome=primary_result.outcome,
            stopped_at=stopped_at,
            module_results=dict(context.module_results),
            outputs=dict(context.outputs),
            finalization_result=finalization_result,
            module_durations_ms=dict(durations),
        )

    def _run_finalizer(self, context: AutomationContext) -> ModuleResult:
        module = self.modules[FINALIZE_MODULE_ID]
        try:
            result = module.run(context)
            if not isinstance(result, ModuleResult):
                raise TypeError(
                    f"Finalize returned {type(result).__name__}; expected ModuleResult"
                )
            return result
        except Exception as exc:
            return ModuleResult(
                NEEDS_REVIEW,
                f"finalization_exception:{type(exc).__name__}",
                {"finalization_exception": str(exc)},
            )
