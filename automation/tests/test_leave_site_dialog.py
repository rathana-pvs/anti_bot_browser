"""Unit tests for Chrome Leave Site modal dialog detection and automatic dismissal.

Tests:
1. VisionEngine.find_leave_site_button detects "Leave site?" and "Leave" button.
2. VisionEngine.find_leave_site_button detects "Changes you made may not be saved" and "Leave" button.
3. VisionEngine.find_leave_site_button falls back to blue button when leave prompt is present.
4. VisionEngine.find_leave_site_button returns None on normal feed screen.
5. BaseTask.handle_leave_site_dialog clicks coordinates and logs evidence.
6. BaseTask.handle_leave_site_dialog falls back to dismiss_dialog_key if human is missing.
7. BaseTask.navigate_to and refresh_page automatically trigger handle_leave_site_dialog.
8. BaseTask.wait_for_states unblocks when an unknown screen is resolved by dismissing dialog.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import ScreenState, StateObservation
from engine.vision import VisionEngine
from tasks.base_task import BaseTask


class VisionLeaveSiteTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.profile_id = "test_leave_profile"
        self.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.vision = VisionEngine(self.client)

    def test_find_leave_site_button_with_prompt_and_leave_text(self):
        dummy_screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mock_ocr = [
            {"text": "Leave site?", "center": (960, 120), "confidence": 0.99},
            {"text": "Changes you made may not be saved.", "center": (960, 150), "confidence": 0.95},
            {"text": "Cancel", "center": (880, 200), "confidence": 0.98},
            {"text": "Leave", "center": (1040, 200), "confidence": 0.99},
        ]
        with patch.object(self.vision, "read_text", return_value=mock_ocr):
            coords = self.vision.find_leave_site_button(dummy_screen)
            self.assertEqual(coords, (1040, 200))

    def test_find_leave_site_button_with_cancel_and_leave(self):
        dummy_screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mock_ocr = [
            {"text": "Cancel", "center": (880, 200), "confidence": 0.95},
            {"text": "Leave", "center": (1040, 200), "confidence": 0.95},
        ]
        with patch.object(self.vision, "read_text", return_value=mock_ocr):
            coords = self.vision.find_leave_site_button(dummy_screen)
            self.assertEqual(coords, (1040, 200))

    def test_find_leave_site_fallback_to_blue_button(self):
        dummy_screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mock_ocr = [
            {"text": "Leave site?", "center": (960, 120), "confidence": 0.95},
            {"text": "Changes you made may not be saved", "center": (960, 150), "confidence": 0.95},
        ]
        with patch.object(self.vision, "read_text", return_value=mock_ocr), \
             patch.object(self.vision, "find_blue_action_button", return_value=(1035, 202)):
            coords = self.vision.find_leave_site_button(dummy_screen)
            self.assertEqual(coords, (1035, 202))

    def test_find_leave_site_returns_none_on_normal_screen(self):
        dummy_screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        mock_ocr = [
            {"text": "What's on your mind?", "center": (500, 300), "confidence": 0.95},
            {"text": "Photo/video", "center": (500, 400), "confidence": 0.95},
        ]
        with patch.object(self.vision, "read_text", return_value=mock_ocr):
            coords = self.vision.find_leave_site_button(dummy_screen)
            self.assertIsNone(coords)

    def test_find_leave_site_returns_none_on_empty_screen(self):
        coords = self.vision.find_leave_site_button(np.array([]))
        self.assertIsNone(coords)


class BaseTaskLeaveSiteTests(unittest.TestCase):
    def setUp(self):
        self.task = BaseTask.__new__(BaseTask)
        self.task.profile_id = "test_profile"
        self.task.client = Mock()
        self.task.human = Mock()
        self.task.vision = Mock()
        self.task.recognizer = Mock()
        self.task.evidence = Mock()
        self.task.logs = []
        self.task.stage_history = []
        self.task.log = Mock()
        self.task.capture_evidence = Mock()

    def test_handle_leave_site_dialog_clicks_and_returns_true(self):
        self.task.vision.find_leave_site_button.return_value = (1020, 200)
        with patch("time.sleep", return_value=None):
            result = self.task.handle_leave_site_dialog()
        self.assertTrue(result)
        self.task.human.click.assert_called_once_with(1020, 200)
        self.task.capture_evidence.assert_called_once_with("dismiss_leave_site_dialog", screen=None)

    def test_handle_leave_site_dialog_falls_back_to_client_key(self):
        self.task.vision.find_leave_site_button.return_value = (1020, 200)
        self.task.human = None
        with patch("time.sleep", return_value=None):
            result = self.task.handle_leave_site_dialog()
        self.assertTrue(result)
        self.task.client.dismiss_dialog_key.assert_called_once()

    def test_handle_leave_site_dialog_returns_false_when_absent(self):
        self.task.vision.find_leave_site_button.return_value = None
        result = self.task.handle_leave_site_dialog()
        self.assertFalse(result)
        self.task.human.click.assert_not_called()

    def test_navigate_to_calls_client_and_checks_leave_dialog(self):
        with patch.object(self.task, "handle_leave_site_dialog") as mock_handler, \
             patch("time.sleep", return_value=None):
            self.task.navigate_to("https://www.facebook.com/me", wait_seconds=2.0)
            self.task.client.navigate_to.assert_called_once_with("https://www.facebook.com/me")
            mock_handler.assert_called_once()

    def test_refresh_page_calls_client_and_checks_leave_dialog(self):
        with patch.object(self.task, "handle_leave_site_dialog") as mock_handler, \
             patch("time.sleep", return_value=None):
            self.task.refresh_page(wait_seconds=2.0)
            self.task.client.refresh_page.assert_called_once()
            mock_handler.assert_called_once()

    def test_wait_for_states_unblocks_when_leave_dialog_is_dismissed(self):
        screen_mock = np.zeros((100, 100, 3), dtype=np.uint8)
        self.task.client.screenshot.return_value = screen_mock
        # Sequence: UNKNOWN (blocked by dialog) -> FEED_READY
        self.task.recognizer.observe.side_effect = [
            StateObservation(ScreenState.UNKNOWN, 0.0),
            StateObservation(ScreenState.FEED_READY, 0.95),
        ]
        with patch.object(self.task, "handle_leave_site_dialog", return_value=True) as mock_dismiss, \
             patch("time.sleep", return_value=None):
            obs, screen = self.task.wait_for_states(
                expected={ScreenState.FEED_READY},
                timeout=5.0,
                poll_interval=0.1,
            )
            self.assertEqual(obs.state, ScreenState.FEED_READY)
            mock_dismiss.assert_called_once_with(screen_mock)


if __name__ == "__main__":
    unittest.main()
