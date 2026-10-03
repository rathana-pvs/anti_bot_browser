"""Safe runtime for declarative Post and Reel composer templates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Protocol

import yaml

from engine.brain_runtime import ALLOWED_CAPABILITIES, CapabilityResult


_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CONTENT_FAMILIES = frozenset({"post", "reel"})
STEP_OUTCOMES = frozenset({
    "success", "timeout", "rejected", "uncertain", "known_dialog", "failure"
})


class TemplateValidationError(ValueError):
    """Raised when a composer template violates the declarative contract."""


@dataclass(frozen=True)
class RecognitionRule:
    signal: str
    weight: float = 1.0


@dataclass(frozen=True)
class ComposerTemplate:
    template_id: str
    content_family: str
    minimum_score: float
    required: tuple[RecognitionRule, ...]
    weighted: tuple[RecognitionRule, ...]
    forbidden: tuple[str, ...]
    steps: tuple[Mapping[str, Any], ...]
    source: Path


@dataclass(frozen=True)
class TemplateObservation:
    """Engine-owned signals observed from one stable screen frame."""

    signals: Mapping[str, float]

    def confidence(self, signal: str) -> float:
        value = self.signals.get(signal, 0.0)
        return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class DetectionResult:
    outcome: str
    template: ComposerTemplate | None = None
    score: float | None = None
    runner_up_score: float | None = None
    reason: str = ""
    candidates: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class TemplateExecutionResult:
    outcome: str
    template_id: str
    completed_steps: int
    reason: str = ""
    data: Mapping[str, Any] | None = None


class TemplateCapabilityProvider(Protocol):
    def call(
        self,
        capability: str,
        parameters: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> CapabilityResult:
        """Execute one trusted, allow-listed capability."""


class ComposerTemplateRegistry:
    def __init__(self, package_root: str | Path):
        self.package_root = Path(package_root).resolve()

    def load(self, content_family: str) -> dict[str, ComposerTemplate]:
        if content_family not in CONTENT_FAMILIES:
            raise TemplateValidationError(f"Unknown content family: {content_family}")
        template_dir = self.package_root / "templates"
        if not template_dir.is_dir():
            raise TemplateValidationError(f"Template directory does not exist: {template_dir}")
        templates: dict[str, ComposerTemplate] = {}
        for path in sorted(template_dir.glob("*.yaml")):
            template = self._load_file(path)
            if template.content_family != content_family:
                continue
            if template.template_id in templates:
                raise TemplateValidationError(
                    f"Duplicate template id: {template.template_id}"
                )
            templates[template.template_id] = template
        if not templates:
            raise TemplateValidationError(
                f"No {content_family} templates found in {template_dir}"
            )
        return templates

    @staticmethod
    def _load_file(path: Path) -> ComposerTemplate:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise TemplateValidationError(f"Could not read template {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise TemplateValidationError(f"Template {path} must be a mapping")
        if raw.get("schema_version") != 1:
            raise TemplateValidationError(f"Unsupported template schema in {path}")
        template_id = raw.get("template_id")
        if not isinstance(template_id, str) or not _ID_RE.fullmatch(template_id):
            raise TemplateValidationError(f"Invalid template_id in {path}")
        family = raw.get("content_family")
        if family not in CONTENT_FAMILIES:
            raise TemplateValidationError(f"Invalid content_family in {path}")

        recognition = raw.get("recognition")
        if not isinstance(recognition, dict):
            raise TemplateValidationError(f"Template {template_id} needs recognition rules")
        minimum_score = recognition.get("minimum_score")
        if not isinstance(minimum_score, (int, float)) or isinstance(minimum_score, bool):
            raise TemplateValidationError(f"Template {template_id} minimum_score is invalid")
        minimum_score = float(minimum_score)
        if not 0.0 < minimum_score <= 1.0:
            raise TemplateValidationError(f"Template {template_id} minimum_score is invalid")

        required = _parse_rules(recognition.get("required"), template_id, required=True)
        weighted = _parse_rules(recognition.get("weighted", []), template_id, required=False)
        forbidden_raw = recognition.get("forbidden", [])
        if not isinstance(forbidden_raw, list) or any(
            not isinstance(signal, str) or not _ID_RE.fullmatch(signal)
            for signal in forbidden_raw
        ):
            raise TemplateValidationError(f"Template {template_id} forbidden rules are invalid")

        steps = raw.get("steps")
        if not isinstance(steps, list) or not steps or len(steps) > 40:
            raise TemplateValidationError(f"Template {template_id} needs 1-40 steps")
        validated_steps = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise TemplateValidationError(
                    f"Template {template_id} step {index + 1} must be a mapping"
                )
            capability = step.get("capability")
            if capability not in ALLOWED_CAPABILITIES:
                raise TemplateValidationError(
                    f"Template {template_id} step {index + 1} uses unsupported "
                    f"capability {capability!r}"
                )
            when = step.get("when")
            if when is not None and (
                not isinstance(when, str) or not _ID_RE.fullmatch(when)
            ):
                raise TemplateValidationError(
                    f"Template {template_id} step {index + 1} has invalid when"
                )
            optional = step.get("optional", False)
            if not isinstance(optional, bool):
                raise TemplateValidationError(
                    f"Template {template_id} step {index + 1} optional must be boolean"
                )
            validated_steps.append(dict(step))

        return ComposerTemplate(
            template_id=template_id,
            content_family=family,
            minimum_score=minimum_score,
            required=required,
            weighted=weighted,
            forbidden=tuple(forbidden_raw),
            steps=tuple(validated_steps),
            source=path.resolve(),
        )


def _parse_rules(value, template_id: str, *, required: bool) -> tuple[RecognitionRule, ...]:
    if not isinstance(value, list) or (required and not value):
        label = "required" if required else "weighted"
        raise TemplateValidationError(f"Template {template_id} {label} rules are invalid")
    rules = []
    for item in value:
        if isinstance(item, str):
            signal, weight = item, 1.0
        elif isinstance(item, dict):
            signal, weight = item.get("signal"), item.get("weight", 1.0)
        else:
            raise TemplateValidationError(f"Template {template_id} recognition rule is invalid")
        if not isinstance(signal, str) or not _ID_RE.fullmatch(signal):
            raise TemplateValidationError(f"Template {template_id} signal is invalid")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or float(weight) <= 0:
            raise TemplateValidationError(f"Template {template_id} weight is invalid")
        rules.append(RecognitionRule(signal, float(weight)))
    return tuple(rules)


class ComposerTemplateDetector:
    def __init__(self, *, minimum_margin: float = 0.15, stable_observations: int = 2):
        if not 0.0 <= minimum_margin <= 1.0:
            raise ValueError("minimum_margin must be between 0 and 1")
        if stable_observations < 1:
            raise ValueError("stable_observations must be positive")
        self.minimum_margin = minimum_margin
        self.stable_observations = stable_observations

    def detect(
        self,
        templates: Iterable[ComposerTemplate],
        observations: Iterable[TemplateObservation],
    ) -> DetectionResult:
        templates = tuple(templates)
        observations = tuple(observations)
        if len(observations) < self.stable_observations:
            return DetectionResult("insufficient_observations", reason="screen_not_stable")

        frame_winners = []
        last_candidates: tuple[tuple[str, float], ...] = ()
        last_ranked: list[tuple[ComposerTemplate, float]] = []
        for observation in observations[-self.stable_observations:]:
            ranked = self._rank(templates, observation)
            last_ranked = ranked
            last_candidates = tuple((item.template_id, score) for item, score in ranked)
            if not ranked:
                return DetectionResult(
                    "not_found", reason="no_template_met_required_signals",
                    candidates=last_candidates,
                )
            winner, score = ranked[0]
            runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
            if score < winner.minimum_score:
                return DetectionResult(
                    "not_found", score=score, runner_up_score=runner_up,
                    reason="minimum_score_not_met", candidates=last_candidates,
                )
            if len(ranked) > 1 and score - runner_up < self.minimum_margin:
                return DetectionResult(
                    "ambiguous", score=score, runner_up_score=runner_up,
                    reason="candidate_margin_too_small", candidates=last_candidates,
                )
            frame_winners.append(winner.template_id)

        if len(set(frame_winners)) != 1:
            return DetectionResult(
                "unstable", reason="template_changed_between_observations",
                candidates=last_candidates,
            )
        winner, score = last_ranked[0]
        runner_up = last_ranked[1][1] if len(last_ranked) > 1 else None
        return DetectionResult(
            "selected", template=winner, score=score,
            runner_up_score=runner_up, candidates=last_candidates,
        )

    @staticmethod
    def _rank(
        templates: Iterable[ComposerTemplate], observation: TemplateObservation
    ) -> list[tuple[ComposerTemplate, float]]:
        ranked = []
        for template in templates:
            if any(observation.confidence(signal) > 0.0 for signal in template.forbidden):
                continue
            if any(observation.confidence(rule.signal) <= 0.0 for rule in template.required):
                continue
            rules = template.required + template.weighted
            total_weight = sum(rule.weight for rule in rules)
            score = sum(
                rule.weight * observation.confidence(rule.signal) for rule in rules
            ) / total_weight
            ranked.append((template, round(score, 4)))
        ranked.sort(key=lambda item: (-item[1], item[0].template_id))
        return ranked


class ComposerTemplateExecutor:
    def __init__(self, provider: TemplateCapabilityProvider):
        self.provider = provider

    def run(
        self,
        template: ComposerTemplate,
        context: Mapping[str, Any],
    ) -> TemplateExecutionResult:
        runtime_context = dict(context)
        inputs = runtime_context.get("inputs")
        if not isinstance(inputs, Mapping):
            inputs = {}
        completed = 0
        for step in template.steps:
            when = step.get("when")
            if when and not inputs.get(when):
                continue
            capability = step["capability"]
            if capability not in ALLOWED_CAPABILITIES:
                raise TemplateValidationError(f"Capability is not allowed: {capability!r}")
            parameters = {
                key: value for key, value in step.items()
                if key not in {"capability", "optional", "when"}
            }
            result = self.provider.call(capability, parameters, runtime_context)
            if result.outcome not in STEP_OUTCOMES:
                raise TemplateValidationError(
                    f"Capability returned invalid outcome: {result.outcome!r}"
                )
            if result.data:
                runtime_context.update(result.data)
            if result.outcome != "success":
                if step.get("optional") and result.outcome in {"timeout", "failure"}:
                    continue
                return TemplateExecutionResult(
                    result.outcome,
                    template.template_id,
                    completed,
                    reason=f"step_{completed + 1}:{capability}",
                    data=dict(result.data),
                )
            completed += 1
        return TemplateExecutionResult(
            "success", template.template_id, completed, data=runtime_context
        )

