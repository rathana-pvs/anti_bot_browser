from pathlib import Path
from dataclasses import replace
import unittest
from unittest.mock import Mock

from composer_templates import ComposerTemplateDetector, ComposerTemplateRegistry
from tasks.facebook_reel import FacebookReelTask


AUTOMATION_ROOT = Path(__file__).resolve().parents[1]


def make_task(selection="auto"):
    task = FacebookReelTask.__new__(FacebookReelTask)
    task.reel_template_selection = selection
    task.reel_template_id = None
    task.reel_templates = ComposerTemplateRegistry(
        AUTOMATION_ROOT / "brains" / "facebook_reel" / "bundled_default"
    ).load("reel")
    task.reel_template_detector = ComposerTemplateDetector()
    task.log = Mock()
    task._is_reel_next_share_composer = Mock(return_value=False)
    task._is_reel_final_composer = Mock(return_value=True)
    return task


class ReelTemplateSelectionTests(unittest.TestCase):
    def test_manual_template_bypasses_detector(self):
        task = make_task("t2")
        task.reel_template_detector = Mock()

        selected = task._select_reel_template("direct_file_chooser", object())

        self.assertEqual(selected, "t2")
        task.reel_template_detector.detect.assert_not_called()

    def test_manual_template_rejects_wrong_entry_family(self):
        task = make_task("t1")
        self.assertIsNone(
            task._select_reel_template("direct_file_chooser", object())
        )

    def test_manual_entry_family_comes_from_template_rules_not_template_id(self):
        task = make_task("t4")
        task.reel_templates["t4"] = replace(
            task.reel_templates["t1"],
            template_id="t4",
        )
        self.assertEqual(task._select_reel_template("reel_studio", object()), "t4")

    def test_share_review_behavior_comes_from_template_steps(self):
        task = make_task()
        self.assertFalse(task._reel_template_uses_share_review("t2"))
        self.assertTrue(task._reel_template_uses_share_review("t3"))

    def test_auto_selects_studio_template(self):
        task = make_task("auto")
        self.assertEqual(task._select_reel_template("reel_studio", object()), "t1")

    def test_auto_distinguishes_direct_and_next_share(self):
        direct = make_task("auto")
        self.assertEqual(
            direct._select_reel_template("direct_file_chooser", object()), "t2"
        )

        share = make_task("auto")
        share._is_reel_next_share_composer.return_value = True
        share._is_reel_final_composer.return_value = True
        self.assertEqual(
            share._select_reel_template("direct_file_chooser", object()), "t3"
        )


if __name__ == "__main__":
    unittest.main()
