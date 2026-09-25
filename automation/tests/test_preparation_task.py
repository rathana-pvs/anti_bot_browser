import os
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tasks.facebook_preparation import FacebookPreparationTask


class FacebookPreparationTaskTests(unittest.TestCase):
    def make_task(self, logged_in=True):
        task = FacebookPreparationTask.__new__(FacebookPreparationTask)
        task.mode = "brief"
        task.client = Mock()
        task.client.is_running.return_value = True
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.client.container_name = "isolated_profile_test"
        task.vision = Mock()
        task.vision.screen_similarity.return_value = 0.5
        task.human = Mock()
        task.verify_logged_in = Mock(return_value=logged_in)
        task._current_session_is_stable = Mock(return_value=logged_in)
        task.log = Mock()
        task.set_stage = Mock()
        task.capture_evidence = Mock()
        task.set_outcome = Mock(return_value=logged_in)
        return task

    def test_zero_duration_preparation_reaches_ready_without_scroll(self):
        task = self.make_task(logged_in=True)
        with patch.object(FacebookPreparationTask, "DURATION_RANGES", {"brief": (0.0, 0.0), "extended": (0.0, 0.0)}):
            result = task.run()

        self.assertTrue(result)
        task.verify_logged_in.assert_not_called()
        task.set_stage.assert_any_call("preparing", preparation_mode="brief")
        task.set_stage.assert_any_call("ready", preparation_mode="brief")
        task.capture_evidence.assert_called_once()
        task.human.scroll.assert_not_called()
        task.human.key_press.assert_not_called()

    def test_preparation_scrolls_without_engagement_and_returns_home(self):
        task = self.make_task(logged_in=True)
        clock = iter([0.0, 0.0, 0.0, 1.0, 1.0])
        with patch.object(FacebookPreparationTask, "DURATION_RANGES", {"brief": (1.0, 1.0), "extended": (1.0, 1.0)}), \
             patch("tasks.facebook_preparation.time.time", side_effect=lambda: next(clock)), \
             patch("tasks.facebook_preparation.time.sleep", return_value=None), \
             patch("tasks.facebook_preparation.random.randint", return_value=3):
            result = task.run()

        self.assertTrue(result)
        task.human.scroll.assert_called_once_with("down", notches=3)
        task.human.key_press.assert_called_once_with("Home")

    def test_unverified_session_stops_for_review(self):
        task = self.make_task(logged_in=False)

        result = task.run()

        self.assertFalse(result)
        task.verify_logged_in.assert_called_once()
        task.set_outcome.assert_called_once()
        self.assertEqual(task.set_outcome.call_args.args[0], "needs_review")
        task.client.screenshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
