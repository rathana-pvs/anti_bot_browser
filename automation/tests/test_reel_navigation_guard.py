import unittest
from unittest.mock import Mock, patch

from modules.publication_result_verifier import ReelPublicationResultVerifier
from modules.publishing_pipeline import PublicationResultVerifierModule
from modules.reel_post_click_verifier import ReelPostClickVerifier
from tasks.facebook_reel import FacebookReelTask


class ReelNavigationGuardTests(unittest.TestCase):
    def test_open_composer_blocks_first_profile_navigation(self):
        task = Mock()
        task._allow_reel_profile_navigation.return_value = False
        self.assertFalse(ReelPublicationResultVerifier(task).verify_latest_reel())
        task.navigate_to.assert_not_called()
        task._scan_profile_for_latest_reel.assert_not_called()

    def test_opening_the_profile_marks_the_composer_page_as_left(self):
        task = Mock()
        task._reel_profile_opened = False
        task._allow_reel_profile_navigation.return_value = True
        task._wait_for_reel_profile_ready.return_value = False
        task.reel_profile_readiness = {"accepted": False}
        with patch("modules.publication_result_verifier.time.sleep"):
            self.assertFalse(ReelPublicationResultVerifier(task).verify_latest_reel())
        self.assertIs(task._reel_profile_opened, True)

    def test_closure_is_checked_only_before_the_first_profile_navigation(self):
        task = Mock()
        task._reel_profile_opened = False
        task._uncertain = Mock()
        verifier = ReelPostClickVerifier(task)
        with patch.object(ReelPostClickVerifier, "composer_has_closed", return_value=False):
            self.assertFalse(verifier.allow_profile_navigation())
            task._uncertain.assert_called_once()
            # After the profile opened, Facebook's loading logo screen must not
            # be read as the composer still being open.
            task._reel_profile_opened = True
            task.client.screenshot.reset_mock()
            self.assertTrue(verifier.allow_profile_navigation())
        task.client.screenshot.assert_not_called()
        task._uncertain.assert_called_once()

    def test_pipeline_preserves_uncertainty_when_navigation_is_blocked(self):
        task = Mock()
        task.result_status = "pending_profile_verification"
        task.reel_template_id = None
        task.post_template_id = None
        task.template_selection_details = None
        def blocked():
            task.result_status = "uncertain"
            task.result_error = "reel_composer_closure_unconfirmed"
            return False
        task._refresh_until_latest_reel_visible.side_effect = blocked
        result = PublicationResultVerifierModule(task, "reel").run(Mock())
        self.assertEqual(result.outcome, "uncertain")
        self.assertEqual(task.result_status, "uncertain")
        task._failed_after_publish.assert_not_called()

    def test_each_refresh_gets_fresh_fifteen_second_loading_budget(self):
        for success_on in (None, 2, 3):
            with self.subTest(success_on=success_on):
                task = FacebookReelTask.__new__(FacebookReelTask)
                task.client = Mock()
                task.log = Mock()
                task.human = Mock()
                task.capture_evidence = Mock()
                task._allow_reel_profile_navigation = Mock(return_value=True)
                task._wait_for_reel_profile_ready = Mock(return_value=True)
                task.execute_publish_gate = Mock()
                clock = [0.0]
                navigations = []
                task.navigate_to = Mock(side_effect=lambda *a, **k: navigations.append(clock[0]))

                def read(screen):
                    recent = len(navigations) == success_on and clock[0] - navigations[-1] >= 12
                    task._reel_top_post_found = True
                    task.latest_reel_timestamp_check = {
                        'accepted': recent,
                        'reason': 'recent_top_post' if recent else 'profile_feed_loading',
                    }
                    return recent

                task._latest_reel_is_visible = Mock(side_effect=read)

                def sleep(seconds):
                    clock[0] += seconds

                with patch('tasks.facebook_reel.time.sleep', side_effect=sleep), \
                     patch('tasks.facebook_reel.time.monotonic', side_effect=lambda: clock[0]):
                    visible = ReelPublicationResultVerifier(task).verify_latest_reel()

                self.assertEqual(visible, success_on is not None)
                attempts = success_on or 3
                self.assertEqual(len(navigations), attempts)
                for index, started in enumerate(navigations):
                    self.assertAlmostEqual(started, index * 15.8)
                self.assertAlmostEqual(clock[0], (attempts - 1) * 15.8 + (12.8 if success_on else 15.8))
                self.assertEqual(task.capture_evidence.call_count, attempts)
                self.assertEqual(task._allow_reel_profile_navigation.call_count, attempts)
                task.human.scroll.assert_not_called()
                task.execute_publish_gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
