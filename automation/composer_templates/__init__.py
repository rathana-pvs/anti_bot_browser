"""Declarative composer template registry, detection, and execution."""

from .runtime import (
    ComposerTemplate,
    ComposerTemplateDetector,
    ComposerTemplateExecutor,
    ComposerTemplateRegistry,
    DetectionResult,
    RecognitionProfile,
    RecognitionProfileRegistry,
    TemplateExecutionResult,
    TemplateObservation,
    TemplateValidationError,
    validate_recognition_candidate,
)

__all__ = [
    "ComposerTemplate",
    "ComposerTemplateDetector",
    "ComposerTemplateExecutor",
    "ComposerTemplateRegistry",
    "DetectionResult",
    "RecognitionProfile",
    "RecognitionProfileRegistry",
    "TemplateExecutionResult",
    "TemplateObservation",
    "TemplateValidationError",
    "validate_recognition_candidate",
]
