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
        task.capture_evidence = Mock()
        task.log = Mock()
        task.set_stage = Mock()
        task.set_outcome = Mock(side_effect=lambda status, **_: status == "completed")
        return task

    def test_success_reports_completed_outcome(self):
        task = self.make_task()
        with patch("tasks.facebook_warming.time.sleep"), \
                patch("tasks.facebook_warming.random.randint", return_value=3), \
                patch("tasks.facebook_warming.random.uniform", return_value=1.0):
            self.assertTrue(task.run())

        task.set_stage.assert_called_once_with("warming", scroll_count=2)
        task.set_outcome.assert_called_once_with("completed", scroll_count=2)
        task.verify_logged_in.assert_called_once_with()
        self.assertEqual(task.human.scroll.call_count, 3)

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
