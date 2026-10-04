import unittest
from unittest.mock import Mock, patch

from tasks.facebook_warming import FacebookWarmingTask


class FacebookWarmingTaskTests(unittest.TestCase):
    def make_task(self, running=True, scroll_count=2):
        task = FacebookWarmingTask.__new__(FacebookWarmingTask)
        task.profile_id = "profile_test"
        task.scroll_count = scroll_count
        task.client = Mock()
        task.client.container_name = "profile_test"
        task.client.is_running.return_value = running
        task.client.get_screen_dimensions.return_value = (1280, 720)
        task.human = Mock()
        task.verify_logged_in = Mock(return_value=True)
        task.session_check_status = "authenticated"
        task.select_and_open_warming_surface = Mock(
            return_value=("profile", "profile", False, None)
        )
        task.capture_evidence = Mock()
        task.log = Mock()
        task.set_stage = Mock()
        task.set_outcome = Mock(side_effect=lambda status, **_: status == "completed")
        return task

    def test_success_reports_completed_outcome(self):
        task = self.make_task()
        with patch("tasks.facebook_warming.time.sleep"), \
                patch("tasks.facebook_warming.random.randint", return_value=3), \
                patch("tasks.facebook_warming.random.uniform", return_value=1.0), \
                patch("tasks.facebook_warming.random.choice", return_value="profile"):
            self.assertTrue(task.run())

        task.set_stage.assert_called_once_with("warming", scroll_count=2)
        outcome = task.set_outcome.call_args
        self.assertEqual(outcome.args, ("completed",))
        self.assertEqual(outcome.kwargs["scroll_count"], 2)
        self.assertEqual(outcome.kwargs["planned_scroll_count"], 2)
        self.assertEqual(outcome.kwargs["scroll_actions"], 3)
        self.assertEqual(outcome.kwargs["warming_surface"], "profile")
        self.assertFalse(outcome.kwargs["warming_surface_fallback"])
        task.verify_logged_in.assert_called_once_with(
            target_url="https://www.facebook.com/me"
        )
        task.select_and_open_warming_surface.assert_called_once_with(
            requested="profile",
            already_open="profile",
        )
        self.assertEqual(task.human.scroll.call_count, 3)

    def test_random_depth_and_passive_extras(self):
        task = self.make_task()
        task.warming_options = {
            "random_scrolls": True, "min_scrolls": 3, "max_scrolls": 3,
            "surface": "news_feed", "pace": "relaxed", "reread": True,
            "long_breaks": True, "cursor_movement": False, "return_to_top": False,
        }
        task.select_and_open_warming_surface.return_value = ("news_feed", "news_feed", False, None)
        with patch("tasks.facebook_warming.time.sleep") as sleep, \
                patch("tasks.facebook_warming.random.random", return_value=0), \
                patch("tasks.facebook_warming.random.uniform", side_effect=lambda a, b: a):
            self.assertTrue(task.run())
        task.verify_logged_in.assert_called_once_with(target_url="https://www.facebook.com/")
        self.assertEqual(task.set_outcome.call_args.kwargs["planned_scroll_count"], 3)
        self.assertEqual(task.set_outcome.call_args.kwargs["scroll_actions"], 6)
        self.assertEqual([c.args[0] for c in task.human.scroll.call_args_list], ["down", "up"] * 3)
        task.human.move_to.assert_not_called()
        self.assertIn(unittest.mock.call(8.0), sleep.call_args_list)
        task.human.click.assert_not_called()

    def test_budget_stops_browsing_and_clips_pause(self):
        task = self.make_task(scroll_count=10)
        task.warming_options = {"max_seconds": 15, "cursor_movement": False}
        elapsed = [0.0]
        def sleep(seconds):
            elapsed[0] += seconds
        with patch("tasks.facebook_warming.time.monotonic", side_effect=lambda: elapsed[0]), \
                patch("tasks.facebook_warming.time.sleep", side_effect=sleep), \
                patch("tasks.facebook_warming.random.uniform", return_value=10):
            self.assertTrue(task.run())
        result = task.set_outcome.call_args.kwargs
        self.assertEqual(result["duration_seconds"], 15)
        self.assertEqual(result["scroll_count"], 2)
        self.assertEqual(result["scroll_actions"], 2)
        self.assertTrue(result["time_limit_reached"])

    def test_stopped_container_reports_pre_publish_failure(self):
        task = self.make_task(running=False)
        self.assertFalse(task.run())
        task.set_outcome.assert_called_once_with(
            "failed_before_publish",
            reason="profile_container_not_running",
        )
        task.verify_logged_in.assert_not_called()

    def test_logged_out_session_is_safely_skipped(self):
        task = self.make_task()
        task.verify_logged_in.return_value = False
        task.session_check_status = "auth_required"

        self.assertFalse(task.run())

        task.set_outcome.assert_called_once_with(
            "skipped_auth_required",
            error="Facebook is logged out, expired, restricted, or requires an account checkpoint.",
            auth_preflight="auth_required",
        )
        task.human.scroll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
