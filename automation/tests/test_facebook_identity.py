"""The login check tells a Page from the personal profile behind it."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.outcome import PublicationStatus
from engine.screen_state import ScreenState, StateObservation
from skills.session import SessionCheck, facebook_identity


def items(*texts):
    return [{"text": text} for text in texts]


class FacebookIdentityTests(unittest.TestCase):
    def test_page_management_marks_a_page(self):
        self.assertEqual(facebook_identity(items("Manage Page", "Dyonte Crawford", "2.9K followers • 0 following")), "page")
        self.assertEqual(facebook_identity(items("Creator Dashboard", "Edit", "Advertise")), "page")

    def test_friend_count_or_story_marks_the_personal_profile(self):
        self.assertEqual(facebook_identity(items("Sor R", "1 friend", "Add to story", "Edit profile")), "personal")
        self.assertEqual(facebook_identity(items("Memories", "Saved", "Groups")), "personal")

    def test_unclear_screens_stay_unknown(self):
        self.assertIsNone(facebook_identity(items("Facebook", "Search Facebook", "Reels")))
        self.assertIsNone(facebook_identity([]))


class SkipPersonalProfileTests(unittest.TestCase):
    def task(self, texts):
        task = SimpleNamespace(log=Mock(), capture_evidence=Mock(), detect_visual_theme=Mock(), navigate_to=Mock(),
                               handle_leave_site_dialog=Mock(return_value=False), client=Mock(), recognizer=Mock(), vision=Mock(),
                               _is_facebook_url=lambda url: True, _has_strong_facebook_visual=lambda observation: True)
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.client.get_current_url.return_value = "https://www.facebook.com/profile.php?id=1"
        task.recognizer.observe_session_gate.return_value = StateObservation(ScreenState.FEED_READY, 0.9, ["photo/video"])
        task.vision.read_text.return_value = items(*texts)
        task.set_outcome = Mock(return_value=False)
        return task

    def test_the_personal_profile_stops_the_job_before_anything_is_done(self):
        task = self.task(["Sor R", "1 friend", "Add to story"])
        with patch("skills.session.time.sleep"):
            self.assertFalse(SessionCheck(task).verify("https://www.facebook.com/me"))
        self.assertEqual(task.session_check_status, "personal_profile")
        SessionCheck(task).skip_unverified()
        self.assertEqual(task.set_outcome.call_args.args[0], "skipped_personal_profile")
        self.assertEqual(PublicationStatus("skipped_personal_profile"), PublicationStatus.SKIPPED_PERSONAL_PROFILE)

    def test_the_page_continues(self):
        task = self.task(["Manage Page", "2.9K followers"])
        with patch("skills.session.time.sleep"):
            self.assertTrue(SessionCheck(task).verify("https://www.facebook.com/me"))
        self.assertEqual(task.facebook_identity, "page")


if __name__ == "__main__":
    unittest.main()
