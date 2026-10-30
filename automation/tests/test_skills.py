"""Shared skills against a fake host: no browser, Docker or OCR runtime."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import ScreenState, StateObservation
from skills.comment import comment_match_confidence, first_comment_search_regions
from skills.dialogs import KnownDialogs
from skills.media import MediaAttachment
from skills.navigation import Navigation
from skills.permalink import permalink_not_requested, validate_facebook_permalink
from skills.session import SessionCheck, has_strong_facebook_visual, is_facebook_url
from skills.session_habits import MAX_LINGER_SECONDS, SessionHabits
from engine.behavior_profile import BehaviorSession
from modules.publishing_pipeline import LingerModule
from skills.text_entry import TextEntry
from modules.composer_entry import CaptionReviewFailure, ClipboardFailure


def fake_host(**overrides):
    host = SimpleNamespace(
        client=Mock(),
        human=Mock(),
        vision=Mock(),
        recognizer=Mock(),
        log=Mock(),
        capture_evidence=Mock(),
        telemetry=None,
    )
    host.client.screenshot.return_value = np.zeros((1000, 2000, 3), dtype=np.uint8)
    for name, value in overrides.items():
        setattr(host, name, value)
    return host


class KnownDialogsTests(unittest.TestCase):
    def test_leave_site_clicks_leave_through_human_input(self):
        host = fake_host()
        host.vision.find_leave_site_button.return_value = (640, 410)
        with patch("skills.dialogs.time.sleep"):
            self.assertTrue(KnownDialogs(host).leave_site())
        host.human.click.assert_called_once_with(640, 410)
        host.client.dismiss_dialog_key.assert_not_called()

    def test_leave_site_absent_does_nothing(self):
        host = fake_host()
        host.vision.find_leave_site_button.return_value = None
        self.assertFalse(KnownDialogs(host).leave_site())
        host.human.click.assert_not_called()

    def test_post_prompt_picks_the_most_confident_not_now(self):
        host = fake_host()
        host.vision.read_text.return_value = [
            {"text": "Not now", "confidence": 0.6, "center": (900, 700)},
            {"text": "Share now", "confidence": 0.99, "center": (900, 600)},
            {"text": "not n0w", "confidence": 0.9, "center": (910, 710)},
        ]
        match = KnownDialogs(host).find_post_publish_prompt()
        self.assertEqual(match["center"], (910, 710))
        region = host.vision.read_text.call_args.kwargs["region"]
        self.assertEqual(region, (300, 80, 1400, 870))

    def test_dismiss_post_prompt_uses_the_host_locator_and_reversible_click(self):
        host = fake_host(
            find_post_publish_prompt=Mock(return_value={"center": (850, 800), "bounds": (800, 780, 900, 820)}),
            click_reversible=Mock(),
        )
        with patch("skills.dialogs.time.sleep"):
            self.assertTrue(KnownDialogs(host).dismiss_post_prompt())
        host.click_reversible.assert_called_once_with(
            (850, 800), label="dismiss_post_prompt", bounds=(800, 780, 900, 820), max_offset_px=4,
        )

    def test_post_publish_prompt_prefers_the_shared_handler(self):
        handler = Mock()
        handler.handle.return_value = "dismissed"
        host = fake_host(post_publish_prompt=handler, check_and_dismiss_post_prompt=Mock())
        self.assertEqual(KnownDialogs(host).handle_post_publish_prompt(), "dismissed")
        host.check_and_dismiss_post_prompt.assert_not_called()


class NavigationTests(unittest.TestCase):
    def test_go_to_types_through_human_and_checks_leave_dialog(self):
        host = fake_host(handle_leave_site_dialog=Mock(return_value=False), telemetry=Mock())
        with patch("skills.navigation.time.sleep"):
            Navigation(host).go_to("https://www.facebook.com/me", wait_seconds=3.0)
        host.human.navigate.assert_called_once_with("https://www.facebook.com/me")
        host.handle_leave_site_dialog.assert_called_once_with()
        url, _elapsed, outcome = host.telemetry.record_navigation.call_args.args
        self.assertEqual((url, outcome), ("https://www.facebook.com/me", "completed"))

    def test_select_surface_falls_back_once(self):
        host = fake_host(
            WARMING_SURFACES={"news_feed": "https://www.facebook.com/", "profile": "https://www.facebook.com/me"},
            open_warming_surface=Mock(side_effect=[(False, "first"), (True, "second")]),
            session_check_status="authenticated",
        )
        result = Navigation(host).select_and_open_surface("news_feed")
        self.assertEqual(result, ("news_feed", "profile", True, "second"))


class SessionCheckTests(unittest.TestCase):
    def test_facebook_host_rejects_lookalikes(self):
        self.assertTrue(is_facebook_url("https://www.facebook.com/me"))
        self.assertFalse(is_facebook_url("https://facebook.com.evil.example/"))
        self.assertFalse(is_facebook_url("javascript:facebook.com"))

    def test_strong_visual_needs_a_facebook_signal(self):
        self.assertTrue(has_strong_facebook_visual(
            StateObservation(ScreenState.FEED_READY, 0.9, ["photo/video"])))
        self.assertFalse(has_strong_facebook_visual(
            StateObservation(ScreenState.FEED_READY, 0.9, ["facebook logo"])))

    def test_login_window_title_stops_before_any_screen_polling(self):
        host = fake_host(navigate_to=Mock())
        host.client.active_window_name.return_value = "Log in to Facebook"
        self.assertFalse(SessionCheck(host).verify("https://www.facebook.com/me"))
        self.assertEqual(host.session_check_status, "auth_required")
        host.navigate_to.assert_called_once_with(
            "https://www.facebook.com/me", wait_seconds=2.0, check_leave_dialog=False)
        host.recognizer.observe_session_gate.assert_not_called()


class MediaAttachmentTests(unittest.TestCase):
    def chooser_host(self):
        visible = {"value": True}
        host = fake_host(
            FILE_CHOOSER_DIALOG_NAMES=("Open File", "File Upload"),
            telemetry=Mock(),
        )
        host.find_visible_file_chooser = lambda: MediaAttachment(host).find_chooser()
        host.client.find_windows.side_effect = lambda name: ["42"] if visible["value"] and name != ".*" else []
        host.client.window_geometry.return_value = {"X": 100, "Y": 100, "WIDTH": 1000, "HEIGHT": 700}
        host.client.file_exists.return_value = True
        host.client.ensure_focus.return_value = 99
        host.human.click.side_effect = lambda *_: visible.update(value=False)
        return host

    def test_attach_types_path_into_the_dialog_and_clicks_open(self):
        host = self.chooser_host()
        host.vision.find_text.return_value = {"center": (1020, 760)}
        with patch("skills.media.time.sleep"):
            self.assertTrue(MediaAttachment(host).attach("/data/shared_media/a.png"))
        host.client.focus_window.assert_called_once_with("42")
        host.human.type_native.assert_called_once_with("/data/shared_media/a.png")
        host.human.click.assert_called_once_with(1020, 760)
        self.assertEqual(host.telemetry.record_step.call_args.args[0], "media_upload")

    def test_attach_refuses_blind_input_without_a_chooser(self):
        host = self.chooser_host()
        host.client.find_windows.side_effect = lambda name: []
        with patch("skills.media.time.sleep"):
            self.assertFalse(MediaAttachment(host).attach("/data/shared_media/a.png"))
        host.human.type_native.assert_not_called()
        host.human.key_press.assert_not_called()

    def test_attach_stops_when_the_file_is_missing(self):
        host = self.chooser_host()
        host.client.file_exists.return_value = False
        with patch("skills.media.time.sleep"):
            self.assertFalse(MediaAttachment(host).attach("/data/shared_media/missing.png"))
        host.human.type_native.assert_not_called()


class TextEntryTests(unittest.TestCase):
    def test_paste_writes_clipboard_then_presses_paste_key(self):
        host = fake_host(input_cancelled=False)
        host.client.input_owner.return_value = ("chrome", "1|2")
        TextEntry(host).paste("ខ្មែរ 😀")
        host.client.write_clipboard.assert_called_once_with("ខ្មែរ 😀", "clipboard")
        host.human.key_press.assert_called_once_with("ctrl+v")

    def test_paste_stops_when_focus_moves(self):
        host = fake_host(input_cancelled=False)
        host.client.input_owner.return_value = ("other", "")
        with self.assertRaises(ClipboardFailure):
            TextEntry(host).paste("caption", expected_owner=("chrome", ""))
        host.client.write_clipboard.assert_not_called()
        host.human.key_press.assert_not_called()

    def test_review_rejects_a_caption_changed_during_review(self):
        host = fake_host(composer_entry=Mock(last_owner=("chrome", "")), composer_review=Mock(), telemetry=Mock())
        host.composer_review.review.return_value = 2.5
        host.composer_entry.read_field.return_value = "edited"
        with self.assertRaises(CaptionReviewFailure) as raised:
            TextEntry(host).review("original")
        self.assertEqual(raised.exception.reason, "caption_changed_after_review")
        self.assertEqual(host.telemetry.record_step.call_args.kwargs["outcome"], "unconfirmed")


class FirstCommentTests(unittest.TestCase):
    def test_comment_must_render_above_the_input_row(self):
        items = [
            {"text": "Read more: example.com/news", "center": (900, 400)},
            {"text": "Read more: example.com/news", "center": (900, 900)},
        ]
        self.assertEqual(comment_match_confidence("Read more: example.com/news", items[:1], 800), 1.0)
        self.assertEqual(comment_match_confidence("Read more: example.com/news", items[1:], 800), 0.0)

    def test_search_regions_scale_with_the_screen(self):
        narrow, broad = first_comment_search_regions(np.zeros((1000, 2000, 3), dtype=np.uint8))
        self.assertEqual(narrow, (1060, 620, 760, 290))
        self.assertEqual(broad, (560, 0, 1340, 1000))


class PermalinkTests(unittest.TestCase):
    def test_profile_pages_are_not_permalinks(self):
        self.assertEqual(validate_facebook_permalink("https://www.facebook.com/me"), (False, None))
        self.assertEqual(validate_facebook_permalink("https://evil.example/reel/123", "reel"), (False, None))

    def test_reel_permalink_is_accepted(self):
        valid, clean = validate_facebook_permalink("https://www.facebook.com/reel/123456789/?s=1", "reel")
        self.assertTrue(valid)
        self.assertIn("/reel/123456789", clean)

    def test_not_requested_is_explicit(self):
        self.assertEqual(permalink_not_requested()["permalink_status"], "not_requested")


class SessionHabitsTests(unittest.TestCase):
    def linger_host(self, *, natural=True, status="published"):
        host = fake_host(result_status=status, telemetry=Mock())
        host.behavior_session = BehaviorSession("medium", 77, "linger", natural=natural)
        host.client.get_screen_dimensions.return_value = (1920, 1080)
        host.client.get_mouse_position.return_value = (900, 500)
        return host

    def test_lingers_only_after_a_confirmed_publish_with_the_switch_on(self):
        self.assertTrue(SessionHabits(self.linger_host()).wants_to_linger())
        self.assertFalse(SessionHabits(self.linger_host(natural=False)).wants_to_linger())
        for status in ("uncertain", "failed_after_publish", "needs_review", "pending_profile_verification"):
            self.assertFalse(SessionHabits(self.linger_host(status=status)).wants_to_linger())

    def test_linger_is_passive_and_bounded(self):
        host = self.linger_host()
        clock = iter(x * 0.5 for x in range(100000))
        with patch.object(host.behavior_session, "chance", return_value=True), \
             patch("skills.session_habits.time.monotonic", side_effect=lambda: next(clock)), \
             patch("skills.session_habits.time.sleep"):
            summary = SessionHabits(host).linger()
        self.assertTrue(summary["lingered"])
        self.assertLessEqual(summary["linger_seconds"], MAX_LINGER_SECONDS + 7.0)
        self.assertGreater(host.human.scroll.call_count, 0)
        host.human.click.assert_not_called()
        host.human.key_press.assert_not_called()

    def test_linger_error_never_changes_the_outcome(self):
        host = self.linger_host()
        host.client.get_screen_dimensions.side_effect = RuntimeError("display gone")
        with patch.object(host.behavior_session, "chance", return_value=True):
            result = LingerModule(host).run(None)
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(host.result_status, "published")


if __name__ == "__main__":
    unittest.main()
