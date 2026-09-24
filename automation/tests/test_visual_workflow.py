"""Offline tests for visual state recognition and verification primitives."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import FacebookStateRecognizer, ScreenState, StateObservation
from engine.vision import VisionEngine
from tasks.base_task import BaseTask
from tasks.facebook_post import FacebookPostTask


class VisionPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.profile_id = "offline_test"
        self.vision = VisionEngine(self.client)
        self.screen = np.zeros((200, 300, 3), dtype=np.uint8)

    def test_similarity_detects_identical_and_changed_images(self):
        changed = self.screen.copy()
        changed[50:150, 80:220] = 255
        self.assertAlmostEqual(self.vision.similarity(self.screen, self.screen), 1.0, places=4)
        self.assertLess(self.vision.similarity(self.screen, changed), 0.99)

    def test_find_text_prefers_lower_half(self):
        self.vision.read_text = Mock(
            return_value=[
                {"text": "Post", "confidence": 0.99, "bounds": (10, 10, 50, 30), "center": (30, 20)},
                {"text": "Post", "confidence": 0.90, "bounds": (100, 160, 160, 190), "center": (130, 175)},
            ]
        )
        result = self.vision.find_text("post", screen=self.screen, prefer_lower_half=True)
        self.assertEqual(result["center"], (130, 175))


class StateRecognizerTests(unittest.TestCase):
    def make_vision(self, texts, blue=(150, 170), template=None):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = []
        for text in texts:
            is_action = text.casefold() in ("post", "publish", "next")
            vision.read_text.return_value.append(
                {
                    "text": text,
                    "confidence": 0.95,
                    "bounds": (100, 160, 180, 190) if is_action else (0, 0, 10, 10),
                    "center": (140, 175) if is_action else (5, 5),
                }
            )
        action_text = next((text for text in texts if text.casefold() in ("post", "publish", "next")), None)
        vision.find_text.return_value = (
            {"text": action_text, "confidence": 0.95, "bounds": (100, 160, 180, 190), "center": (140, 175)}
            if action_text
            else None
        )
        vision.find_blue_action_button.return_value = blue
        vision.find_template.return_value = template
        return vision

    def test_login_has_priority(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Log in", "Create new account"]))
        self.assertEqual(recognizer.observe().state, ScreenState.LOGIN_REQUIRED)

    def test_composer_requires_enabled_blue_action(self):
        disabled = FacebookStateRecognizer(self.make_vision(["Create post", "Post"], blue=None))
        enabled = FacebookStateRecognizer(self.make_vision(["Create post", "Post"], blue=(150, 170)))
        self.assertEqual(disabled.observe().state, ScreenState.COMPOSER_OPEN)
        self.assertEqual(enabled.observe().state, ScreenState.POST_ENABLED)

    def test_media_composer_with_next_is_ready(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Create post", "Next"]))
        self.assertEqual(recognizer.observe().state, ScreenState.MEDIA_READY)

    def test_post_settings_review_with_post_is_enabled(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Post settings", "Post preview", "Post"]))
        observation = recognizer.observe()
        self.assertEqual(observation.state, ScreenState.POST_ENABLED)
        self.assertIn("post settings review", observation.signals)

    def test_feed_prompt_alone_is_not_an_open_composer(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["What's on your mind"]))
        self.assertEqual(recognizer.observe().state, ScreenState.FEED_READY)

    def test_feed_prompt_and_unrelated_post_are_not_an_open_composer(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["What's on your mind", "Post"]))
        self.assertEqual(recognizer.observe().state, ScreenState.FEED_READY)

    def test_error_has_priority_over_composer(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Create post", "Something went wrong", "Post"]))
        self.assertEqual(recognizer.observe().state, ScreenState.ERROR_DIALOG)

    def test_success_confirmation(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Your post is now published"]))
        self.assertEqual(recognizer.observe().state, ScreenState.POST_CONFIRMED)

    def test_reel_processing_confirmation(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Your reel is being processed"]))
        self.assertEqual(recognizer.observe().state, ScreenState.POST_CONFIRMED)


class LoginGateTests(unittest.TestCase):
    def make_task(self, observations):
        task = BaseTask.__new__(BaseTask)
        task.client = Mock()
        task.client.navigate_to = Mock()
        task.client.exec_cmd.return_value = Mock(returncode=0, stdout="Facebook")
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.recognizer = Mock()
        task.recognizer.observe.side_effect = observations
        task.capture_evidence = Mock()
        task.log = Mock()
        return task

    def test_loading_screen_is_retried_before_accepting_feed(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.UNKNOWN, 0.0),
                StateObservation(ScreenState.FEED_READY, 0.9, ["photo/video"]),
            ]
        )
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.verify_logged_in())
        self.assertEqual(task.recognizer.observe.call_count, 2)

    def test_actual_login_screen_is_rejected(self):
        task = self.make_task([StateObservation(ScreenState.LOGIN_REQUIRED, 0.98, ["log in"])])
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertFalse(task.verify_logged_in())


class FileChooserTests(unittest.TestCase):
    def test_attachment_clicks_visual_open_without_return(self):
        task = BaseTask.__new__(BaseTask)
        task.client = Mock()
        task.human = Mock()
        task.vision = Mock()
        task.log = Mock()
        task.capture_evidence = Mock()
        dialog_visible = {"value": True}

        def exec_cmd(args, check=False):
            if args[:4] == ["xdotool", "search", "--onlyvisible", "--name"]:
                return Mock(returncode=0 if dialog_visible["value"] else 1, stdout="42\n" if dialog_visible["value"] else "")
            if args[:3] == ["xdotool", "getwindowgeometry", "--shell"]:
                return Mock(returncode=0, stdout="X=100\nY=100\nWIDTH=1000\nHEIGHT=700\n")
            if args[:2] == ["test", "-f"]:
                return Mock(returncode=0, stdout="")
            if args[:3] == ["xdotool", "search", "--class"]:
                return Mock(returncode=0, stdout="99\n")
            return Mock(returncode=0, stdout="")

        task.client.exec_cmd.side_effect = exec_cmd
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.find_text.return_value = {"center": (1020, 760), "text": "Open", "confidence": 0.99}
        task.human.click.side_effect = lambda *_: dialog_visible.update(value=False)

        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.attach_file_gtk("/data/shared_media/photo.png"))

        task.human.click.assert_called_once_with(1020, 760)
        task.human.key_press.assert_called_once_with("ctrl+l")


class CaptionTargetTests(unittest.TestCase):
    def make_task(self, detected_text):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.client = Mock()
        task.vision = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.find_blue_action_button.return_value = (952, 840)
        task.vision.read_text.return_value = [
            {"text": "AI label off", "center": (919, 400)},
            {"text": detected_text, "center": (787, 437)},
        ]
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        return task

    def test_caption_target_tolerates_apostrophe_ocr_noise(self):
        task = self.make_task("What'$ on your mind,")

        self.assertEqual(task._stable_caption_target(), (787, 437))
        region = task.vision.read_text.call_args.kwargs["region"]
        self.assertTrue(region[0] <= 787 <= region[0] + region[2])
        self.assertTrue(region[1] <= 437 <= region[1] + region[3])

    def test_caption_target_accepts_whats_as_one_ocr_token(self):
        task = self.make_task("Whats on your mind, name?")
        self.assertEqual(task._stable_caption_target(), (787, 437))


class FirstCommentTargetTests(unittest.TestCase):
    def test_uses_topmost_comment_as_field(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "Comment as Page", "center": (900, 820), "confidence": 0.95},
            {"text": "Comment as Page", "center": (900, 620), "confidence": 0.92},
            {"text": "Manage posts", "center": (1100, 100), "confidence": 0.99},
        ]
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(task._find_first_comment_input(screen), (900, 620))


if __name__ == "__main__":
    unittest.main()
