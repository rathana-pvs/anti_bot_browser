"""Declarative composer template registry, detection, and execution."""

from .runtime import (
    ComposerTemplate,
    ComposerTemplateDetector,
    ComposerTemplateExecutor,
    ComposerTemplateRegistry,
    DetectionResult,
    TemplateExecutionResult,
    TemplateObservation,
    TemplateValidationError,
)

__all__ = [
    "ComposerTemplate",
    "ComposerTemplateDetector",
    "ComposerTemplateExecutor",
    "ComposerTemplateRegistry",
    "DetectionResult",
    "TemplateExecutionResult",
    "TemplateObservation",
    "TemplateValidationError",
]

