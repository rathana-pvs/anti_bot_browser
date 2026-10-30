import os
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tasks.facebook_preparation import FacebookPreparationTask
from tasks.base_task import BaseTask
from engine.vision import VisionEngine
from engine.behavior_profile import BehaviorSession


class FacebookPreparationTaskTests(unittest.TestCase):
    def make_task(self, logged_in=True):
        task = FacebookPreparationTask.__new__(FacebookPreparationTask)
        task.mode = "brief"
        task.behavior_session = BehaviorSession("medium", 123, "preparation-test")
        task.client = Mock()
        task.client.is_running.return_value = True
        task.client.get_mouse_position.return_value = (50, 6)
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.client.container_name = "isolated_profile_test"
        # A spec catches calls to methods that do not exist on the real engine.
        task.vision = Mock(spec=VisionEngine)
        task.vision.similarity.return_value = 0.5
        task.human = Mock()
        task.verify_logged_in = Mock(return_value=logged_in)
        task.session_check_status = "authenticated" if logged_in else "auth_required"
        task._current_session_is_stable = Mock(return_value=logged_in)
        task.select_and_open_warming_surface = Mock(
            return_value=(
                "news_feed",
                "news_feed",
                False,
                task.client.screenshot.return_value,
            )
        )
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
        task.set_stage.assert_any_call(
            "ready",
            preparation_mode="brief",
            warming_surface="news_feed",
        )
        task.capture_evidence.assert_called_once()
        task.human.scroll.assert_not_called()
        task.human.key_press.assert_not_called()

    def test_preparation_scrolls_over_content_and_returns_in_bursts(self):
        task = self.make_task(logged_in=True)
        elapsed = [0.0]
        with patch.object(FacebookPreparationTask, "DURATION_RANGES", {"brief": (1.0, 1.0), "extended": (1.0, 1.0)}), \
             patch("tasks.facebook_preparation.time.monotonic", side_effect=lambda: elapsed[0]), \
             patch("tasks.facebook_preparation.time.sleep", side_effect=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)), \
             patch.object(task.behavior_session.stream("warming"), "choice", return_value=3):
            result = task.run()

        self.assertTrue(result)
        commands = task.human.method_calls
        self.assertEqual(commands[0][0], "move_to")
        x, y = commands[0].args
        self.assertTrue(45 <= x <= 78 and 40 <= y <= 80)
        self.assertEqual(commands[1], unittest.mock.call.scroll("down", notches=3))
        scrolls = task.human.scroll.call_args_list
        self.assertEqual(sum(c.kwargs["notches"] for c in scrolls if c.args[0] == "up"), 3)
        self.assertTrue(all(c.kwargs["notches"] <= 4 for c in scrolls))
        task.human.key_press.assert_not_called()
        task.human.click.assert_not_called()
        self.assertTrue(any("remaining" in call.args[1] for call in task.log.call_args_list))
        self.assertEqual(task.set_outcome.call_args.kwargs["return_scroll_notches"], 3)
        self.assertEqual(task.set_outcome.call_args.kwargs["visible_scroll_changes"], 1)

    def test_logged_out_session_is_safely_skipped(self):
        task = self.make_task(logged_in=False)

        with patch("tasks.facebook_preparation.random.choice", return_value="news_feed"):
            result = task.run()

        self.assertFalse(result)
        task.verify_logged_in.assert_called_once_with(
            target_url="https://www.facebook.com/"
        )
        task.set_outcome.assert_called_once()
        self.assertEqual(task.set_outcome.call_args.args[0], "skipped_auth_required")
        task.client.screenshot.assert_not_called()

    def test_unstable_session_verification_reuses_selected_surface(self):
        task = self.make_task(logged_in=True)
        task._current_session_is_stable.return_value = False

        with patch("tasks.facebook_preparation.random.choice", return_value="profile"), \
             patch.object(FacebookPreparationTask, "DURATION_RANGES", {"brief": (0.0, 0.0), "extended": (0.0, 0.0)}):
            result = task.run()

        self.assertTrue(result)
        task.verify_logged_in.assert_called_once_with(
            target_url="https://www.facebook.com/me"
        )
        task.select_and_open_warming_surface.assert_called_once_with(
            requested="profile",
            already_open="profile",
        )

    def test_surface_choice_allows_profile_and_same_choice_on_future_runs(self):
        task = self.make_task(logged_in=True)
        task.select_and_open_warming_surface = BaseTask.select_and_open_warming_surface.__get__(task)
        task.open_warming_surface = Mock(return_value=(True, task.client.screenshot.return_value))

        with patch("skills.navigation.random.choice", return_value="profile"):
            first = task.select_and_open_warming_surface()
            second = task.select_and_open_warming_surface()

        self.assertEqual(first[:3], ("profile", "profile", False))
        self.assertEqual(second[:3], ("profile", "profile", False))
        self.assertEqual(task.open_warming_surface.call_args_list, [
            unittest.mock.call("profile"),
            unittest.mock.call("profile"),
        ])

    def test_surface_choice_uses_other_surface_once_as_fallback(self):
        task = self.make_task(logged_in=True)
        task.select_and_open_warming_surface = BaseTask.select_and_open_warming_surface.__get__(task)
        task.open_warming_surface = Mock(side_effect=[
            (False, task.client.screenshot.return_value),
            (True, task.client.screenshot.return_value),
        ])

        with patch("skills.navigation.random.choice", return_value="news_feed"):
            result = task.select_and_open_warming_surface()

        self.assertEqual(result[:3], ("news_feed", "profile", True))
        self.assertEqual(task.open_warming_surface.call_args_list, [
            unittest.mock.call("news_feed"),
            unittest.mock.call("profile"),
        ])

    def test_surface_choice_reuses_already_verified_page(self):
        task = self.make_task(logged_in=True)
        task.select_and_open_warming_surface = BaseTask.select_and_open_warming_surface.__get__(task)
        task.open_warming_surface = Mock(return_value=(True, task.client.screenshot.return_value))

        result = task.select_and_open_warming_surface(
            requested="profile",
            already_open="profile",
        )

        self.assertEqual(result[:3], ("profile", "profile", False))
        task.open_warming_surface.assert_called_once_with("profile", navigate=False)

    def test_preparation_retries_surfaces_once_and_recovers(self):
        task = self.make_task()
        screen = task.client.screenshot.return_value
        task.select_and_open_warming_surface.side_effect = [
            ('profile', None, True, screen), ('profile', 'news_feed', True, screen),
        ]
        with patch.object(FacebookPreparationTask, 'DURATION_RANGES', {'brief': (0.0, 0.0)}):
            self.assertTrue(task.run())
        self.assertEqual(task.select_and_open_warming_surface.call_args_list, [
            unittest.mock.call(requested=None, already_open=None),
            unittest.mock.call(requested='profile', already_open=None),
        ])
        self.assertEqual(task.set_outcome.call_args.args[0], 'completed')
        self.assertEqual(task.set_outcome.call_args.kwargs['warming_surface_attempts'], 2)
        self.assertEqual(task.capture_evidence.call_args_list[0].args[0], 'preparation_surface_not_ready_1')
        task.human.click.assert_not_called()

    def test_second_attempt_reloads_original_page_and_stops_after_four_checks(self):
        task = self.make_task()
        task._current_session_is_stable.return_value = False
        task.select_and_open_warming_surface = BaseTask.select_and_open_warming_surface.__get__(task)
        task.open_warming_surface = Mock(return_value=(False, task.client.screenshot.return_value))
        task.set_outcome.return_value = False
        with patch('tasks.facebook_preparation.random.choice', return_value='profile'):
            self.assertFalse(task.run())
        self.assertEqual(task.open_warming_surface.call_args_list, [
            unittest.mock.call('profile', navigate=False), unittest.mock.call('news_feed'),
            unittest.mock.call('profile'), unittest.mock.call('news_feed'),
        ])
        self.assertEqual(task.set_outcome.call_args.args[0], 'failed_before_publish')
        self.assertEqual(task.set_outcome.call_args.kwargs['warming_surface_attempts'], 2)
        self.assertEqual([c.args[0] for c in task.capture_evidence.call_args_list], [
            'preparation_surface_not_ready_1', 'preparation_surface_not_ready_2',
        ])
        task.human.scroll.assert_not_called()

    def test_checkpoint_during_surface_check_skips_without_fallback_or_retry(self):
        task = self.make_task()
        task.select_and_open_warming_surface = BaseTask.select_and_open_warming_surface.__get__(task)

        def blocked(*args, **kwargs):
            task.session_check_status = 'auth_required'
            return False, task.client.screenshot.return_value

        task.open_warming_surface = Mock(side_effect=blocked)
        task.set_outcome.return_value = False
        with patch('skills.navigation.random.choice', return_value='profile'):
            self.assertFalse(task.run())
        task.open_warming_surface.assert_called_once_with('profile')
        self.assertEqual(task.set_outcome.call_args.args[0], 'skipped_auth_required')


if __name__ == "__main__":
    unittest.main()
