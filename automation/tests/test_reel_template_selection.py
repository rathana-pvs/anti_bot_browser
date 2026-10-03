from pathlib import Path
from dataclasses import replace
import unittest
from unittest.mock import Mock

from composer_templates import ComposerTemplateDetector, ComposerTemplateRegistry
from composer_templates import RecognitionProfileRegistry
from tasks.facebook_reel import FacebookReelTask


AUTOMATION_ROOT = Path(__file__).resolve().parents[1]


def make_task(selection="auto"):
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.reel_template_selection = selection
    task.reel_template_id = None
    task.template_selection_details = None
    task.entry_detection_details = None
    task.reel_entry_route = None
    task.reel_templates = ComposerTemplateRegistry(
        AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
    ).load("reel")
    task.reel_entry_profiles = RecognitionProfileRegistry(
        AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
        / "routing" / "entry.yaml"
    ).load()
    task.reel_entry_detector = ComposerTemplateDetector()
    task.reel_composer_detector = ComposerTemplateDetector()
    task.log = Mock()
    task._is_reel_next_share_composer = Mock(return_value=False)
    task._is_reel_final_composer = Mock(return_value=True)
    return task


class ReelTemplateSelectionTests(unittest.TestCase):
    def test_manual_template_bypasses_both_selection_detectors(self):
        task = make_task("t2")
        task.reel_entry_detector = Mock()
        task.reel_composer_detector = Mock()

        selected = task._select_manual_template_for_entry("direct_file_chooser")
        valid = task._validate_manual_direct_composer(object())

        self.assertEqual(selected, "t2")
        self.assertTrue(valid)
        task.reel_entry_detector.detect.assert_not_called()
        task.reel_composer_detector.detect.assert_not_called()

    def test_manual_template_rejects_wrong_entry_family(self):
        task = make_task("t1")
        self.assertIsNone(
            task._select_manual_template_for_entry("direct_file_chooser")
        )

    def test_manual_entry_family_comes_from_template_rules_not_template_id(self):
        task = make_task("t4")
        task.reel_templates["t4"] = replace(
            task.reel_templates["t1"],
            template_id="t4",
        )
        self.assertEqual(task._select_manual_template_for_entry("reel_studio"), "t4")

    def test_share_review_behavior_comes_from_template_steps(self):
        task = make_task()
        self.assertFalse(task._reel_template_uses_share_review("t2"))
        self.assertTrue(task._reel_template_uses_share_review("t3"))

    def test_auto_entry_detector_selects_studio_then_t1(self):
        task = make_task("auto")
        self.assertEqual(task._detect_reel_entry_route("reel_studio"), "studio")
        self.assertEqual(task._select_studio_template(), "t1")
        self.assertEqual(
            task.template_selection_details["composer_detection"]["outcome"],
            "not_required",
        )

    def test_auto_uses_second_detector_for_direct_composer(self):
        direct = make_task("auto")
        self.assertEqual(direct._detect_reel_entry_route("direct_file_chooser"), "direct")
        self.assertEqual(direct._detect_direct_composer_template(object()), "t2")

        share = make_task("auto")
        share._is_reel_next_share_composer.return_value = True
        share._is_reel_final_composer.return_value = True
        self.assertEqual(share._detect_reel_entry_route("direct_file_chooser"), "direct")
        self.assertEqual(share._detect_direct_composer_template(object()), "t3")

    def test_manual_direct_validation_rejects_wrong_composer(self):
        task = make_task("t2")
        task._is_reel_next_share_composer.return_value = True
        task._is_reel_final_composer.return_value = True
        self.assertEqual(
            task._select_manual_template_for_entry("direct_file_chooser"),
            "t2",
        )
        self.assertFalse(task._validate_manual_direct_composer(object()))


if __name__ == "__main__":
    unittest.main()
