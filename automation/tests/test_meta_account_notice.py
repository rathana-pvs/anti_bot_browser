"""Meta's "Your Meta Account is here" notice is closed, never read as a logout."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import FacebookStateRecognizer, ScreenState
from skills.dialogs import KnownDialogs

NOTICE = [
    {"text": "Your Meta Account is here", "bounds": (802, 404, 1102, 434), "confidence": 0.99},
    {"text": "making it easier to log in, control settings and keep your info secure.", "bounds": (700, 460, 1200, 480), "confidence": 0.98},
]


class RecognitionTests(unittest.TestCase):
    def recognizer(self, items):
        vision = Mock()
        vision.read_text.return_value = items
        return FacebookStateRecognizer(vision)

    def test_notice_is_not_a_login_screen(self):
        for observe in ("observe_session_gate", "observe", "observe_safety_gate"):
            observation = getattr(self.recognizer(NOTICE), observe)(np.zeros((1080, 1920, 3), dtype=np.uint8))
            self.assertEqual(observation.state, ScreenState.UNKNOWN, observe)
            self.assertIn("meta_account_notice", observation.signals)

    def test_a_real_login_page_is_still_a_login_screen(self):
        observation = self.recognizer([{"text": "Log in", "confidence": 0.99}, {"text": "Forgot password?", "confidence": 0.99}]) \
            .observe_session_gate(np.zeros((1080, 1920, 3), dtype=np.uint8))
        self.assertEqual(observation.state, ScreenState.LOGIN_REQUIRED)


class DismissTests(unittest.TestCase):
    def task(self, still_open_after):
        """A notice that closes after `still_open_after` more screenshots."""
        task = SimpleNamespace(log=Mock(), capture_evidence=Mock(), human=Mock(), client=Mock(), vision=Mock())
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        remaining = [still_open_after]

        def read_text(screen, region=None, min_confidence=0.0):
            if remaining[0] < 0:
                return []
            remaining[0] -= 1
            return NOTICE
        task.vision.read_text.side_effect = read_text
        return task

    def test_escape_closes_the_notice(self):
        task = self.task(still_open_after=0)
        with patch("skills.dialogs.time.sleep"):
            self.assertEqual(KnownDialogs(task).dismiss_meta_account_notice(), "dismissed")
        task.human.key_press.assert_called_once_with("Escape")
        task.human.click.assert_not_called()

    def test_the_close_button_is_clicked_when_escape_does_not_work(self):
        task = self.task(still_open_after=1)
        with patch("skills.dialogs.time.sleep"):
            self.assertEqual(KnownDialogs(task).dismiss_meta_account_notice(), "dismissed")
        task.human.click.assert_called_once_with(1224, 369)

    def test_a_notice_that_stays_open_fails_without_other_clicks(self):
        task = self.task(still_open_after=10)
        with patch("skills.dialogs.time.sleep"):
            self.assertEqual(KnownDialogs(task).dismiss_meta_account_notice(), "failed")
        self.assertEqual(task.human.click.call_count, 1)

    def test_absent_notice_does_nothing(self):
        task = self.task(still_open_after=-1)
        task.vision.read_text.side_effect = None
        task.vision.read_text.return_value = []
        self.assertEqual(KnownDialogs(task).dismiss_meta_account_notice(), "absent")
        task.human.key_press.assert_not_called()


if __name__ == "__main__":
    unittest.main()
