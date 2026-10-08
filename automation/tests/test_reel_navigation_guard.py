import unittest
from unittest.mock import Mock, patch

from modules.publication_result_verifier import ReelPublicationResultVerifier
from modules.publishing_pipeline import PublicationResultVerifierModule


class ReelNavigationGuardTests(unittest.TestCase):
    def test_open_composer_blocks_first_profile_navigation(self):
        task = Mock()
        task._allow_reel_profile_navigation.return_value = False
        self.assertFalse(ReelPublicationResultVerifier(task).verify_latest_reel())
        task.navigate_to.assert_not_called()
        task._scan_profile_for_latest_reel.assert_not_called()

    def test_composer_is_rechecked_before_second_profile_navigation(self):
        task = Mock()
        task._allow_reel_profile_navigation.side_effect = [True, False]
        task._scan_profile_for_latest_reel.return_value = (False, object())
        with patch("modules.publication_result_verifier.time.sleep"):
            self.assertFalse(ReelPublicationResultVerifier(task).verify_latest_reel())
        task.navigate_to.assert_called_once()
        self.assertEqual(task._allow_reel_profile_navigation.call_count, 2)

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


if __name__ == "__main__":
    unittest.main()
