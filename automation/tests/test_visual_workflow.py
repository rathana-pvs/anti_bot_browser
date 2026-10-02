"""Offline tests for visual state recognition and verification primitives."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import FacebookStateRecognizer, ScreenState, StateObservation
from engine.human_input import HumanInput
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

    def test_ocr_device_prefers_cuda_only_when_available(self):
        self.assertEqual(VisionEngine.select_ocr_device("auto", True), "cuda")
        self.assertEqual(VisionEngine.select_ocr_device("cuda", True), "cuda")
        self.assertEqual(VisionEngine.select_ocr_device("auto", False), "cpu")
        self.assertEqual(VisionEngine.select_ocr_device("cuda", False), "cpu")
        self.assertEqual(VisionEngine.select_ocr_device("cpu", True), "cpu")


class SafeClickVariationTests(unittest.TestCase):
    def test_safe_click_point_is_small_and_inside_inset_bounds(self):
        with unittest.mock.patch(
            "engine.human_input.random.randint",
            side_effect=lambda low, high: high,
        ):
            point = HumanInput.safe_click_point((100, 200, 300, 260), max_offset_px=6)

        self.assertEqual(point, (206, 236))
        self.assertGreaterEqual(point[0], 144)
        self.assertLessEqual(point[0], 256)
        self.assertGreaterEqual(point[1], 213)
        self.assertLessEqual(point[1], 247)

    def test_reversible_click_falls_back_to_exact_target_without_bounds(self):
        task = BaseTask.__new__(BaseTask)
        task.human = Mock()
        task.log = Mock()

        actual = task.click_reversible((500, 400), label="unbounded_control")

        self.assertEqual(actual, (500, 400))
        task.human.click.assert_called_once_with(500, 400)

    def test_reversible_click_records_and_uses_confirmed_bounds(self):
        task = BaseTask.__new__(BaseTask)
        task.human = Mock()
        task.log = Mock()
        task.remember_reversible_click_bounds((200, 230), (100, 200, 300, 260))

        with unittest.mock.patch(
            "engine.human_input.random.randint",
            side_effect=lambda low, high: low,
        ):
            actual = task.click_reversible((200, 230), label="bounded_control")

        self.assertEqual(actual, (194, 224))
        task.human.click.assert_called_once_with(194, 224)


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

    def test_generic_photos_and_reels_do_not_prove_facebook_feed(self):
        vision = self.make_vision(["Photos", "Reels"])
        vision.find_photo_video_button.return_value = None
        recognizer = FacebookStateRecognizer(vision)
        self.assertEqual(recognizer.observe().state, ScreenState.UNKNOWN)

    def test_error_has_priority_over_composer(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Create post", "Something went wrong", "Post"]))
        self.assertEqual(recognizer.observe().state, ScreenState.ERROR_DIALOG)

    def test_restriction_has_priority_over_feed(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["What's on your mind", "Account restricted"]))
        self.assertEqual(recognizer.observe().state, ScreenState.ERROR_DIALOG)

    def test_targeted_session_gate_detects_feed_without_full_screen(self):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = [
            {"text": "What's on your mind?", "confidence": 0.95, "center": (150, 90)},
        ]
        observation = FacebookStateRecognizer(vision).observe_session_gate()
        self.assertEqual(observation.state, ScreenState.FEED_READY)
        self.assertEqual(vision.read_text.call_args.kwargs["region"], "session_gate")

    def test_targeted_session_gate_blocks_checkpoint(self):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = [
            {"text": "Security check: confirm your identity", "confidence": 0.95, "center": (150, 90)},
        ]
        observation = FacebookStateRecognizer(vision).observe_session_gate()
        self.assertEqual(observation.state, ScreenState.ERROR_DIALOG)

    def test_targeted_composer_safety_gate_uses_dialog_region(self):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = [
            {"text": "Something went wrong", "confidence": 0.95, "center": (150, 90)},
        ]

        observation = FacebookStateRecognizer(vision).observe_safety_gate()

        self.assertEqual(observation.state, ScreenState.ERROR_DIALOG)
        self.assertEqual(vision.read_text.call_args.kwargs["region"], "browser_dialog")

    def test_reel_publication_gate_uses_bottom_left_region(self):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = [
            {"text": "Your reel was published", "confidence": 0.95, "center": (50, 90)},
        ]

        observation = FacebookStateRecognizer(vision).observe_publication_gate()

        self.assertEqual(observation.state, ScreenState.POST_CONFIRMED)
        self.assertEqual(
            vision.read_text.call_args.kwargs["region"],
            (0.0, 0.50, 0.40, 1.0),
        )

    def test_targeted_session_gate_surfaces_leave_site_dialog(self):
        vision = Mock()
        vision.capture_screen.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        vision.read_text.return_value = [
            {"text": "Leave site? Changes you made may not be saved", "confidence": 0.95, "center": (150, 90)},
            {"text": "Leave", "confidence": 0.95, "center": (170, 120)},
        ]
        observation = FacebookStateRecognizer(vision).observe_session_gate()
        self.assertEqual(observation.state, ScreenState.UNKNOWN)
        self.assertIn("leave_site_dialog", observation.signals)

    def test_success_confirmation(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Your post is now published"]))
        self.assertEqual(recognizer.observe().state, ScreenState.POST_CONFIRMED)

    def test_reel_processing_confirmation(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Your reel is being processed"]))
        self.assertEqual(recognizer.observe().state, ScreenState.POST_CONFIRMED)

    def test_caption_word_publishing_does_not_override_ready_post_review(self):
        recognizer = FacebookStateRecognizer(self.make_vision([
            "Post settings",
            "Post preview",
            "Image publishing reliability validation",
            "Post",
        ]))
        self.assertEqual(recognizer.observe().state, ScreenState.POST_ENABLED)

    def test_standalone_publishing_indicator_is_recognized(self):
        recognizer = FacebookStateRecognizer(self.make_vision(["Publishing..."]))
        self.assertEqual(recognizer.observe().state, ScreenState.PUBLISHING)


class LoginGateTests(unittest.TestCase):
    def make_task(self, gate_observations, full_observations=None):
        task = BaseTask.__new__(BaseTask)
        task.client = Mock()
        task.vision = Mock()
        task.vision.detect_theme.return_value = ("dark", 0.91)
        task.client.navigate_to = Mock()
        task.client.exec_cmd.return_value = Mock(returncode=0, stdout="Facebook")
        task.client.get_current_url.return_value = "https://www.facebook.com/"
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.recognizer = Mock()
        task.recognizer.observe_session_gate.side_effect = gate_observations
        task.recognizer.observe.side_effect = full_observations or []
        task.capture_evidence = Mock()
        task.log = Mock()
        return task

    def test_loading_screen_is_retried_before_accepting_feed(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.UNKNOWN, 0.0),
                StateObservation(ScreenState.FEED_READY, 0.9, ["photo/video"]),
            ],
            [StateObservation(ScreenState.UNKNOWN, 0.0)],
        )
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.verify_logged_in())
        self.assertEqual(task.recognizer.observe_session_gate.call_count, 2)
        self.assertEqual(task.recognizer.observe.call_count, 1)
        self.assertEqual(task.vision.detect_theme.call_count, 2)

    def test_login_gate_can_verify_the_profile_page_directly(self):
        task = self.make_task([
            StateObservation(ScreenState.FEED_READY, 0.9, ["photo/video"]),
        ])
        task.client.get_current_url.return_value = "https://www.facebook.com/me"

        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(
                task.verify_logged_in(target_url="https://www.facebook.com/me")
            )

        task.client.navigate_to.assert_called_once_with("https://www.facebook.com/me")

    def test_actual_login_screen_is_rejected(self):
        task = self.make_task([StateObservation(ScreenState.LOGIN_REQUIRED, 0.98, ["log in"])])
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertFalse(task.verify_logged_in())
        self.assertEqual(task.session_check_status, "auth_required")

    def test_restriction_screen_is_rejected(self):
        task = self.make_task([StateObservation(ScreenState.ERROR_DIALOG, 0.99, ["account restricted"])])
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertFalse(task.verify_logged_in())
        self.assertEqual(task.session_check_status, "safety_blocked")

    def test_weak_visual_is_rejected_then_empty_title_with_strong_visual_is_accepted(self):
        task = self.make_task([
            StateObservation(ScreenState.FEED_READY, 0.8, ["false positive"]),
            StateObservation(ScreenState.FEED_READY, 0.9, ["what's on your mind"]),
        ])
        task.client.exec_cmd.return_value = Mock(returncode=0, stdout="")
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.verify_logged_in())
        self.assertEqual(task.recognizer.observe_session_gate.call_count, 2)

    def test_facebook_visual_on_non_facebook_host_is_rejected(self):
        task = self.make_task([
            StateObservation(ScreenState.FEED_READY, 0.9, ["what's on your mind"]),
            StateObservation(ScreenState.FEED_READY, 0.9, ["what's on your mind"]),
        ])
        task.client.get_current_url.side_effect = [
            "https://www.facebook.com/",
            "https://facebook.com.evil.example/",
            "https://www.facebook.com/",
        ]
        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            self.assertTrue(task.verify_logged_in())
        self.assertEqual(task.recognizer.observe_session_gate.call_count, 2)


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
    def test_detects_visible_file_chooser_and_returns_matching_title(self):
        task = BaseTask.__new__(BaseTask)
        task.client = Mock()

        def exec_cmd(args, check=False):
            title = args[-1]
            if title == "File Upload":
                return Mock(returncode=0, stdout="17\n42\n")
            return Mock(returncode=1, stdout="")

        task.client.exec_cmd.side_effect = exec_cmd

        self.assertEqual(task.find_visible_file_chooser(), ("42", "File Upload"))

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
    def make_task(self, detected_text, include_modal_title=True):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.client = Mock()
        task.vision = Mock()
        task.record_locator_telemetry = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.get_pixel_region.return_value = (384, 162, 1152, 756)
        detected = [
            {"text": "AI label off", "center": (919, 400)},
            {"text": detected_text, "center": (787, 517), "confidence": 0.92},
        ]
        if include_modal_title:
            detected.insert(0, {"text": "Create post", "center": (952, 399), "confidence": 0.99})
        task.vision.read_text.return_value = detected
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        return task

    def test_caption_target_tolerates_apostrophe_ocr_noise(self):
        task = self.make_task("What'$ on your mind,")

        self.assertEqual(task._stable_caption_target(), (787, 517))
        region = task.vision.read_text.call_args.kwargs["region"]
        self.assertTrue(region[0] <= 787 <= region[0] + region[2])
        self.assertTrue(region[1] <= 517 <= region[1] + region[3])

    def test_caption_target_accepts_whats_as_one_ocr_token(self):
        task = self.make_task("Whats on your mind, name?")
        self.assertEqual(task._stable_caption_target(), (787, 517))

    def test_caption_target_accepts_dark_mode_easyocr_substitution(self):
        task = self.make_task("Whal 5 on your mind?")

        self.assertEqual(task._stable_caption_target(), (787, 517))

    def test_caption_target_works_while_next_button_is_disabled(self):
        task = self.make_task("What's on your mind?")

        self.assertEqual(task._stable_caption_target(), (787, 517))
        task.vision.find_blue_action_button.assert_not_called()

    def test_caption_target_rejects_feed_prompt_without_modal_title(self):
        task = self.make_task("What's on your mind?", include_modal_title=False)

        self.assertIsNone(task._stable_caption_target())

    def test_caption_target_tolerates_one_intermittent_ocr_miss(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.client = Mock()
        task.vision = VisionEngine.__new__(VisionEngine)
        task.record_locator_telemetry = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.get_pixel_region = Mock(return_value=(384, 162, 1152, 756))
        detected = [
            {"text": "Create post", "center": (952, 399), "confidence": 0.99},
            {"text": "What's on your mind?", "center": (787, 517), "confidence": 0.92},
        ]
        task.vision.read_text = Mock(side_effect=[detected, [], detected])

        with unittest.mock.patch("engine.vision.time.sleep", return_value=None):
            self.assertEqual(task._stable_caption_target(), (787, 517))


class FeedComposerTargetTests(unittest.TestCase):
    def make_task(self, detected_text):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.client = Mock()
        task.vision = Mock()
        task.record_locator_telemetry = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.get_pixel_region.return_value = (230, 216, 1460, 864)
        task.vision.read_text.return_value = [
            {"text": detected_text, "center": (1202, 476), "confidence": 0.6983},
        ]
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        return task

    def test_feed_composer_accepts_observed_dark_mode_ocr_text(self):
        task = self.make_task("Whal 5 on your mind?")

        self.assertEqual(task._stable_feed_composer_target(), (1202, 476))

    def test_feed_composer_rejects_partial_phrase(self):
        task = self.make_task("Share something on your page")

        self.assertIsNone(task._stable_feed_composer_target())


class ProfileMediaTargetTests(unittest.TestCase):
    def make_task(self, detected_text):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.client = Mock()
        task.vision = Mock()
        task.record_locator_telemetry = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.get_pixel_region.return_value = (230, 216, 1460, 864)
        task.vision.read_text.return_value = detected_text
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        task.vision.find_photo_video_button.return_value = None
        return task

    def test_accepts_photo_video_below_composer_prompt(self):
        task = self.make_task([
            {"text": "What's on your mind?", "center": (1400, 940), "confidence": 0.91},
            {"text": "Photo/video", "center": (1180, 1002), "confidence": 0.96},
        ])

        self.assertEqual(task._stable_profile_media_target(), (1180, 1002))
        task.vision.find_photo_video_button.assert_not_called()

    def test_rejects_cover_photo_target_above_composer(self):
        task = self.make_task([
            {"text": "Photo/video", "center": (1338, 311), "confidence": 0.99},
            {"text": "What's on your mind?", "center": (1400, 940), "confidence": 0.91},
        ])

        self.assertIsNone(task._stable_profile_media_target())
        visual_region = task.vision.find_photo_video_button.call_args.kwargs["region"]
        self.assertGreaterEqual(visual_region[1], 960)
        self.assertLessEqual(visual_region[1] + visual_region[3], 1080)

    def test_requires_composer_prompt_before_using_visual_fallback(self):
        task = self.make_task([
            {"text": "Photo/video", "center": (1338, 311), "confidence": 0.99},
        ])

        self.assertIsNone(task._stable_profile_media_target())
        task.vision.find_photo_video_button.assert_not_called()


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


class ReelUploadTargetTests(unittest.TestCase):
    def make_task(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.client = Mock()
        task.vision = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        return task

    def test_prefers_blue_upload_button_over_preview_instruction(self):
        task = self.make_task()
        task.vision.find_blue_action_buttons.return_value = [{
            "center": (177, 790),
            "bounds": (18, 771, 336, 809),
            "area": 12084,
        }]
        task.vision.read_text.return_value = [
            {"text": "Upload", "confidence": 0.99, "center": (177, 790)},
        ]
        self.assertEqual(task._find_reel_upload_target(), (177, 790))

    def test_rejects_noninteractive_upload_preview_sentence(self):
        task = self.make_task()
        task.vision.find_blue_action_buttons.return_value = []
        task.vision.read_text.return_value = [
            {"text": "Upload your video in order to see a preview here", "confidence": 0.99, "center": (1129, 650)},
        ]
        self.assertIsNone(task._find_reel_upload_target())


class ReelEntryFlowTests(unittest.TestCase):
    def make_task(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.recognizer = Mock()
        task.recognizer.observe.return_value = StateObservation(ScreenState.UNKNOWN, 0.2)
        task.capture_evidence = Mock()
        task.telemetry = Mock()
        task.vision = Mock()
        return task

    def test_direct_file_chooser_takes_priority_over_page_detection(self):
        task = self.make_task()
        task.find_visible_file_chooser = Mock(return_value=("42", "File Upload"))
        task._find_reel_upload_target = Mock(return_value=(177, 790))

        status, target, _ = task._wait_for_reel_entry(timeout=1.0)

        self.assertEqual(status, "direct_file_chooser")
        self.assertEqual(target, ("42", "File Upload"))
        self.assertEqual(task._preferred_reel_sidebar_region, task.LEFT_REEL_SIDEBAR)
        task._find_reel_upload_target.assert_not_called()

    def test_reel_studio_upload_control_selects_its_actual_left_sidebar(self):
        task = self.make_task()
        task.find_visible_file_chooser = Mock(return_value=None)
        task._find_reel_upload_target = Mock(return_value=(177, 790))

        status, target, _ = task._wait_for_reel_entry(timeout=1.0)

        self.assertEqual(status, "reel_studio")
        self.assertEqual(target, (177, 790))
        self.assertEqual(task._preferred_reel_sidebar_region, task.LEFT_REEL_SIDEBAR)

    def test_delayed_direct_file_chooser_is_detected_without_page_click(self):
        task = self.make_task()
        task.find_visible_file_chooser = Mock(side_effect=[None, ("42", "File Upload")])
        task._find_reel_upload_target = Mock(return_value=None)

        with unittest.mock.patch("tasks.facebook_reel.time.sleep", return_value=None):
            status, target, _ = task._wait_for_reel_entry(timeout=1.0)

        self.assertEqual(status, "direct_file_chooser")
        self.assertEqual(target, ("42", "File Upload"))
        task._find_reel_upload_target.assert_called_once_with()

    def test_final_composer_requires_description_and_reel_context(self):
        task = self.make_task()
        screen = task.client.screenshot.return_value
        final_items = [
            {"text": "Describe your reel..."},
            {"text": "Uploaded media"},
            {"text": "Post audience"},
        ]
        task.vision.read_text.side_effect = [[], final_items]

        self.assertTrue(task._is_reel_final_composer(screen))
        self.assertEqual(
            task.vision.read_text.call_args_list[-1].kwargs["region"],
            (0.00, 0.08, 0.34, 1.0),
        )

        task.vision.read_text.side_effect = [
            [{"text": "Describe your reel..."}],
            [{"text": "Describe your reel..."}],
        ]
        self.assertFalse(task._is_reel_final_composer(screen))

    def test_direct_flow_searches_left_sidebar_first(self):
        task = self.make_task()
        task._preferred_reel_sidebar_region = task.LEFT_REEL_SIDEBAR
        task.vision.read_text.return_value = [
            {"text": "Describe your reel...", "center": (290, 310)},
            {"text": "Uploaded media"},
        ]

        self.assertTrue(task._is_reel_final_composer(task.client.screenshot()))
        self.assertEqual(task._reel_description_target, (290, 310))
        task.vision.read_text.assert_called_once()
        self.assertEqual(
            task.vision.read_text.call_args.kwargs["region"],
            task.LEFT_REEL_SIDEBAR,
        )

    def test_detected_left_layout_stays_locked_until_broad_fallback(self):
        task = self.make_task()
        task._preferred_reel_sidebar_region = task.LEFT_REEL_SIDEBAR
        task.vision.read_text.return_value = []

        self.assertFalse(task._is_reel_final_composer(task.client.screenshot()))
        task.vision.read_text.assert_called_once_with(
            task.client.screenshot(),
            region=task.LEFT_REEL_SIDEBAR,
            min_confidence=0.18,
        )

        task.vision.read_text.reset_mock()
        self.assertFalse(
            task._is_reel_final_composer(
                task.client.screenshot(),
                allow_layout_fallback=True,
            )
        )
        self.assertEqual(task.vision.read_text.call_count, 2)
        self.assertEqual(
            [call.kwargs["region"] for call in task.vision.read_text.call_args_list],
            [task.LEFT_REEL_SIDEBAR, task.RIGHT_REEL_SIDEBAR],
        )

    def test_reel_safety_uses_full_screen_only_on_third_local_miss(self):
        task = self.make_task()
        targeted = StateObservation(ScreenState.UNKNOWN, 0.0)
        full = StateObservation(ScreenState.UNKNOWN, 0.0)
        task.recognizer.observe_safety_gate.return_value = targeted
        task.recognizer.observe.return_value = full
        screen = task.client.screenshot()

        self.assertIs(task._reel_safety_observation(screen, 1), targeted)
        self.assertIs(task._reel_safety_observation(screen, 2), targeted)
        task.recognizer.observe.assert_not_called()
        self.assertIs(task._reel_safety_observation(screen, 3), full)
        task.recognizer.observe.assert_called_once_with(screen)

    def test_final_composer_remains_detectable_after_early_caption_entry(self):
        task = self.make_task()
        task._preferred_reel_sidebar_region = task.LEFT_REEL_SIDEBAR
        task._reel_description_target = (290, 310)
        task._reel_caption_entered = True
        task.vision.read_text.return_value = [
            {"text": "Caption already entered"},
            {"text": "Uploaded media"},
            {"text": "Post audience"},
        ]

        self.assertTrue(task._is_reel_final_composer(task.client.screenshot()))

    def test_profile_006_next_share_flow_requires_editing_surface_markers(self):
        task = self.make_task()
        task.vision.read_text.return_value = [
            {"text": "Create reel"},
            {"text": "Uploaded media"},
            {"text": "Edit"},
            {"text": "Audio"},
            {"text": "Closed captions"},
            {"text": "Audio descriptions"},
        ]

        self.assertTrue(task._is_reel_next_share_composer(task.client.screenshot()))

        task.vision.read_text.return_value = [
            {"text": "Create reel"},
            {"text": "Uploaded media"},
            {"text": "Post audience"},
        ]
        self.assertFalse(task._is_reel_next_share_composer(task.client.screenshot()))

    def test_early_caption_entry_pastes_and_marks_caption_entered(self):
        task = self.make_task()
        task.caption = "Caption while the video processes"
        task._wait_for_target = Mock(return_value=("ready", (290, 310), task.client.screenshot()))
        task._find_reel_description_target = Mock()
        task._wait_for_reel_caption = Mock(
            return_value=(True, task.client.screenshot(), 1.0)
        )
        task.human = Mock()
        task.paste_text = Mock()
        task.log = Mock()

        status, _ = task._enter_reel_caption(timeout=15.0)

        self.assertEqual(status, "entered")
        self.assertTrue(task._reel_caption_entered)
        self.assertEqual(task._reel_description_target, (290, 310))
        task.human.click.assert_called_once_with(290, 310)
        task.paste_text.assert_called_once_with(task.caption)

    def test_unavailable_early_caption_does_not_type(self):
        task = self.make_task()
        task.caption = "Caption while the video processes"
        task._wait_for_target = Mock(return_value=("timeout", None, None))
        task._find_reel_description_target = Mock()
        task.human = Mock()
        task.paste_text = Mock()

        status, _ = task._enter_reel_caption(timeout=15.0)

        self.assertEqual(status, "unavailable")
        task.human.click.assert_not_called()
        task.paste_text.assert_not_called()

    def test_direct_media_state_rejects_empty_preview(self):
        task = self.make_task()
        task.vision.read_text.return_value = [
            {"text": "Add video"},
            {"text": "or drag and drop"},
        ]

        self.assertEqual(
            task._direct_reel_media_state(task.client.screenshot()),
            "missing",
        )

    def test_direct_media_state_accepts_uploaded_media(self):
        task = self.make_task()
        task.vision.read_text.return_value = [
            {"text": "Describe your reel...", "center": (290, 310)},
            {"text": "Uploaded media"},
        ]

        self.assertEqual(
            task._direct_reel_media_state(task.client.screenshot()),
            "attached",
        )
        self.assertEqual(task._reel_description_target, (290, 310))

    def test_direct_layout_reattaches_once_when_second_chooser_appears(self):
        task = self.make_task()
        task.log = Mock()
        task.find_visible_file_chooser = Mock(
            side_effect=[("99", "Open File"), None]
        )
        task.attach_file_gtk = Mock(return_value=True)
        task._direct_reel_media_state = Mock(return_value="attached")

        status, _ = task._ensure_direct_reel_media_attached(
            "/data/shared_media/video.mp4",
            timeout=2.0,
        )

        self.assertEqual(status, "ready")
        task.attach_file_gtk.assert_called_once_with(
            "/data/shared_media/video.mp4",
            chooser_window=("99", "Open File"),
        )
        evidence = task.capture_evidence.call_args
        self.assertEqual(evidence.args[0], "reel_direct_media_attached")
        self.assertTrue(evidence.kwargs["second_attachment"])

    def test_description_target_reuses_final_composer_observation(self):
        task = self.make_task()
        task._reel_description_target = (290, 310)

        self.assertEqual(task._find_reel_description_target(), (290, 310))
        task.vision.find_stable.assert_not_called()
        task.vision.find_text_cascaded.assert_not_called()

    def test_description_target_falls_back_to_left_sidebar_layout(self):
        task = self.make_task()
        task.vision.find_text_cascaded.return_value = {
            "center": (290, 310),
            "text": "Describe your reel...",
        }
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertEqual(task._find_reel_description_target(), (290, 310))
        self.assertEqual(
            task.vision.find_text_cascaded.call_args.kwargs["region"],
            (0.00, 0.08, 0.34, 1.0),
        )

    def test_description_target_uses_uploaded_media_heading_as_geometry_anchor(self):
        task = self.make_task()

        def find_text(labels, **_kwargs):
            if tuple(labels) == ("uploaded media",):
                return {"center": (80, 388), "text": "Uploaded media"}
            return None

        task.vision.find_text_cascaded.side_effect = find_text
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertEqual(task._find_reel_description_target(), (170, 310))

    def test_description_target_does_not_use_distant_post_audience_anchor(self):
        task = self.make_task()
        task.vision.find_text_cascaded.return_value = None
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertIsNone(task._find_reel_description_target())
        searched_labels = [
            tuple(call.args[0]) for call in task.vision.find_text_cascaded.call_args_list
        ]
        self.assertNotIn(("public", "post audience", "tag and collaborate", "add ai label"), searched_labels)

    def test_caption_verification_rejects_untouched_placeholder(self):
        task = self.make_task()
        task.caption = "Save this for your routine and follow for more"
        task.vision.read_text.return_value = [
            {"text": "Describe your reel..."},
            {"text": "Uploaded media"},
            {"text": "Post audience"},
        ]

        self.assertEqual(task._reel_caption_match_confidence(task.client.screenshot()), 0.0)

    def test_caption_verification_accepts_visible_caption_tokens(self):
        task = self.make_task()
        task.caption = "Save this for your routine and follow for more"
        task._preferred_reel_sidebar_region = task.LEFT_REEL_SIDEBAR
        task.vision.read_text.return_value = [
            {"text": "Save this for your routine"},
            {"text": "Uploaded media"},
        ]

        self.assertGreaterEqual(
            task._reel_caption_match_confidence(task.client.screenshot()),
            0.5,
        )
        task.vision.read_text.assert_called_once()

    def test_caption_verification_rejects_common_words_outside_empty_editor(self):
        task = self.make_task()
        task.caption = (
            "In an interview with the Radio Times the musician announced a formal end "
            "to the relationship and discussed the ongoing conflict"
        )
        task._reel_description_target = (290, 310)
        task.vision.read_text.return_value = [
            {"text": "Describe your reel..."},
            {"text": "Uploaded media"},
            {"text": "Post audience"},
            {"text": "Remixing and use of original audio"},
        ]

        self.assertLess(
            task._reel_caption_match_confidence(task.client.screenshot()),
            0.6,
        )
        region = task.vision.read_text.call_args.kwargs["region"]
        self.assertLess(region[0], task._reel_description_target[0])
        self.assertLess(region[1], task._reel_description_target[1])
        self.assertGreater(region[0] + region[2], task._reel_description_target[0])
        self.assertGreater(region[1] + region[3], task._reel_description_target[1])

    def test_profile_006_composer_with_enabled_next_advances_instead_of_publishing(self):
        task = self.make_task()
        task._is_reel_next_share_composer = Mock(return_value=True)
        task._is_reel_final_composer = Mock(return_value=True)
        task._find_stable_enabled_action = Mock(return_value=(300, 1046))

        status, target, _ = task._wait_for_reel_post_upload_transition(
            timeout=1.0,
            label="reel_next_ready",
        )

        self.assertEqual(status, "next")
        self.assertEqual(target, (300, 1046))
        task._is_reel_final_composer.assert_not_called()

    def test_existing_direct_final_composer_path_remains_unchanged(self):
        task = self.make_task()
        task._is_reel_next_share_composer = Mock(return_value=False)
        task._is_reel_final_composer = Mock(return_value=True)
        task._find_stable_enabled_action = Mock(return_value=(300, 1046))

        status, target, _ = task._wait_for_reel_post_upload_transition(
            timeout=1.0,
            label="reel_next_ready",
        )

        self.assertEqual(status, "final_composer")
        self.assertIsNone(target)
        task._is_reel_final_composer.assert_called_once()
        task._find_stable_enabled_action.assert_not_called()

    def test_post_upload_transition_preserves_next_flow(self):
        task = self.make_task()
        task._is_reel_next_share_composer = Mock(return_value=False)
        task._is_reel_final_composer = Mock(return_value=False)
        task._find_stable_enabled_action = Mock(return_value=(420, 990))

        status, target, _ = task._wait_for_reel_post_upload_transition(
            timeout=1.0,
            label="reel_next_ready",
        )

        self.assertEqual(status, "next")
        self.assertEqual(target, (420, 990))
        self.assertFalse(
            task._find_stable_enabled_action.call_args.kwargs["allow_semantic_fallback"]
        )

    def test_video_composer_wait_is_capped_at_two_minutes_total(self):
        task = self.make_task()
        task._video_composer_started_at = 1000.0

        with unittest.mock.patch("tasks.facebook_reel.time.monotonic", return_value=1115.0):
            self.assertEqual(task._remaining_video_composer_wait(), 5.0)
            self.assertEqual(task._remaining_video_composer_wait(30.0), 5.0)

        with unittest.mock.patch("tasks.facebook_reel.time.monotonic", return_value=1121.0):
            self.assertEqual(task._remaining_video_composer_wait(), 0.0)

    def test_share_surface_requires_heading_audience_and_review_option(self):
        task = self.make_task()
        task.vision.read_text.return_value = [
            {"text": "Share"},
            {"text": "Post audience"},
            {"text": "Remixing and use of original audio"},
            {"text": "Boost post"},
        ]

        self.assertTrue(task._is_reel_share_surface(task.client.screenshot()))

        task.vision.read_text.return_value = [
            {"text": "Post audience"},
            {"text": "Boost post"},
        ]
        self.assertFalse(task._is_reel_share_surface(task.client.screenshot()))

    def test_publish_action_uses_unique_blue_cta_on_confirmed_share_surface(self):
        task = self.make_task()
        task.remember_reversible_click_bounds = Mock()
        task._is_reel_share_surface = Mock(return_value=True)
        task.vision.find_blue_action_buttons.return_value = [{
            "center": (256, 580),
            "bounds": (162, 568, 351, 593),
        }]
        task.vision.find_text_cascaded.return_value = None
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertEqual(task._find_stable_reel_publish_action(), (256, 580))
        task._is_reel_share_surface.assert_called_once()
        self.assertEqual(
            task.vision.find_blue_action_buttons.call_args.kwargs["region"],
            task.LEFT_PUBLICATION_REGION,
        )

    def test_publish_action_preserves_old_explicit_post_flow(self):
        task = self.make_task()
        task.remember_reversible_click_bounds = Mock()
        task._is_reel_share_surface = Mock(return_value=False)
        task.vision.find_blue_action_buttons.return_value = [{
            "center": (960, 990),
            "bounds": (840, 965, 1080, 1015),
        }]
        task.vision.find_text_cascaded.return_value = {
            "text": "Post",
            "center": (960, 990),
            "confidence": 0.96,
        }
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertEqual(task._find_stable_reel_publish_action(), (960, 990))
        task._is_reel_share_surface.assert_not_called()

    def test_publish_action_rejects_unrecognized_surface_when_post_ocr_misses(self):
        task = self.make_task()
        task.remember_reversible_click_bounds = Mock()
        task._is_reel_share_surface = Mock(return_value=False)
        task.vision.find_blue_action_buttons.return_value = [{
            "center": (256, 580),
            "bounds": (162, 568, 351, 593),
        }]
        task.vision.find_text_cascaded.return_value = None
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertIsNone(task._find_stable_reel_publish_action())
        task.remember_reversible_click_bounds.assert_not_called()

    def test_publish_action_rejects_ambiguous_multiple_blue_ctas(self):
        task = self.make_task()
        task.remember_reversible_click_bounds = Mock()
        task._is_reel_share_surface = Mock(return_value=True)
        task.vision.find_blue_action_buttons.return_value = [
            {"center": (256, 580), "bounds": (162, 568, 351, 593)},
            {"center": (80, 580), "bounds": (20, 568, 140, 593)},
        ]
        task.vision.find_text_cascaded.return_value = None
        task.vision.find_stable.side_effect = lambda locator, **_: locator()

        self.assertIsNone(task._find_stable_reel_publish_action())
        task._is_reel_share_surface.assert_not_called()

    def test_legacy_publish_fallback_disables_semantic_guessing(self):
        task = self.make_task()
        task._find_stable_enabled_action = Mock(return_value=(960, 990))

        self.assertEqual(
            task._find_explicit_legacy_reel_publish_action(),
            (960, 990),
        )
        self.assertFalse(
            task._find_stable_enabled_action.call_args.kwargs["allow_semantic_fallback"]
        )

    def test_new_share_flow_suppresses_refresh_while_post_is_still_visible(self):
        task = self.make_task()
        screen = task.client.screenshot()
        task._is_reel_share_surface = Mock(return_value=True)

        self.assertTrue(task._new_share_post_is_still_pending(screen, True))
        self.assertFalse(task._new_share_post_is_still_pending(screen, False))


class ReelProfileActionTests(unittest.TestCase):
    def make_task(self, ocr_items):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.client = Mock()
        task.vision = Mock()
        task.telemetry = None
        task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.get_pixel_region.return_value = (230, 216, 1460, 864)
        task.vision.read_text.return_value = ocr_items
        task.vision.find_stable.side_effect = lambda locator, **_: locator()
        return task

    def test_selects_composer_reel_instead_of_reels_navigation_tab(self):
        task = self.make_task([
            {"text": "Reels", "confidence": 0.99, "center": (703, 263)},
            {"text": "What's on your mind?", "confidence": 0.91, "center": (1190, 341)},
            {"text": "Photo/video", "confidence": 0.96, "center": (1180, 401)},
            {"text": "Reel", "confidence": 0.90, "center": (1401, 401)},
        ])

        self.assertEqual(task._find_profile_reel_action(), (1401, 401))

    def test_rejects_reels_navigation_tab_without_composer_action(self):
        task = self.make_task([
            {"text": "Reels", "confidence": 0.99, "center": (703, 263)},
            {"text": "What's on your mind?", "confidence": 0.91, "center": (1190, 341)},
            {"text": "Photo/video", "confidence": 0.96, "center": (1180, 401)},
        ])

        self.assertIsNone(task._find_profile_reel_action())

    def test_uses_photo_video_as_secondary_composer_anchor(self):
        task = self.make_task([
            {"text": "Reels", "confidence": 0.99, "center": (703, 263)},
            {"text": "Photo/video", "confidence": 0.96, "center": (1180, 401)},
            {"text": "Reel", "confidence": 0.90, "center": (1401, 401)},
        ])

        self.assertEqual(task._find_profile_reel_action(), (1401, 401))


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


class ReelPublicationVerificationTests(unittest.TestCase):
    def make_task(self, observations, similarities):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.recognizer = Mock()
        task.recognizer.observe_publication_gate.side_effect = observations
        task.recognizer.observe.return_value = StateObservation(ScreenState.UNKNOWN, 0.0)
        task.vision = Mock()
        task.vision.similarity.side_effect = similarities
        task._handle_remix_audio_dialog = Mock(return_value="absent")
        task.check_and_dismiss_post_prompt = Mock(return_value=False)
        task.log = Mock()
        return task

    def test_feed_without_reel_confirmation_does_not_confirm_publication(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.FEED_READY, 0.8),
                StateObservation(ScreenState.FEED_READY, 0.8),
            ],
            [0.70, 0.70],
        )
        with unittest.mock.patch(
            "tasks.facebook_reel.time.time",
            side_effect=[0.0, 0.0, 0.2, 2.0],
        ), unittest.mock.patch("tasks.facebook_reel.time.sleep", return_value=None):
            status, _ = task._verify_reel_publication(
                np.zeros((100, 100, 3), dtype=np.uint8),
                timeout=1.0,
            )

        self.assertEqual(status, "uncertain")

    def test_reel_confirmation_completes_popup_verification(self):
        task = self.make_task(
            [StateObservation(ScreenState.POST_CONFIRMED, 0.98)],
            [0.70],
        )
        with unittest.mock.patch(
            "tasks.facebook_reel.time.time",
            side_effect=[0.0, 0.0],
        ), unittest.mock.patch("tasks.facebook_reel.time.sleep", return_value=None):
            status, final = task._verify_reel_publication(
                np.zeros((100, 100, 3), dtype=np.uint8),
                timeout=1.0,
            )

        self.assertEqual(status, "published")
        self.assertEqual(final[0].state, ScreenState.POST_CONFIRMED)
        task.recognizer.observe_publication_gate.assert_called_once_with(
            task.client.screenshot(),
            region=task.LEFT_PUBLICATION_REGION,
        )
        task.recognizer.observe.assert_not_called()

    def test_reel_confirmation_never_uses_full_screen_before_profile_fallback(self):
        task = self.make_task(
            [
                StateObservation(ScreenState.UNKNOWN, 0.0),
                StateObservation(ScreenState.UNKNOWN, 0.0),
                StateObservation(ScreenState.UNKNOWN, 0.0),
            ],
            [0.70, 0.70, 0.70],
        )
        with unittest.mock.patch(
            "tasks.facebook_reel.time.time",
            side_effect=[0.0, 0.0, 0.1, 0.2, 2.0],
        ), unittest.mock.patch("tasks.facebook_reel.time.sleep", return_value=None):
            status, _ = task._verify_reel_publication(
                np.zeros((100, 100, 3), dtype=np.uint8),
                timeout=1.0,
            )

        self.assertEqual(status, "uncertain")
        self.assertEqual(task.recognizer.observe_publication_gate.call_count, 3)
        task.recognizer.observe.assert_not_called()


class FirstCommentTargetTests(unittest.TestCase):
    def test_shared_comment_scan_finds_input_and_action_with_one_ocr_call(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "Comment", "center": (1130, 390), "confidence": 0.80},
            {"text": "Comment as Page", "center": (1167, 840), "confidence": 0.95},
        ]
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)

        input_target, action_target = task._find_first_comment_targets(screen)

        self.assertEqual(input_target, (1167, 840))
        self.assertEqual(action_target["center"], (1130, 390))
        task.vision.read_text.assert_called_once()
        self.assertEqual(
            task.vision.read_text.call_args.kwargs["region"],
            task._first_comment_search_regions(screen)[0],
        )

    def test_comment_scan_uses_existing_broad_region_after_narrow_miss(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.read_text.side_effect = [
            [],
            [{"text": "Comment as Creator", "center": (1167, 840), "confidence": 0.95}],
        ]

        input_target, action_target = task._find_first_comment_targets(
            screen,
            allow_broad_fallback=True,
        )

        self.assertEqual(input_target, (1167, 840))
        self.assertIsNone(action_target)
        self.assertEqual(task._last_comment_search_tier, "broad")
        narrow, broad = task._first_comment_search_regions(screen)
        self.assertEqual(
            [call.kwargs["region"] for call in task.vision.read_text.call_args_list],
            [narrow, broad],
        )

    def test_comment_scan_stops_after_broad_post_area(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.read_text.side_effect = [
            [],
            [],
            [{"text": "Write a public comment...", "center": (410, 910), "confidence": 0.91}],
        ]

        input_target, action_target = task._find_first_comment_targets(
            screen,
            allow_broad_fallback=True,
        )

        self.assertIsNone(input_target)
        self.assertIsNone(action_target)
        self.assertEqual(task._last_comment_search_tier, "not_found")
        narrow, broad = task._first_comment_search_regions(screen)
        self.assertEqual(
            [call.kwargs["region"] for call in task.vision.read_text.call_args_list],
            [narrow, broad],
        )

    def test_broad_post_area_does_not_consult_full_screen_results(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.vision.read_text.side_effect = [
            [],
            [],
            [{"text": "Comment", "center": (200, 150), "confidence": 0.99}],
        ]

        self.assertEqual(
            task._find_first_comment_targets(screen, allow_broad_fallback=True),
            (None, None),
        )
        self.assertEqual(task._last_comment_search_tier, "not_found")

    def test_comment_scan_never_clicks_comment_action_and_finds_input_after_scroll(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.client = Mock()
        task.client.screenshot.return_value = screen
        task.human = Mock()
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.click_reversible = Mock()
        task._find_first_comment_targets = Mock(side_effect=[
            (None, {
                "center": (1116, 879),
                "bounds": (1102, 865, 1130, 893),
                "source": "ocr_comment_label",
            }),
            ((1160, 910), None),
        ])

        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            target, target_screen = task._open_profile_first_comment_input(navigate=False)

        self.assertEqual(target, (1160, 910))
        self.assertIs(target_screen, screen)
        task.click_reversible.assert_not_called()
        task.human.scroll.assert_called_once_with("down", notches=1)

    def test_comment_scan_reaches_bottom_of_tall_reel_card(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.client = Mock()
        task.client.screenshot.return_value = screen
        task.human = Mock()
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task._find_first_comment_targets = Mock(
            side_effect=[(None, None)] * 8 + [((1160, 910), None)],
        )

        with unittest.mock.patch("tasks.base_task.time.sleep", return_value=None):
            target, _ = task._open_profile_first_comment_input(navigate=False)

        self.assertEqual(target, (1160, 910))
        self.assertEqual(task.human.scroll.call_count, 7)
        self.assertTrue(all(
            call.kwargs["allow_broad_fallback"] is True
            for call in task._find_first_comment_targets.call_args_list
        ))

    def test_comment_scan_defers_broad_region_during_first_local_attempt(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        task.vision.read_text.return_value = []
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)

        self.assertEqual(
            task._find_first_comment_targets(screen, allow_broad_fallback=False),
            (None, None),
        )
        task.vision.read_text.assert_called_once()

    def test_comment_input_can_be_constrained_to_verified_region(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        task.vision.find_comment_input.return_value = (1120, 840)
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        region = (1017, 669, 729, 313)

        self.assertEqual(
            task._find_first_comment_input(screen, region=region),
            (1120, 840),
        )
        task.vision.find_comment_input.assert_called_once_with(
            screen=screen,
            regions=[region],
        )

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

    def test_reel_audio_policy_dialog_preserves_selection_and_saves_once(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        task.human = Mock()
        task.log = Mock()
        task.capture_evidence = Mock()
        screen = np.zeros((288, 589, 3), dtype=np.uint8)
        task.vision.read_text.side_effect = [
            [
                {
                    "text": "Remixing and original audio use",
                    "confidence": 0.99,
                    "center": (294, 27),
                },
                {
                    "text": "Allow others to use this reel's original audio and remix?",
                    "confidence": 0.95,
                    "center": (220, 65),
                },
            ],
            [{"text": "Save", "confidence": 0.99, "center": (294, 254)}],
        ]
        task.vision.find_blue_action_buttons.return_value = [{
            "center": (294, 254),
            "bounds": (20, 240, 569, 268),
        }]

        result = task._handle_remix_audio_dialog(screen)

        self.assertEqual(result, "saved")
        task.human.click.assert_called_once_with(294, 254)
        task.capture_evidence.assert_called_once()

    def test_reel_audio_policy_handler_ignores_unrelated_save_button(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.vision = Mock()
        task.human = Mock()
        task.log = Mock()
        task.capture_evidence = Mock()
        task.vision.read_text.return_value = [
            {"text": "Save", "confidence": 0.99, "center": (294, 254)},
        ]

        result = task._handle_remix_audio_dialog(np.zeros((288, 589, 3), dtype=np.uint8))

        self.assertEqual(result, "absent")
        task.vision.find_blue_action_buttons.assert_not_called()
        task.human.click.assert_not_called()

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
        task._open_permalink_comment_input = Mock()

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
        task._open_permalink_comment_input.assert_not_called()
        self.assertEqual(task.last_comment_method, "profile_first_post")
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

    def test_comment_text_containing_posting_is_not_a_pending_indicator(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        screen = np.zeros((100, 100, 3), dtype=np.uint8)
        task.client.screenshot.return_value = screen
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "A posting guide", "confidence": 0.99, "center": (60, 60)},
        ]
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(return_value=((90, 75), screen))

        with unittest.mock.patch("time.sleep", return_value=None), \
             unittest.mock.patch("tasks.base_task.random.uniform", return_value=1.0):
            result = task.post_first_comment("A posting guide")

        self.assertEqual(result, "submitted_verified")

    def test_post_first_comment_visibly_verifies_submitted_text(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "Canary comment test September 25 2026", "confidence": 0.95, "center": (60, 60)},
            {"text": "Comment as Fat Frog", "confidence": 0.95, "center": (60, 95)},
        ]
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(
            return_value=((90, 75), np.zeros((100, 100, 3)))
        )

        with unittest.mock.patch("time.sleep", return_value=None), \
             unittest.mock.patch("tasks.base_task.random.uniform", return_value=1.0):
            result = task.post_first_comment("Canary comment test — September 25, 2026")

        self.assertEqual(result, "submitted_verified")
        task.capture_evidence.assert_any_call(
            "after_first_comment",
            task.client.screenshot.return_value,
            comment_status="submitted_verified",
            comment_match_confidence=1.0,
            comment_method="profile_first_post",
        )

    def test_permalink_is_used_only_when_primary_target_is_missing(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        screen = np.zeros((100, 100, 3), dtype=np.uint8)
        task.client.screenshot.return_value = screen
        task.vision = Mock()
        task.vision.read_text.return_value = []
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(return_value=(None, None))
        task._open_permalink_comment_input = Mock(return_value=((70, 80), screen))

        with unittest.mock.patch("time.sleep", return_value=None), \
             unittest.mock.patch("tasks.base_task.random.uniform", return_value=1.0):
            result = task.post_first_comment(
                "https://example.com/link",
                post_url="https://www.facebook.com/permalink.php?story_fbid=123&id=456",
            )

        self.assertEqual(result, "submitted_unverified")
        self.assertEqual(task.last_comment_method, "verified_permalink_fallback")
        task._open_profile_first_comment_input.assert_called_once_with()
        task._open_permalink_comment_input.assert_called_once()
        task.human.key_press.assert_called_once_with("Return")

    def test_comment_page_reuse_avoids_permalink_correlation_when_profile_input_exists(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.post_first_comment = Mock(return_value="submitted_verified")
        task.correlate_and_extract_permalink = Mock()

        status, permalink = task.post_first_comment_with_page_reuse(
            "https://example.com/link",
            caption="caption",
            media_type="reel",
        )

        self.assertEqual(status, "submitted_verified")
        self.assertEqual(permalink["permalink_status"], "not_requested")
        task.post_first_comment.assert_called_once_with(
            "https://example.com/link",
            reuse_profile_page=True,
        )
        task.correlate_and_extract_permalink.assert_not_called()

    def test_comment_page_reuse_correlates_once_only_after_profile_input_is_missing(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.post_first_comment = Mock(side_effect=[
            "failed_input_not_found",
            "submitted_verified",
        ])
        captured = {
            "post_url": "https://www.facebook.com/reel/123",
            "post_url_verified_at": "2026-09-28T00:00:00+00:00",
            "post_match_confidence": 0.9,
        }
        task.correlate_and_extract_permalink = Mock(return_value=captured)
        task.recover_missing_permalink = Mock(return_value={
            **captured,
            "permalink_status": "captured",
            "permalink_missing": False,
            "permalink_recovery_attempted": False,
        })

        status, permalink = task.post_first_comment_with_page_reuse(
            "https://example.com/link",
            caption="caption",
            media_type="reel",
        )

        self.assertEqual(status, "submitted_verified")
        self.assertEqual(permalink["permalink_status"], "captured")
        task.correlate_and_extract_permalink.assert_called_once_with(
            caption="caption",
            media_type="reel",
        )
        self.assertEqual(task.post_first_comment.call_args_list, [
            unittest.mock.call(
                "https://example.com/link",
                reuse_profile_page=True,
            ),
            unittest.mock.call(
                "https://example.com/link",
                post_url="https://www.facebook.com/reel/123",
                prefer_permalink=True,
            ),
        ])

    def test_reel_comment_refreshes_and_checks_latest_post_before_commenting(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.navigate_to = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.human = Mock()
        task.capture_evidence = Mock()
        task._scan_profile_for_latest_reel = Mock(return_value=(True, task.client.screenshot.return_value))
        task.post_first_comment = Mock(return_value="submitted_verified")

        status, permalink = task.post_reel_first_comment_after_refresh(
            "https://example.com/link",
        )

        self.assertEqual(status, "submitted_verified")
        self.assertIsNone(permalink["post_url"])
        self.assertEqual(permalink["permalink_status"], "not_requested")
        task.navigate_to.assert_called_once_with(
            "https://www.facebook.com/me",
            wait_seconds=3.0,
        )
        task.post_first_comment.assert_called_once_with(
            "https://example.com/link",
            reuse_profile_page=True,
            warm_down_after_submit=False,
        )

    def test_reel_comment_stops_after_refreshed_feed_has_no_input(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.navigate_to = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.human = Mock()
        task.capture_evidence = Mock()
        task._scan_profile_for_latest_reel = Mock(return_value=(True, task.client.screenshot.return_value))
        task.post_first_comment = Mock(return_value="failed_input_not_found")

        status, permalink = task.post_reel_first_comment_after_refresh(
            "https://example.com/link",
        )

        self.assertEqual(status, "failed_input_not_found")
        self.assertEqual(permalink["permalink_status"], "not_requested")
        task.post_first_comment.assert_called_once_with(
            "https://example.com/link",
            reuse_profile_page=True,
            warm_down_after_submit=False,
        )

    def test_reel_comment_waits_ten_seconds_and_refreshes_once_more(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.navigate_to = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.human = Mock()
        task.capture_evidence = Mock()
        task._scan_profile_for_latest_reel = Mock(
            side_effect=[
                (False, task.client.screenshot.return_value),
                (False, task.client.screenshot.return_value),
            ]
        )
        task.post_first_comment = Mock()
        task.permalink_not_requested = Mock(return_value={"permalink_status": "not_requested"})

        with unittest.mock.patch("tasks.facebook_reel.time.sleep", return_value=None) as sleep:
            status, _ = task.post_reel_first_comment_after_refresh("https://example.com/link")

        self.assertEqual(status, "failed_input_not_found")
        self.assertEqual(task.navigate_to.call_count, 2)
        sleep.assert_called_once_with(10.0)
        task.post_first_comment.assert_not_called()

    def test_latest_reel_scan_scrolls_past_profile_header_and_loading_cards(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        screen = np.zeros((100, 100, 3), dtype=np.uint8)
        task.log = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = screen
        task.human = Mock()
        task._latest_reel_is_visible = Mock(side_effect=[False, False, True])

        with unittest.mock.patch("tasks.facebook_reel.time.sleep", return_value=None):
            visible, final_screen = task._scan_profile_for_latest_reel()

        self.assertTrue(visible)
        self.assertIs(final_screen, screen)
        self.assertEqual(task.client.screenshot.call_count, 3)
        self.assertEqual(task.human.scroll.call_count, 2)
        task.human.scroll.assert_called_with("down", notches=2)

    def test_reel_comment_skips_post_comment_warm_down(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.set_stage = Mock()
        task.human = Mock()
        screen = np.zeros((100, 100, 3), dtype=np.uint8)
        task.client = Mock()
        task.client.screenshot.return_value = screen
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "Direct Reel comment", "confidence": 0.99, "center": (60, 60)},
        ]
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(return_value=((90, 75), screen))

        with unittest.mock.patch("time.sleep", return_value=None):
            result = task.post_first_comment(
                "Direct Reel comment",
                reuse_profile_page=True,
                warm_down_after_submit=False,
            )

        self.assertEqual(result, "submitted_verified")
        task.set_stage.assert_called_once_with("commenting")
        self.assertNotIn(
            "after_comment_modal_close",
            [call.args[0] for call in task.capture_evidence.call_args_list],
        )
        task.human.click.assert_called_once_with(90, 75)

    def test_comment_verification_includes_upper_modal_comment_stream(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        task.vision = Mock()
        visible_comment = {
            "text": "Verification comment profile 002",
            "confidence": 0.95,
            "center": (50, 42),
        }

        def regional_ocr(_screen, region=None, **_kwargs):
            x, y, width, height = region
            return [visible_comment] if y <= 42 <= y + height else []

        task.vision.read_text.side_effect = regional_ocr
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(
            return_value=((70, 92), np.zeros((100, 100, 3), dtype=np.uint8))
        )

        with unittest.mock.patch("time.sleep", return_value=None), \
             unittest.mock.patch("tasks.base_task.random.uniform", return_value=1.0):
            result = task.post_first_comment("Verification comment — profile 002")

        self.assertEqual(result, "submitted_verified")
        self.assertLessEqual(task.vision.read_text.call_args.kwargs["region"][1], 42)

    def test_comment_verification_crop_includes_right_side_link_preview(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()
        task.human = Mock()
        task.client = Mock()
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.client.screenshot.return_value = screen
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {
                "text": "Senate Rejects Sanders-Led Effort to Block Arms Sale to Israel",
                "confidence": 0.95,
                "center": (1500, 740),
            }
        ]
        task.paste_text = Mock()
        task._open_profile_first_comment_input = Mock(return_value=((1167, 839), screen))

        with unittest.mock.patch("time.sleep", return_value=None), \
             unittest.mock.patch("tasks.base_task.random.uniform", return_value=1.0):
            result = task.post_first_comment(
                "https://asiandot.com/article/senate-rejects-sanders-led-effort-to-block-arms-sale-to-israel"
            )

        self.assertEqual(result, "submitted_verified")
        region = task.vision.read_text.call_args.kwargs["region"]
        self.assertGreaterEqual(region[0] + region[2], 1800)

    def test_vision_find_comment_input_locates_pill(self):
        vision = VisionEngine(Mock(profile_id="test"))
        vision.read_text = Mock(return_value=[
            {"text": "Comment as John Doe", "bounds": (600, 950, 750, 970), "center": (675, 960), "confidence": 0.96},
        ])
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        target = vision.find_comment_input(screen)
        self.assertIsNotNone(target)
        self.assertEqual(target, (660, 960))

    def test_vision_comment_input_prefers_first_post_over_higher_confidence(self):
        vision = VisionEngine(Mock(profile_id="test"))
        vision.read_text = Mock(return_value=[
            {"text": "Comment as Page", "bounds": (1100, 850, 1300, 890), "center": (1200, 870), "confidence": 0.99},
            {"text": "Comment as Page", "bounds": (1100, 410, 1300, 450), "center": (1200, 430), "confidence": 0.70},
        ])
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(vision.find_comment_input(screen), (1160, 430))

    def test_comment_action_prefers_first_post(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.vision = Mock()
        task.vision.read_text.return_value = [
            {"text": "Comment", "center": (1130, 820), "confidence": 0.99},
            {"text": "Comment", "center": (1130, 390), "confidence": 0.75},
            {"text": "2 comments", "center": (1400, 350), "confidence": 0.98},
        ]
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(task._find_first_comment_action(screen)["center"], (1130, 390))

    def test_standalone_comment_task_flow(self):
        from tasks.facebook_comment import FacebookCommentTask
        task = FacebookCommentTask.__new__(FacebookCommentTask)
        task.profile_id = "profile_test"
        task.comment_text = "https://example.com"
        task.post_url = None
        task.client = Mock()
        task.client.is_running.return_value = True
        task.verify_logged_in = Mock(return_value=True)
        task.post_first_comment = Mock(return_value="submitted_verified")
        task.set_outcome = Mock(return_value=True)
        task.log = Mock()

        self.assertTrue(task.run())
        task.verify_logged_in.assert_called_once_with(
            target_url="https://www.facebook.com/me"
        )
        task.post_first_comment.assert_called_once_with(
            "https://example.com",
            post_url=None,
            reuse_profile_page=True,
        )
        task.set_outcome.assert_called_once_with(
            "completed",
            None,
            first_comment="submitted_verified",
            first_comment_method=None,
        )


if __name__ == "__main__":
    unittest.main()
