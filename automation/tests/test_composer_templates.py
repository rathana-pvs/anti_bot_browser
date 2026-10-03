from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from composer_templates import (
    ComposerTemplateDetector,
    ComposerTemplateExecutor,
    ComposerTemplateRegistry,
    RecognitionProfileRegistry,
    TemplateObservation,
    TemplateValidationError,
    validate_recognition_candidate,
)
from engine.brain_runtime import CapabilityResult


AUTOMATION_ROOT = Path(__file__).resolve().parents[1]


class ComposerTemplateTests(unittest.TestCase):
    def test_bundled_post_templates_load(self):
        templates = ComposerTemplateRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_post" / "bundled_default"
        ).load("post")
        self.assertEqual(set(templates), {"p1", "p2"})

    def test_bundled_reel_templates_load(self):
        templates = ComposerTemplateRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
        ).load("reel")
        self.assertEqual(set(templates), {"t1", "t2", "t3"})

    def test_reel_entry_profiles_use_the_generic_detector(self):
        profiles = RecognitionProfileRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
            / "routing" / "entry.yaml"
        ).load()
        frame = TemplateObservation({"direct_file_chooser": 1.0})
        result = ComposerTemplateDetector().detect(
            profiles.values(),
            [frame, frame],
        )
        self.assertEqual(result.outcome, "selected")
        self.assertEqual(result.template.template_id, "direct")

    def test_manual_validation_checks_rules_without_candidate_selection(self):
        templates = ComposerTemplateRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
        ).load("reel")
        matched, score, reason = validate_recognition_candidate(
            templates["t2"],
            TemplateObservation({
                "direct_file_chooser": 1.0,
                "final_reel_composer": 0.95,
                "uploaded_media_visible": 0.95,
            }),
        )
        self.assertTrue(matched)
        self.assertGreaterEqual(score, templates["t2"].minimum_score)
        self.assertEqual(reason, "matched")

    def test_detector_selects_stable_post_template(self):
        templates = ComposerTemplateRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_post" / "bundled_default"
        ).load("post")
        frame = TemplateObservation({
            "composer_ready": 0.98,
            "enabled_next_button": 0.96,
            "review_flow_surface": 0.92,
        })
        result = ComposerTemplateDetector().detect(templates.values(), [frame, frame])
        self.assertEqual(result.outcome, "selected")
        self.assertEqual(result.template.template_id, "p2")

    def test_detector_rejects_unstable_choice(self):
        templates = ComposerTemplateRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_post" / "bundled_default"
        ).load("post")
        direct = TemplateObservation({
            "composer_ready": 1.0,
            "enabled_post_button": 1.0,
            "direct_post_surface": 1.0,
        })
        review = TemplateObservation({
            "composer_ready": 1.0,
            "enabled_next_button": 1.0,
            "review_flow_surface": 1.0,
        })
        result = ComposerTemplateDetector().detect(
            templates.values(), [direct, review]
        )
        self.assertEqual(result.outcome, "unstable")

    def test_manual_execution_never_calls_detector(self):
        templates = ComposerTemplateRegistry(
            AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
        ).load("reel")
        provider = Mock()
        provider.call.return_value = CapabilityResult("success")
        detector = Mock()

        result = ComposerTemplateExecutor(provider).run(
            templates["t2"],
            {"inputs": {"media_path": "video.mp4", "caption": "hello"}},
        )

        self.assertEqual(result.outcome, "success")
        detector.detect.assert_not_called()
        self.assertEqual(provider.call.call_count, 4)

    def test_registry_rejects_executable_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "templates").mkdir()
            (root / "templates" / "bad.yaml").write_text(
                """schema_version: 1
template_id: bad
content_family: post
recognition:
  minimum_score: 0.8
  required: [composer_ready]
steps:
  - capability: shell
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TemplateValidationError, "unsupported"):
                ComposerTemplateRegistry(root).load("post")


if __name__ == "__main__":
    unittest.main()
