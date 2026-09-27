"""Validated, declarative workflow packages for the automation engine.

Brain packages are data, not executable Python.  This module intentionally has
no importlib/eval/exec path: packages can only name capabilities that the host
engine has explicitly allow-listed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping, Protocol

import yaml


ENGINE_VERSION = "1.0.0"
SUPPORTED_MANIFEST_VERSION = 1
SUPPORTED_BRAIN_API_VERSION = 1
TERMINAL_STATES = {
    "completed",
    "failed_safe",
    "safe_abort",
    "uncertain",
    "operator_review",
}
TRANSITION_KEYS = {
    "on_success",
    "on_timeout",
    "on_rejected",
    "on_uncertain",
    "on_known_dialog",
    "on_failure",
}
ALLOWED_CAPABILITIES = {
    "observe_screen",
    "wait_for_state",
    "find_text",
    "find_template",
    "click_candidate",
    "type_text",
    "select_media",
    "dismiss_known_dialog",
    "request_publish",
    "verify_publication",
    "request_operator_review",
}

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


class BrainValidationError(ValueError):
    """Raised when a Brain package violates the host contract."""


class BrainResolutionError(LookupError):
    """Raised when a requested Brain cannot be resolved safely."""


def _semver_tuple(value: str, field_name: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise BrainValidationError(f"{field_name} must be a semantic version")
    return tuple(int(part) for part in match.groups())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainValidationError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BrainValidationError(f"{path} must contain a JSON object")
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BrainValidationError(f"Could not read valid YAML from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BrainValidationError(f"{path} must contain a YAML mapping")
    return value


def _content_digest(root: Path) -> str:
    """Hash a local package deterministically for job pinning.

    Distributed archives retain their separately signed archive digest.  This
    digest identifies the exact extracted contents used by a local job.
    """
    digest = sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise BrainValidationError(f"Brain package is empty: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


@dataclass(frozen=True)
class BrainPackage:
    root: Path
    manifest: Mapping[str, Any]
    workflow: Mapping[str, Any]
    digest: str

    @property
    def brain_id(self) -> str:
        return str(self.manifest["id"])

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    def metadata(self) -> dict[str, str | int]:
        return {
            "id": self.brain_id,
            "version": self.version,
            "digest": self.digest,
            "brain_api_version": int(self.manifest["brain_api_version"]),
        }


def load_brain_package(
    root: str | Path,
    *,
    expected_id: str | None = None,
    engine_version: str = ENGINE_VERSION,
) -> BrainPackage:
    root = Path(root).resolve()
    if not root.is_dir():
        raise BrainValidationError(f"Brain package directory does not exist: {root}")

    manifest_path = root / "manifest.json"
    workflow_path = root / "workflow.yaml"
    manifest = _read_json(manifest_path)
    workflow = _read_yaml(workflow_path)
    _validate_manifest(manifest, expected_id=expected_id, engine_version=engine_version)
    _validate_workflow(workflow, manifest)
    return BrainPackage(root=root, manifest=manifest, workflow=workflow, digest=_content_digest(root))


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_id: str | None,
    engine_version: str,
) -> None:
    required = {
        "manifest_version",
        "brain_api_version",
        "id",
        "version",
        "platform",
        "task_type",
        "min_engine_version",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise BrainValidationError(f"Manifest is missing required fields: {', '.join(missing)}")
    if manifest["manifest_version"] != SUPPORTED_MANIFEST_VERSION:
        raise BrainValidationError("Unsupported manifest_version")
    if manifest["brain_api_version"] != SUPPORTED_BRAIN_API_VERSION:
        raise BrainValidationError("Unsupported brain_api_version")

    brain_id = manifest["id"]
    if not isinstance(brain_id, str) or not _IDENTIFIER_RE.fullmatch(brain_id):
        raise BrainValidationError("Manifest id is not a safe identifier")
    if expected_id is not None and brain_id != expected_id:
        raise BrainValidationError(f"Expected Brain id {expected_id!r}, got {brain_id!r}")

    version = _semver_tuple(str(manifest["version"]), "version")
    del version  # Validation is the purpose; ordering happens in the registry.
    engine = _semver_tuple(engine_version, "engine_version")
    minimum = _semver_tuple(str(manifest["min_engine_version"]), "min_engine_version")
    if engine < minimum:
        raise BrainValidationError(
            f"Engine {engine_version} is older than required {manifest['min_engine_version']}"
        )
    maximum_value = manifest.get("max_engine_version_exclusive")
    if maximum_value is not None:
        maximum = _semver_tuple(str(maximum_value), "max_engine_version_exclusive")
        if engine >= maximum:
            raise BrainValidationError(
                f"Engine {engine_version} is not below required maximum {maximum_value}"
            )

    for field_name in ("platform", "task_type"):
        value = manifest[field_name]
        if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
            raise BrainValidationError(f"Manifest {field_name} is not a safe identifier")


def _validate_workflow(workflow: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    if workflow.get("schema_version") != 1:
        raise BrainValidationError("Unsupported workflow schema_version")
    if workflow.get("brain_api_version") != manifest["brain_api_version"]:
        raise BrainValidationError("Workflow and manifest brain_api_version values differ")
    if workflow.get("workflow_id") != manifest["id"]:
        raise BrainValidationError("Workflow id does not match manifest id")

    limits = workflow.get("limits")
    if not isinstance(limits, dict):
        raise BrainValidationError("Workflow limits must be a mapping")
    for name, default_max in (
        ("max_steps", 500),
        ("max_runtime_seconds", 3600),
        ("max_recovery_attempts", 20),
    ):
        value = limits.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > default_max:
            raise BrainValidationError(f"Workflow limit {name} must be between 1 and {default_max}")

    states = workflow.get("states")
    if not isinstance(states, dict) or not states or len(states) > 100:
        raise BrainValidationError("Workflow states must contain between 1 and 100 states")
    initial = workflow.get("initial_state")
    if initial not in states:
        raise BrainValidationError("Workflow initial_state is not defined")

    graph: dict[str, set[str]] = {name: set() for name in states}
    for state_name, definition in states.items():
        if not isinstance(state_name, str) or not _IDENTIFIER_RE.fullmatch(state_name):
            raise BrainValidationError(f"Unsafe state identifier: {state_name!r}")
        if not isinstance(definition, dict):
            raise BrainValidationError(f"State {state_name} must be a mapping")
        actions = definition.get("actions", [])
        if not isinstance(actions, list) or len(actions) > 20:
            raise BrainValidationError(f"State {state_name} actions must be a list of at most 20")
        for action in actions:
            if not isinstance(action, dict):
                raise BrainValidationError(f"State {state_name} contains an invalid action")
            capability = action.get("capability")
            if capability not in ALLOWED_CAPABILITIES:
                raise BrainValidationError(
                    f"State {state_name} requests unsupported capability {capability!r}"
                )

        transitions = [key for key in TRANSITION_KEYS if key in definition]
        if not transitions:
            raise BrainValidationError(f"State {state_name} has no outcome transition")
        for key in transitions:
            target = definition[key]
            if target not in states and target not in TERMINAL_STATES:
                raise BrainValidationError(
                    f"State {state_name} transition {key} targets unknown state {target!r}"
                )
            if target in states:
                graph[state_name].add(target)

    reachable: set[str] = set()
    pending = [initial]
    while pending:
        state_name = pending.pop()
        if state_name in reachable:
            continue
        reachable.add(state_name)
        pending.extend(graph[state_name] - reachable)
    unreachable = sorted(set(states) - reachable)
    if unreachable:
        raise BrainValidationError(f"Workflow has unreachable states: {', '.join(unreachable)}")


class BrainRegistry:
    """Resolve installed immutable Brain versions without following user paths."""

    def __init__(self, root: str | Path, *, engine_version: str = ENGINE_VERSION):
        self.root = Path(root).resolve()
        self.engine_version = engine_version

    def resolve(self, brain_id: str, requested_version: str | None = None) -> BrainPackage:
        if not _IDENTIFIER_RE.fullmatch(brain_id):
            raise BrainResolutionError("Unsafe Brain id")
        family = self.root / brain_id
        version = requested_version or self._active_version(brain_id) or "bundled_default"
        if version != "bundled_default" and not _SEMVER_RE.fullmatch(version):
            raise BrainResolutionError("Unsafe or invalid Brain version")
        candidate = (family / version).resolve()
        try:
            candidate.relative_to(family.resolve())
        except ValueError as exc:
            raise BrainResolutionError("Brain path escaped its family directory") from exc
        try:
            return load_brain_package(
                candidate,
                expected_id=brain_id,
                engine_version=self.engine_version,
            )
        except BrainValidationError as exc:
            raise BrainResolutionError(str(exc)) from exc

    def _active_version(self, brain_id: str) -> str | None:
        catalog_path = self.root / "catalog.json"
        if not catalog_path.is_file():
            return None
        catalog = _read_json(catalog_path)
        families = catalog.get("brains", {})
        if not isinstance(families, dict):
            raise BrainResolutionError("Brain catalog has an invalid brains mapping")
        entry = families.get(brain_id, {})
        if not isinstance(entry, dict):
            raise BrainResolutionError(f"Brain catalog entry for {brain_id} is invalid")
        active = entry.get("active_version")
        return str(active) if active is not None else None


@dataclass(frozen=True)
class CapabilityResult:
    outcome: str = "success"
    data: Mapping[str, Any] = field(default_factory=dict)


class CapabilityProvider(Protocol):
    def evaluate(self, requirement: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
        """Evaluate an engine-owned visual requirement."""

    def call(
        self,
        capability: str,
        parameters: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> CapabilityResult:
        """Execute one allow-listed engine capability."""


@dataclass(frozen=True)
class WorkflowResult:
    terminal_state: str
    steps: int
    final_state: str
    history: tuple[Mapping[str, Any], ...]


class WorkflowInterpreter:
    """Bounded interpreter for already-validated workflows."""

    def __init__(self, package: BrainPackage, provider: CapabilityProvider):
        self.package = package
        self.provider = provider

    def run(self, inputs: Mapping[str, Any] | None = None) -> WorkflowResult:
        workflow = self.package.workflow
        limits = workflow["limits"]
        max_steps = int(limits["max_steps"])
        deadline = time.monotonic() + int(limits["max_runtime_seconds"])
        state_name = str(workflow["initial_state"])
        states = workflow["states"]
        context: dict[str, Any] = {"inputs": dict(inputs or {})}
        history: list[Mapping[str, Any]] = []

        for step in range(1, max_steps + 1):
            if time.monotonic() >= deadline:
                return WorkflowResult("operator_review", step - 1, state_name, tuple(history))
            definition = states[state_name]
            history.append({"step": step, "state": state_name})

            requirement = definition.get("require")
            if requirement is not None:
                if not isinstance(requirement, dict):
                    raise BrainValidationError(f"State {state_name} require must be a mapping")
                if not self.provider.evaluate(requirement, context):
                    target = definition.get("on_timeout", "operator_review")
                    if target in TERMINAL_STATES:
                        return WorkflowResult(target, step, state_name, tuple(history))
                    state_name = target
                    continue

            outcome = "success"
            for action in definition.get("actions", []):
                capability = action["capability"]
                # Recheck at execution time so a mutated in-memory object cannot
                # turn package validation into an authorization bypass.
                if capability not in ALLOWED_CAPABILITIES:
                    raise BrainValidationError(f"Capability is not allowed: {capability!r}")
                parameters = {key: value for key, value in action.items() if key != "capability"}
                result = self.provider.call(capability, parameters, context)
                if result.outcome not in {
                    "success", "timeout", "rejected", "uncertain", "known_dialog", "failure"
                }:
                    raise BrainValidationError(f"Capability returned invalid outcome {result.outcome!r}")
                if result.data:
                    context.update(result.data)
                if result.outcome != "success":
                    outcome = result.outcome
                    break

            transition_key = "on_success" if outcome == "success" else f"on_{outcome}"
            target = definition.get(transition_key, definition.get("on_failure", "operator_review"))
            if target in TERMINAL_STATES:
                return WorkflowResult(target, step, state_name, tuple(history))
            if target not in states:
                raise BrainValidationError(
                    f"State {state_name} has no safe transition for outcome {outcome!r}"
                )
            state_name = target

        return WorkflowResult("operator_review", max_steps, state_name, tuple(history))
