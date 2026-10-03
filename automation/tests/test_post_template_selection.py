from pathlib import Path
import unittest
from unittest.mock import Mock

from composer_templates import ComposerTemplateDetector, ComposerTemplateRegistry
from tasks.facebook_post import FacebookPostTask


AUTOMATION_ROOT = Path(__file__).resolve().parents[1]


def make_task(next_target=None, post_target=None):
    task = FacebookPostTask.__new__(FacebookPostTask)
    task.brain_targets = {}
    task.post_template_id = None
    task.post_templates = ComposerTemplateRegistry(
        AUTOMATION_ROOT / "brains" / "facebook_post" / "bundled_default"
    ).load("post")
    task.post_template_detector = ComposerTemplateDetector()
    task._stable_blue_text_target = Mock(side_effect=[next_target, post_target])
    task.log = Mock()
    return task


class PostTemplateSelectionTests(unittest.TestCase):
    def test_selects_direct_post_template(self):
        task = make_task(post_target=(900, 700))
        template, next_target, post_target = task._select_post_template()
        self.assertEqual(template, "p1")
        self.assertIsNone(next_target)
        self.assertEqual(post_target, (900, 700))

    def test_selects_next_review_template(self):
        task = make_task(next_target=(900, 700))
        template, next_target, post_target = task._select_post_template()
        self.assertEqual(template, "p2")
        self.assertEqual(next_target, (900, 700))
        self.assertIsNone(post_target)

    def test_unknown_actions_do_not_guess_template(self):
        task = make_task()
        template, _, _ = task._select_post_template()
        self.assertIsNone(template)


if __name__ == "__main__":
    unittest.main()
