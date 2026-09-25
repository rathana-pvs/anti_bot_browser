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
from tasks.facebook_reel import FacebookReelTask


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
        ocr_items = []
        for text in texts:
            is_action = text.casefold() in ("post", "publish", "next")
            ocr_items.append(
                {
                    "text": text,
                    "confidence": 0.95,
                    "bounds": (100, 160, 180, 190) if is_action else (0, 0, 10, 10),
                    "center": (140, 175) if is_action else (5, 5),
                }
            )
        def read_text(_screen, region=None, **_kwargs):
            if region is None:
                return ocr_items
            x, y, width, height = region
            return [
                item for item in ocr_items
                if x <= item["center"][0] <= x + width
                and y <= item["center"][1] <= y + height
            ]
        vision.read_text.side_effect = read_text
        action_text = next((text for text in texts if text.casefold() in ("post", "publish", "next")), None)
        vision.find_text.return_value = (
            {"text": action_text, "confidence": 0.95, "bounds": (100, 160, 180, 190), "center": (140, 175)}
            if action_text
            else None
        )
        vision.find_blue_action_button.return_value = blue
        vision.find_blue_action_buttons.return_value = (
            [{"center": blue, "bounds": (100, 155, 200, 190), "area": 3500}]
            if blue
            else []
        )
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

    def test_media_composer_with_separated_create_and_post_tokens(self):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = [
            {"text": "Create", "confidence": 0.95, "bounds": (40, 20, 70, 35), "center": (55, 27)},
            {"text": "something in between", "confidence": 0.90, "bounds": (0, 0, 10, 10), "center": (5, 5)},
            {"text": "post", "confidence": 0.95, "bounds": (80, 20, 110, 35), "center": (95, 27)},
            {"text": "Next", "confidence": 0.95, "bounds": (100, 160, 180, 190), "center": (140, 175)},
        ]
        vision.find_text.return_value = {"text": "Next", "confidence": 0.95, "bounds": (100, 160, 180, 190), "center": (140, 175)}
        vision.find_blue_action_button.return_value = (150, 170)
        recognizer = FacebookStateRecognizer(vision)
        self.assertEqual(recognizer.observe().state, ScreenState.MEDIA_READY)

    def test_media_composer_with_modal_marker_and_blue_next(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["AI label", "Next"]))
        self.assertEqual(recognizer.observe().state, ScreenState.MEDIA_READY)

    def test_post_settings_review_with_post_is_enabled(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Post settings", "Post preview", "Post"]))
        observation = recognizer.observe()
        self.assertEqual(observation.state, ScreenState.POST_ENABLED)
        self.assertIn("post settings review", observation.signals)

    def test_post_settings_review_uses_unique_blue_footer_when_ocr_misses_post(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Post settings", "Post preview"]))
        observation = recognizer.observe()
        self.assertEqual(observation.state, ScreenState.POST_ENABLED)
        self.assertIn("unique review-modal footer action", observation.signals)

    def test_post_settings_review_rejects_ambiguous_blue_actions_without_post_text(self):
        vision = self.make_vision(["Post settings", "Post preview"])
        vision.find_blue_action_buttons.return_value = [
            {"center": (150, 170), "bounds": (100, 155, 200, 190), "area": 3500},
            {"center": (50, 170), "bounds": (10, 155, 90, 190), "area": 2800},
        ]
        observation = FacebookStateRecognizer(vision).observe()
        self.assertEqual(observation.state, ScreenState.COMPOSER_OPEN)

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


class PostPublishPromptTests(unittest.TestCase):
    def make_task(self, ocr):
        task = BaseTask.__new__(BaseTask)
        task.client = Mock()
        task.human = Mock()
        task.vision = Mock()
        task.log = Mock()
        task.capture_evidence = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.read_text.return_value = ocr
        return task

    def test_clicks_not_now_on_optional_post_publish_prompt(self):
        task = self.make_task([
            {"text": "Speak With People Directly", "confidence": 0.90, "center": (950, 300)},
            {"text": "Not now", "confidence": 0.99, "center": (850, 800)},
            {"text": "Add Button", "confidence": 0.95, "center": (1050, 800)},
        ])
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.check_and_dismiss_post_prompt())
        task.human.click.assert_called_once_with(850, 800)
        self.assertEqual(task.vision.read_text.call_args.kwargs["region"], (288, 86, 1344, 939))

    def test_tolerates_zero_in_not_now_ocr(self):
        task = self.make_task([
            {"text": "Not n0w", "confidence": 0.51, "center": (850, 800)},
        ])
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.check_and_dismiss_post_prompt())
        task.human.click.assert_called_once_with(850, 800)


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
        task.human.key_press.assert_has_calls([
            unittest.mock.call("ctrl+l"),
            unittest.mock.call("ctrl+a"),
            unittest.mock.call("BackSpace"),
        ])


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


class ActionTargetTests(unittest.TestCase):
    def make_task(self, button_text, confidence=0.95):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.client = Mock()
        task.vision = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.find_blue_action_buttons.return_value = [
            {
                "center": (1072, 858),
                "bounds": (958, 840, 1186, 877),
                "area": 8436,
            }
        ]
        task.vision.read_text.return_value = [
            {
                "text": button_text,
                "confidence": confidence,
                "bounds": (1050, 848, 1094, 868),
                "center": (1072, 858),
            }
        ]
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        return task

    def test_next_target_does_not_accept_a_blue_post_button(self):
        task = self.make_task("Post")
        self.assertIsNone(task._stable_blue_text_target(("next",)))

    def test_low_confidence_exact_post_inside_button_is_accepted(self):
        task = self.make_task("Post", confidence=0.20)
        self.assertEqual(task._stable_blue_text_target(("post", "publish")), (1072, 858))
        self.assertEqual(
            task.vision.read_text.call_args.kwargs["region"],
            (958, 840, 228, 37),
        )

    def test_caption_text_containing_publish_is_not_a_button_label(self):
        task = self.make_task("beginning: publish and first comment")
        task.vision.read_text.return_value = []
        self.assertIsNone(task._stable_blue_text_target(("post", "publish")))


class PublicationVerificationTests(unittest.TestCase):
    def make_task(self, observations, similarities):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.caption = "new post caption"
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.recognizer = Mock()
        task.recognizer.observe.side_effect = observations
        task.vision = Mock()
        task.vision.similarity.side_effect = similarities
        task.check_and_dismiss_post_prompt = Mock(return_value=False)
        task._is_recent_post_on_screen = Mock(return_value=False)
        task.log = Mock()
        return task

    def test_timeout_without_confirmed_modal_closure_is_uncertain(self):
        task = self.make_task(
            [StateObservation(ScreenState.UNKNOWN, 0.2)],
            [0.50],
        )
        with unittest.mock.patch("tasks.facebook_post.time.time", side_effect=[0.0, 0.0, 2.0]), \
             unittest.mock.patch("tasks.facebook_post.time.sleep", return_value=None):
            status, _ = task._verify_publication(np.zeros((100, 100, 3), dtype=np.uint8), timeout=1.0)
        self.assertEqual(status, "uncertain")

    def test_post_publish_prompt_is_dismissed_before_state_checks(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.FEED_READY, 0.8),
                StateObservation(ScreenState.FEED_READY, 0.8),
            ],
            [0.80, 0.80],
        )
        task.check_and_dismiss_post_prompt.side_effect = [True]
        with unittest.mock.patch("tasks.facebook_post.time.time", side_effect=[0.0, 0.0, 0.2, 0.4]), \
             unittest.mock.patch("tasks.facebook_post.time.sleep", return_value=None):
            status, _ = task._verify_publication(np.zeros((100, 100, 3), dtype=np.uint8), timeout=1.0)
        self.assertEqual(status, "published")
        task.check_and_dismiss_post_prompt.assert_called_once()

    def test_posting_state_does_not_use_caption_as_confirmation(self):
        task = self.make_task(
            [StateObservation(ScreenState.PUBLISHING, 0.9)],
            [0.50],
        )
        with unittest.mock.patch("tasks.facebook_post.time.time", side_effect=[0.0, 0.0, 2.0]), \
             unittest.mock.patch("tasks.facebook_post.time.sleep", return_value=None):
            status, _ = task._verify_publication(np.zeros((100, 100, 3), dtype=np.uint8), timeout=1.0)
        self.assertEqual(status, "uncertain")
        task._is_recent_post_on_screen.assert_not_called()

    def test_two_changed_feed_observations_confirm_modal_closed(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.FEED_READY, 0.8),
                StateObservation(ScreenState.FEED_READY, 0.8),
            ],
            [0.80, 0.80],
        )
        with unittest.mock.patch("tasks.facebook_post.time.time", side_effect=[0.0, 0.0, 0.5]), \
             unittest.mock.patch("tasks.facebook_post.time.sleep", return_value=None):
            status, _ = task._verify_publication(np.zeros((100, 100, 3), dtype=np.uint8), timeout=1.0)
        self.assertEqual(status, "published")

    def test_confirmation_still_waits_for_profile_feed(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.POST_CONFIRMED, 0.98),
                StateObservation(ScreenState.FEED_READY, 0.8),
                StateObservation(ScreenState.FEED_READY, 0.8),
            ],
            [0.70, 0.70, 0.70],
        )
        with unittest.mock.patch("tasks.facebook_post.time.time", side_effect=[0.0, 0.0, 0.2, 0.4]), \
             unittest.mock.patch("tasks.facebook_post.time.sleep", return_value=None):
            status, final = task._verify_publication(np.zeros((100, 100, 3), dtype=np.uint8), timeout=1.0)
        self.assertEqual(status, "published")
        self.assertEqual(final[0].state, ScreenState.FEED_READY)
        self.assertEqual(task.recognizer.observe.call_count, 3)


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

    def test_reel_task_inherits_first_comment_support(self):
        reel_task = FacebookReelTask.__new__(FacebookReelTask)
        reel_task.vision = Mock()
        reel_task.vision.read_text.return_value = [
            {"text": "Comment as Creator", "center": (900, 750), "confidence": 0.94},
            {"text": "Posts", "center": (300, 400), "confidence": 0.99},
        ]
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(reel_task._find_first_comment_input(screen), (900, 750))

    def test_post_first_comment_successful_flow(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.vision = Mock()
        task.vision.read_text.return_value = []
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(return_value=((900, 750), np.zeros((100, 100, 3))))

        with unittest.mock.patch("time.sleep", return_value=None), \
             unittest.mock.patch("tasks.base_task.random.uniform", return_value=2.5):
            result = task.post_first_comment("https://example.com/link")

        self.assertEqual(result, "submitted_unverified")
        task.human.click.assert_has_calls([
            unittest.mock.call(900, 750),
            unittest.mock.call(20, 50),
        ])
        task.paste_text.assert_called_once_with("https://example.com/link")
        task.human.key_press.assert_called_once_with("Return")
        self.assertEqual(task.current_stage, "warming")
        self.assertEqual(task.stage_history[-1]["seconds"], 2.5)
        task.capture_evidence.assert_any_call(
            "after_comment_modal_close",
            task.client.screenshot.return_value,
        )

    def test_comment_modal_stays_open_while_submission_is_pending(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "Posting...", "confidence": 0.99, "center": (60, 80)},
        ]
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(
            return_value=((90, 75), np.zeros((100, 100, 3)))
        )

        with unittest.mock.patch("time.sleep", return_value=None):
            result = task.post_first_comment("https://example.com/link")

        self.assertEqual(result, "submission_pending")
        task.human.click.assert_called_once_with(90, 75)

    def test_vision_find_comment_input_locates_pill(self):
        vision = VisionEngine(Mock(profile_id="test"))
        vision.read_text = Mock(return_value=[
            {"text": "Comment as John Doe", "bounds": (600, 950, 750, 970), "center": (675, 960), "confidence": 0.96},
        ])
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        target = vision.find_comment_input(screen)
        self.assertIsNotNone(target)
        self.assertEqual(target, (660, 960))

    def test_standalone_comment_task_flow(self):
        from tasks.facebook_comment import FacebookCommentTask
        task = FacebookCommentTask.__new__(FacebookCommentTask)
        task.profile_id = "profile_test"
        task.comment_text = "https://example.com"
        task.post_url = None
        task.client = Mock()
        task.client.is_running.return_value = True
        task.verify_logged_in = Mock(return_value=True)
        task.post_first_comment = Mock(return_value="submitted_unverified")
        task.set_outcome = Mock(return_value=True)
        task.log = Mock()

        self.assertTrue(task.run())
        task.post_first_comment.assert_called_once_with("https://example.com", post_url=None)


if __name__ == "__main__":
    unittest.main()
