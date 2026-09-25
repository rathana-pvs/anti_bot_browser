"""Unit tests for Phase 3: Guarded Semantic Fallback.

Tests:
  1. Candidate serialization and structured candidate list generation.
  2. HeuristicProvider semantic synonym ranking across UI actions.
  3. Hallucination rejection (rejection of ungrounded / invented candidate IDs).
  4. Deterministic pre-click verification (region boundaries, screen state).
  5. Irreversible action safety gating (final Publish action produces 'needs_review').
  6. Shadow-mode invariant (evaluates proposals without firing executable clicks).
  7. Evidence audit recording.
  8. Task-level safety handling in FacebookPostTask and FacebookReelTask.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import ScreenState, StateObservation
from engine.semantic_fallback import (
    CandidateItem,
    HeuristicProvider,
    SemanticFallbackEngine,
    SemanticProposal,
    collect_action_candidates,
)
from engine.evidence import EvidenceRecorder
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask


class SemanticRankingTests(unittest.TestCase):
    def setUp(self):
        self.provider = HeuristicProvider()

    def test_heuristic_provider_exact_and_substring_synonym(self):
        candidates = [
            {"id": "c1", "text": "Cancel", "region": "bottom_action_bar"},
            {"id": "c2", "text": "Share to feed", "region": "bottom_action_bar"},
            {"id": "c3", "text": "Back", "region": "bottom_action_bar"},
        ]

        # Publish goal
        cid, conf, reason, model = self.provider.rank(
            state="ready_to_publish",
            goal="identify the publish action",
            candidates=candidates,
        )
        self.assertEqual(cid, "c2")
        self.assertGreaterEqual(conf, 0.90)
        self.assertIn("synonym", reason)

        # Next goal
        candidates_next = [
            {"id": "c1", "text": "Cancel", "region": "bottom_action_bar"},
            {"id": "c2", "text": "Next step", "region": "bottom_action_bar"},
        ]
        cid, conf, reason, model = self.provider.rank(
            state="media_ready",
            goal="identify the next action",
            candidates=candidates_next,
        )
        self.assertEqual(cid, "c2")
        self.assertGreaterEqual(conf, 0.88)

    def test_heuristic_provider_no_match_returns_none(self):
        candidates = [
            {"id": "c1", "text": "Privacy Settings", "region": "bottom_action_bar"},
            {"id": "c2", "text": "Audience", "region": "bottom_action_bar"},
        ]
        cid, conf, reason, model = self.provider.rank(
            state="ready_to_publish",
            goal="identify the publish action",
            candidates=candidates,
        )
        self.assertIsNone(cid)
        self.assertEqual(conf, 0.0)

    def test_enabled_action_wins_over_similar_settings_text(self):
        candidates = [
            {"id": "c1", "text": "Boost post", "region": "bottom_action_bar"},
            {"id": "c2", "text": "Post", "region": "enabled_action"},
            {"id": "c3", "text": "Share to story", "region": "bottom_action_bar"},
        ]

        cid, conf, _, _ = self.provider.rank(
            state="post_enabled",
            goal="identify the publish action",
            candidates=candidates,
        )

        self.assertEqual(cid, "c2")
        self.assertEqual(conf, 1.0)

    def test_collect_action_candidates_marks_tight_blue_button_ocr(self):
        vision = Mock()
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        vision.read_text.side_effect = [
            [{"text": "Boost post", "bounds": (760, 760, 860, 810), "center": (810, 785), "confidence": 0.9}],
            [{"text": "Post", "bounds": (1010, 835, 1130, 880), "center": (1070, 857), "confidence": 0.95}],
        ]
        vision.find_blue_action_buttons.return_value = [{
            "bounds": (950, 830, 1190, 885), "center": (1070, 857),
        }]

        candidates = collect_action_candidates(vision, screen, "bottom_action_bar")

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[1]["region"], "enabled_action")


class HallucinationGuardTests(unittest.TestCase):
    def test_hallucinated_candidate_id_rejected(self):
        engine = SemanticFallbackEngine(provider_name="heuristic", shadow_mode=True)
        # Mock provider to return an invented candidate ID "c99"
        engine.provider = Mock()
        engine.provider.name = "mock_hallucinating_provider"
        engine.provider.rank.return_value = ("c99", 0.98, "Invented candidate", "mock-model")

        raw_candidates = [
            {"text": "Cancel", "bounds": (10, 10, 50, 30), "center": (30, 20)},
            {"text": "Back", "bounds": (60, 10, 100, 30), "center": (80, 20)},
        ]

        proposal, candidate_map = engine.rank_candidates(
            state="ready_to_publish",
            goal="identify the publish action",
            raw_candidates=raw_candidates,
            expected_region="bottom_action_bar",
        )

        self.assertIsNotNone(proposal)
        self.assertIsNone(proposal.candidate_id)  # Reject hallucinated ID!
        self.assertIn("Rejected hallucinated candidate", proposal.reason)
        self.assertEqual(proposal.validation_reason, "hallucinated_candidate_id")

    def test_malformed_provider_confidence_is_rejected(self):
        engine = SemanticFallbackEngine(provider_name="heuristic", shadow_mode=True)
        engine.provider = Mock()
        engine.provider.name = "mock_invalid_provider"
        engine.provider.rank.return_value = ("c1", float("nan"), "Invalid confidence", "mock-model")

        proposal, _ = engine.rank_candidates(
            state="media_ready",
            goal="identify the next action",
            raw_candidates=[{
                "text": "Next", "bounds": (900, 800, 1020, 850), "center": (960, 825),
            }],
            expected_region="bottom_action_bar",
        )

        self.assertIsNone(proposal.candidate_id)
        self.assertIn("provider_schema_invalid", proposal.validation_reason)


class DeterministicPreClickGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = SemanticFallbackEngine(provider_name="heuristic", shadow_mode=False)
        self.screen = np.zeros((1080, 1920, 3), dtype=np.uint8)

    @staticmethod
    def _fresh(candidate):
        return [{
            "text": candidate.text,
            "bounds": candidate.bounds,
            "center": candidate.center,
            "confidence": candidate.confidence,
        }]

    def test_rejects_candidate_out_of_region_bounds(self):
        # Candidate at top of screen (y=50), but expected region is bottom_action_bar (y > 700)
        candidate = CandidateItem(
            id="c1",
            text="Share now",
            region="bottom_action_bar",
            bounds=(900, 40, 1020, 60),
            center=(960, 50),
            confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1",
            confidence=0.95,
            reason="Matched synonym",
            latency_ms=10.0,
            provider="heuristic",
            model="heuristic-v1",
            shadow_mode=False,
        )

        allow, reason, target = self.engine.validate_proposal(
            proposal=proposal,
            candidate_map={"c1": candidate},
            current_screen=self.screen,
            expected_region="bottom_action_bar",
            recognizer=None,
            is_reversible=True,
            fresh_candidates=self._fresh(candidate),
            require_enabled_action=False,
        )

        self.assertFalse(allow)
        self.assertIn("out_of_region_bounds", reason)

    def test_rejects_candidate_when_screen_in_error_dialog(self):
        candidate = CandidateItem(
            id="c1",
            text="Next",
            region="bottom_action_bar",
            bounds=(900, 800, 1020, 850),
            center=(960, 825),
            confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1",
            confidence=0.95,
            reason="Matched synonym",
            latency_ms=10.0,
            provider="heuristic",
            model="heuristic-v1",
            shadow_mode=False,
        )

        recognizer = Mock()
        recognizer.observe.return_value = StateObservation(ScreenState.ERROR_DIALOG, 0.99, ["something went wrong"])

        allow, reason, target = self.engine.validate_proposal(
            proposal=proposal,
            candidate_map={"c1": candidate},
            current_screen=self.screen,
            expected_region="bottom_action_bar",
            recognizer=recognizer,
            is_reversible=True,
            fresh_candidates=self._fresh(candidate),
        )

        self.assertFalse(allow)
        self.assertEqual(reason, "screen_in_error_dialog")

    def test_irreversible_action_produces_needs_review(self):
        # Irreversible final publish action
        candidate = CandidateItem(
            id="c1",
            text="Share to feed",
            region="bottom_action_bar",
            bounds=(900, 800, 1020, 850),
            center=(960, 825),
            confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1",
            confidence=0.95,
            reason="Matched synonym",
            latency_ms=10.0,
            provider="heuristic",
            model="heuristic-v1",
            shadow_mode=False,
        )

        recognizer = Mock()
        recognizer.observe.return_value = StateObservation(
            ScreenState.POST_ENABLED, 0.95, ["share to feed", "enabled blue action"]
        )

        allow, reason, target = self.engine.validate_proposal(
            proposal=proposal,
            candidate_map={"c1": candidate},
            current_screen=self.screen,
            expected_region="bottom_action_bar",
            recognizer=recognizer,
            is_reversible=False,  # Irreversible!
            fresh_candidates=self._fresh(candidate),
            expected_states={ScreenState.POST_ENABLED},
        )

        self.assertFalse(allow)
        self.assertEqual(reason, "needs_review")
        self.assertEqual(target, (960, 825))

    def test_shadow_mode_blocks_clicks(self):
        # Reversible action in shadow mode
        self.engine.shadow_mode = True
        candidate = CandidateItem(
            id="c1",
            text="Next",
            region="bottom_action_bar",
            bounds=(900, 800, 1020, 850),
            center=(960, 825),
            confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1",
            confidence=0.95,
            reason="Matched synonym",
            latency_ms=10.0,
            provider="heuristic",
            model="heuristic-v1",
            shadow_mode=True,
        )

        recognizer = Mock()
        recognizer.observe.return_value = StateObservation(
            ScreenState.MEDIA_READY, 0.95, ["next", "enabled blue action"]
        )

        allow, reason, target = self.engine.validate_proposal(
            proposal=proposal,
            candidate_map={"c1": candidate},
            current_screen=self.screen,
            expected_region="bottom_action_bar",
            recognizer=recognizer,
            is_reversible=True,
            fresh_candidates=self._fresh(candidate),
            expected_states={ScreenState.MEDIA_READY},
        )

        # In shadow mode, click is blocked
        self.assertFalse(allow)
        self.assertEqual(reason, "shadow_mode_active")
        self.assertTrue(proposal.validated)

    def test_reversible_action_allowed_when_shadow_mode_disabled(self):
        self.engine.shadow_mode = False
        candidate = CandidateItem(
            id="c1",
            text="Next",
            region="bottom_action_bar",
            bounds=(900, 800, 1020, 850),
            center=(960, 825),
            confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1",
            confidence=0.95,
            reason="Matched synonym",
            latency_ms=10.0,
            provider="heuristic",
            model="heuristic-v1",
            shadow_mode=False,
        )

        recognizer = Mock()
        recognizer.observe.return_value = StateObservation(
            ScreenState.MEDIA_READY, 0.95, ["next", "enabled blue action"]
        )

        allow, reason, target = self.engine.validate_proposal(
            proposal=proposal,
            candidate_map={"c1": candidate},
            current_screen=self.screen,
            expected_region="bottom_action_bar",
            recognizer=recognizer,
            is_reversible=True,
            fresh_candidates=self._fresh(candidate),
            expected_states={ScreenState.MEDIA_READY},
        )

        self.assertTrue(allow)
        self.assertEqual(reason, "validated")
        self.assertEqual(target, (960, 825))

    def test_rejects_candidate_missing_from_fresh_observation(self):
        candidate = CandidateItem(
            id="c1", text="Next", region="bottom_action_bar",
            bounds=(900, 800, 1020, 850), center=(960, 825), confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1", confidence=0.95, reason="Matched synonym",
            latency_ms=1.0, provider="heuristic", model="heuristic-v1", shadow_mode=True,
        )

        allow, reason, target = self.engine.validate_proposal(
            proposal, {"c1": candidate}, self.screen,
            expected_region="bottom_action_bar", fresh_candidates=[],
        )

        self.assertFalse(allow)
        self.assertEqual(reason, "candidate_missing_from_fresh_observation")
        self.assertIsNone(target)

    def test_rejects_unexpected_state_before_shadow_decision(self):
        candidate = CandidateItem(
            id="c1", text="Next", region="bottom_action_bar",
            bounds=(900, 800, 1020, 850), center=(960, 825), confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1", confidence=0.95, reason="Matched synonym",
            latency_ms=1.0, provider="heuristic", model="heuristic-v1", shadow_mode=True,
        )
        recognizer = Mock()
        recognizer.observe.return_value = StateObservation(
            ScreenState.COMPOSER_OPEN, 0.82, ["composer text"], ["Next"]
        )

        allow, reason, _ = self.engine.validate_proposal(
            proposal, {"c1": candidate}, self.screen,
            expected_region="bottom_action_bar", recognizer=recognizer,
            fresh_candidates=self._fresh(candidate),
            expected_states={ScreenState.MEDIA_READY},
        )

        self.assertFalse(allow)
        self.assertIn("unexpected_screen_state:composer_open", reason)

    def test_localized_action_can_derive_ready_state_from_enabled_geometry(self):
        candidate = CandidateItem(
            id="c1", text="Continuer", region="bottom_action_bar",
            bounds=(900, 800, 1020, 850), center=(960, 825), confidence=0.95,
        )
        proposal = SemanticProposal(
            candidate_id="c1", confidence=0.95, reason="Provider match",
            latency_ms=1.0, provider="mock", model="mock-v1", shadow_mode=True,
        )
        recognizer = Mock()
        recognizer.observe.return_value = StateObservation(
            ScreenState.COMPOSER_OPEN, 0.82, ["composer text"], ["Continuer"]
        )
        recognizer.vision.find_blue_action_buttons.return_value = [{
            "center": (960, 825), "bounds": (880, 790, 1040, 860),
        }]
        self.engine.shadow_mode = True

        allow, reason, _ = self.engine.validate_proposal(
            proposal, {"c1": candidate}, self.screen,
            expected_region="bottom_action_bar", recognizer=recognizer,
            fresh_candidates=self._fresh(candidate),
            expected_states={ScreenState.MEDIA_READY},
        )

        self.assertFalse(allow)
        self.assertEqual(reason, "shadow_mode_active")
        self.assertEqual(proposal.observed_state, "composer_open->media_ready")


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.recorder = EvidenceRecorder("audit_test", "test_task")
        self.recorder.directory = self.temp_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_semantic_fallback_writes_json(self):
        meta_path = self.recorder.record_semantic_fallback(
            goal="identify the next action",
            state="media_ready",
            proposal={"candidate_id": "c1", "confidence": 0.95, "reason": "Exact match"},
            candidates=[{"id": "c1", "text": "Next"}],
        )

        self.assertTrue(os.path.exists(meta_path))
        with open(meta_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data["goal"], "identify the next action")
        self.assertEqual(data["proposal"]["candidate_id"], "c1")
        self.assertEqual(data["candidates_count"], 1)


class TaskIntegrationTests(unittest.TestCase):
    def test_post_task_gated_publish_triggers_needs_review(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.log = Mock()
        task.capture_evidence = Mock()
        task.set_outcome = Mock(return_value=False)
        task._stable_blue_text_target = Mock(return_value=None)
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)

        # Mock rank_candidates_semantically to simulate irreversible publish proposal
        task.rank_candidates_semantically = Mock(return_value=(False, "needs_review", (950, 850)))

        target = task._stable_post_target()
        self.assertIsNone(target)
        self.assertTrue(getattr(task, "_requires_review", False))


if __name__ == "__main__":
    unittest.main()
