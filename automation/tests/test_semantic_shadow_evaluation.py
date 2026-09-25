import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from evaluate_semantic_shadow import (
    EvidenceSample,
    ReplayResult,
    classify_label,
    select_samples,
    summarize,
)


class SemanticShadowEvaluationTests(unittest.TestCase):
    def test_classifies_feed_and_reel_ground_truth_labels(self):
        self.assertEqual(
            classify_label("before_next"),
            ("next", "feed_next", "media_ready", "identify the next action"),
        )
        self.assertEqual(
            classify_label("before_reel_publish"),
            ("publish", "reel_publish", "post_enabled", "identify the publish action"),
        )
        self.assertIsNone(classify_label("publication_verification"))

    def test_summary_reports_precision_coverage_and_zero_clicks(self):
        base = dict(
            profile_id="profile_003", locale="en-US", scenario="feed_next",
            action="next", state="media_ready", image_path="evidence.png",
            target=[100, 100], candidate_count=2, proposal_candidate_id="c1",
            proposal_text="Next", proposal_confidence=0.98,
            proposal_center=[100, 100], distance_to_target_px=0.0,
            guard_result="shadow_mode_active", executable_click=False, latency_ms=1.0,
        )
        results = [
            ReplayResult(**base, outcome="correct"),
            ReplayResult(**{
                **base,
                "proposal_candidate_id": None,
                "proposal_text": None,
                "proposal_center": None,
                "distance_to_target_px": None,
                "outcome": "abstained",
                "guard_result": "no_proposal",
            }),
        ]

        report = summarize(results, discovered=5, tolerance_px=180.0)

        self.assertEqual(report["summary"]["samples"], 2)
        self.assertEqual(report["summary"]["coverage_pct"], 50.0)
        self.assertEqual(report["summary"]["precision_pct"], 100.0)
        self.assertEqual(report["summary"]["accuracy_pct"], 50.0)
        self.assertEqual(report["summary"]["executable_clicks"], 0)
        self.assertFalse(report["safety"]["temporal_freshness_evaluated"])

    def test_sample_selection_balances_profiles_before_filling(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            samples = []
            for index, profile_id in enumerate(("profile_003", "profile_003", "profile_002")):
                image = Path(temp_dir) / f"{index}.png"
                image.write_bytes(f"image-{index}".encode())
                samples.append(EvidenceSample(
                    profile_id=profile_id, locale="en-US", scenario="feed_next",
                    action="next", state="media_ready", goal="identify the next action",
                    image_path=str(image), metadata_path=f"{image}.json", target=(1, 1),
                ))

            selected = select_samples(samples, max_per_scenario=2)

            self.assertEqual({sample.profile_id for sample in selected}, {"profile_002", "profile_003"})


if __name__ == "__main__":
    unittest.main()
