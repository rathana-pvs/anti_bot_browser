#!/usr/bin/env python3
"""Offline semantic shadow evaluation against saved automation evidence.

This tool never connects to a browser or emits input. It replays screenshots
that already have a deterministic action target recorded in their adjacent JSON
metadata, ranks OCR candidates, and compares the proposal with that ground
truth. The resulting precision is an evaluation signal only; Publish remains
review-gated regardless of the result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import cv2

from engine.screen_state import ScreenState, StateObservation
from engine.semantic_fallback import (
    EXPECTED_ACTION_STATES,
    SemanticFallbackEngine,
    collect_action_candidates,
)
from engine.vision import VisionEngine


LABEL_ACTIONS = {
    "before_next": "next",
    "reel_next_ready": "next",
    "reel_edit_next_ready": "next",
    "before_publish": "publish",
    "reel_publish_ready": "publish",
    "before_reel_publish": "publish",
}


@dataclass(frozen=True)
class EvidenceSample:
    profile_id: str
    locale: str
    scenario: str
    action: str
    state: str
    goal: str
    image_path: str
    metadata_path: str
    target: tuple[int, int]


@dataclass
class ReplayResult:
    profile_id: str
    locale: str
    scenario: str
    action: str
    state: str
    image_path: str
    target: list[int]
    candidate_count: int
    proposal_candidate_id: str | None
    proposal_text: str | None
    proposal_confidence: float
    proposal_center: list[int] | None
    distance_to_target_px: float | None
    outcome: str
    guard_result: str
    executable_click: bool
    latency_ms: float


class _OfflineClient:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id


class _ReplayRecognizer:
    def __init__(self, state: ScreenState):
        self.state = state

    def observe(self, _screen):
        return StateObservation(
            self.state,
            1.0,
            ["offline ground truth", "enabled blue action"],
            [],
        )


def _locale_for_profile(repo_root: Path, profile_id: str) -> str:
    config_path = repo_root / "profiles" / profile_id / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        return str(config.get("fingerprint", {}).get("language") or "unknown")
    except (OSError, ValueError, TypeError):
        return "unknown"


def classify_label(label: str) -> tuple[str, str, str, str] | None:
    action = LABEL_ACTIONS.get(label)
    if action is None:
        return None
    is_reel = "reel" in label
    scenario = f"{'reel' if is_reel else 'feed'}_{action}"
    if action == "next":
        return action, scenario, ScreenState.MEDIA_READY.value, "identify the next action"
    return action, scenario, ScreenState.POST_ENABLED.value, "identify the publish action"


def discover_samples(repo_root: Path, profiles: Iterable[str]) -> list[EvidenceSample]:
    samples: list[EvidenceSample] = []
    for profile_id in profiles:
        evidence_root = repo_root / "profiles" / profile_id / "automation_evidence"
        locale = _locale_for_profile(repo_root, profile_id)
        if not evidence_root.is_dir():
            continue
        for metadata_path in evidence_root.glob("*/*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            classified = classify_label(str(metadata.get("label", "")))
            target = metadata.get("target")
            image_path = metadata_path.with_suffix(".png")
            if (
                classified is None
                or not isinstance(target, list)
                or len(target) != 2
                or not all(isinstance(value, (int, float)) for value in target)
                or not image_path.is_file()
            ):
                continue
            action, scenario, state, goal = classified
            samples.append(EvidenceSample(
                profile_id=profile_id,
                locale=locale,
                scenario=scenario,
                action=action,
                state=state,
                goal=goal,
                image_path=str(image_path),
                metadata_path=str(metadata_path),
                target=(int(target[0]), int(target[1])),
            ))
    return sorted(samples, key=lambda sample: sample.image_path, reverse=True)


def select_samples(samples: list[EvidenceSample], max_per_scenario: int) -> list[EvidenceSample]:
    """Select recent, content-distinct evidence balanced across profiles."""
    selected: list[EvidenceSample] = []
    digests: set[str] = set()

    def add(sample: EvidenceSample) -> bool:
        try:
            digest = hashlib.blake2b(Path(sample.image_path).read_bytes(), digest_size=12).hexdigest()
        except OSError:
            return False
        if digest in digests:
            return False
        digests.add(digest)
        selected.append(sample)
        return True

    for scenario in sorted({sample.scenario for sample in samples}):
        scenario_samples = [sample for sample in samples if sample.scenario == scenario]
        scenario_selected = 0
        # Take the newest distinct sample from each profile first.
        for profile_id in sorted({sample.profile_id for sample in scenario_samples}):
            for sample in scenario_samples:
                if sample.profile_id == profile_id and add(sample):
                    scenario_selected += 1
                    break
            if scenario_selected >= max_per_scenario:
                break
        # Fill remaining slots by recency after profile coverage is established.
        if scenario_selected < max_per_scenario:
            for sample in scenario_samples:
                if add(sample):
                    scenario_selected += 1
                if scenario_selected >= max_per_scenario:
                    break
    return selected


def evaluate_sample(sample: EvidenceSample, tolerance_px: float) -> ReplayResult:
    screen = cv2.imread(sample.image_path)
    if screen is None:
        raise RuntimeError(f"Could not read evidence image: {sample.image_path}")

    vision = VisionEngine(_OfflineClient(sample.profile_id))
    vision.detect_theme(screen)
    candidates = collect_action_candidates(
        vision, screen, "bottom_action_bar", min_confidence=0.20,
    )
    engine = SemanticFallbackEngine(provider_name="heuristic", shadow_mode=True)
    proposal, candidate_map = engine.rank_candidates(
        state=sample.state,
        goal=sample.goal,
        raw_candidates=candidates,
        expected_region="bottom_action_bar",
    )

    candidate = (
        candidate_map.get(proposal.candidate_id)
        if proposal is not None and proposal.candidate_id is not None
        else None
    )
    distance = None
    outcome = "abstained"
    guard_result = "no_proposal"
    executable_click = False
    if candidate is not None and proposal is not None:
        distance = math.hypot(
            candidate.center[0] - sample.target[0],
            candidate.center[1] - sample.target[1],
        )
        outcome = "correct" if distance <= tolerance_px else "incorrect"
        expected_states = EXPECTED_ACTION_STATES[sample.goal]
        executable_click, guard_result, _ = engine.validate_proposal(
            proposal=proposal,
            candidate_map=candidate_map,
            current_screen=screen,
            expected_region="bottom_action_bar",
            recognizer=_ReplayRecognizer(ScreenState(sample.state)),
            is_reversible=sample.action == "next",
            fresh_candidates=candidates,
            expected_states=expected_states,
            require_enabled_action=True,
        )

    return ReplayResult(
        profile_id=sample.profile_id,
        locale=sample.locale,
        scenario=sample.scenario,
        action=sample.action,
        state=sample.state,
        image_path=sample.image_path,
        target=list(sample.target),
        candidate_count=len(candidates),
        proposal_candidate_id=proposal.candidate_id if proposal else None,
        proposal_text=candidate.text if candidate else None,
        proposal_confidence=proposal.confidence if proposal else 0.0,
        proposal_center=list(candidate.center) if candidate else None,
        distance_to_target_px=round(distance, 2) if distance is not None else None,
        outcome=outcome,
        guard_result=guard_result,
        executable_click=bool(executable_click),
        latency_ms=proposal.latency_ms if proposal else 0.0,
    )


def _metrics(results: list[ReplayResult]) -> dict:
    total = len(results)
    proposals = sum(result.outcome != "abstained" for result in results)
    correct = sum(result.outcome == "correct" for result in results)
    incorrect = sum(result.outcome == "incorrect" for result in results)
    return {
        "samples": total,
        "proposals": proposals,
        "correct": correct,
        "incorrect": incorrect,
        "abstained": total - proposals,
        "coverage_pct": round(proposals / total * 100.0, 1) if total else None,
        "precision_pct": round(correct / proposals * 100.0, 1) if proposals else None,
        "accuracy_pct": round(correct / total * 100.0, 1) if total else None,
        "executable_clicks": sum(result.executable_click for result in results),
    }


def summarize(results: list[ReplayResult], discovered: int, tolerance_px: float) -> dict:
    breakdown: dict[str, dict[str, dict]] = {"scenario": {}, "state": {}, "locale": {}}
    for dimension in breakdown:
        values = sorted({getattr(result, dimension) for result in results})
        for value in values:
            breakdown[dimension][value] = _metrics([
                result for result in results if getattr(result, dimension) == value
            ])
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_saved_evidence_shadow_replay",
        "provider": "heuristic",
        "shadow_mode": True,
        "tolerance_px": tolerance_px,
        "discovered_ground_truth_samples": discovered,
        "summary": _metrics(results),
        "breakdown": breakdown,
        "safety": {
            "browser_connections": 0,
            "input_events": 0,
            "external_provider_calls": 0,
            "temporal_freshness_evaluated": False,
            "publish_gate_expected_result": "needs_review",
        },
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", default=["profile_002", "profile_003"])
    parser.add_argument("--max-per-scenario", type=int, default=3)
    parser.add_argument("--tolerance-px", type=float, default=180.0)
    parser.add_argument("--output", default="automation/reports/semantic_shadow_latest.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    samples = discover_samples(repo_root, args.profiles)
    selected = select_samples(samples, max(1, args.max_per_scenario))
    results = [evaluate_sample(sample, args.tolerance_px) for sample in selected]
    report = summarize(results, len(samples), args.tolerance_px)

    output_path = (repo_root / args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temp_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, output_path)

    print(json.dumps({"output": str(output_path), **report["summary"]}, indent=2))
    return 0 if report["summary"]["executable_clicks"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
