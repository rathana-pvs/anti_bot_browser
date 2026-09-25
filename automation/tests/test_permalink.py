"""Unit tests for Phase 1 Facebook permalink validation and visual correlation."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tasks.base_task import BaseTask
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask


class PermalinkValidationTests(unittest.TestCase):
    def test_valid_post_permalinks(self):
        valid_urls = [
            "https://www.facebook.com/username/posts/pfbid025Xk4w1?mibextid=ZbWKwL",
            "https://facebook.com/permalink.php?story_fbid=pfbid123&id=61551580380381&rdid=xyz",
            "https://m.facebook.com/photo.php?fbid=123456789&set=a.100",
            "https://web.facebook.com/groups/123456/permalink/789012/",
        ]
        for url in valid_urls:
            valid, clean = BaseTask.validate_facebook_permalink(url, post_type="post")
            self.assertTrue(valid, f"Expected {url} to be valid post permalink")
            self.assertIsNotNone(clean)
            self.assertNotIn("mibextid", clean)
            self.assertNotIn("rdid", clean)

    def test_valid_reel_permalinks(self):
        valid_reels = [
            "https://www.facebook.com/reel/123456789012345?s=yWDuG2&fs=e",
            "https://facebook.com/username/videos/987654321098765/",
            "https://www.facebook.com/watch/?v=112233445566&ref=sharing",
        ]
        for url in valid_reels:
            valid, clean = BaseTask.validate_facebook_permalink(url, post_type="reel")
            self.assertTrue(valid, f"Expected {url} to be valid reel permalink")
            self.assertIsNotNone(clean)

    def test_reject_naked_profile_and_system_urls(self):
        invalid_urls = [
            "https://www.facebook.com/me",
            "https://www.facebook.com/profile.php?id=61551580380381",
            "https://www.facebook.com/profile.php?id=61551580380381&sk=about",
            "https://www.facebook.com/home.php",
            "https://www.facebook.com/login.php",
            "https://www.facebook.com/checkpoint",
            "https://malicious-facebook.com/posts/123",
            "https://google.com/search?q=facebook",
            "",
            None,
        ]
        for url in invalid_urls:
            valid, clean = BaseTask.validate_facebook_permalink(url, post_type="post")
            self.assertFalse(valid, f"Expected {url} to be rejected")
            self.assertIsNone(clean)


class PermalinkCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.task = FacebookPostTask.__new__(FacebookPostTask)
        self.task.client = Mock()
        self.task.human = Mock()
        self.task.vision = Mock()
        self.task.log = Mock()
        self.task.log_decision = Mock()
        self.task.capture_evidence = Mock()
        self.task.evidence = Mock()
        self.task.client.screenshot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

    @patch("time.sleep", return_value=None)
    def test_correlate_and_extract_success(self, _):
        # OCR detects caption words and "Just now" timestamp
        self.task.vision.read_text.return_value = [
            {"text": "Account 3", "confidence": 0.95, "center": (300, 180)},
            {"text": "Just now", "confidence": 0.92, "center": (300, 205)},
            {"text": "Summer vacation vibes at the beach", "confidence": 0.90, "center": (300, 250)},
        ]
        self.task.client.get_current_url.return_value = "https://www.facebook.com/username/posts/pfbid123?mibextid=xyz"

        result = self.task.correlate_and_extract_permalink(
            caption="Summer vacation vibes at the beach",
            media_type="post",
        )

        self.assertIsNotNone(result["post_url"])
        self.assertEqual(result["post_url"], "https://www.facebook.com/username/posts/pfbid123")
        self.assertGreaterEqual(result["post_match_confidence"], 0.70)
        self.assertIsNotNone(result["post_url_verified_at"])
        # Verify timestamp click was invoked at (300, 205)
        self.task.human.click.assert_called_with(300, 205)

    @patch("time.sleep", return_value=None)
    def test_correlate_ambiguous_leaves_url_none(self, _):
        # OCR finds completely unrelated text
        self.task.vision.read_text.return_value = [
            {"text": "Intro", "confidence": 0.90, "center": (100, 100)},
            {"text": "Manage posts", "confidence": 0.95, "center": (200, 200)},
        ]

        result = self.task.correlate_and_extract_permalink(
            caption="Special announcement today",
            media_type="post",
            max_scans=1,
        )

        self.assertIsNone(result["post_url"])
        self.assertEqual(result["post_match_confidence"], 0.0)
        self.assertIsNone(result["post_url_verified_at"])

    def test_recent_timestamp_accepts_observed_dark_mode_ocr_substitutions(self):
        self.assertTrue(BaseTask._is_recent_timestamp_text("a tew seconds a00"))
        self.assertTrue(BaseTask._is_recent_timestamp_text("Jusl n0w"))
        self.assertTrue(BaseTask._is_recent_timestamp_text("7Minutes Ano"))
        self.assertFalse(BaseTask._is_recent_timestamp_text("published last year"))

    @patch("time.sleep", return_value=None)
    def test_correlates_canary_when_caption_is_split_and_timestamp_has_ocr_noise(self, _):
        self.task.vision.read_text.return_value = [
            {"text": "Fat Frog", "confidence": 0.93, "center": (1135, 851)},
            {"text": "a tew seconds a00", "confidence": 0.25, "center": (1348, 871)},
            {"text": "Telemetry canary test", "confidence": 0.98, "center": (1173, 903)},
            {"text": "September 25, 2026", "confidence": 0.76, "center": (1432, 901)},
        ]
        self.task.client.get_current_url.return_value = (
            "https://www.facebook.com/fatfrog/posts/pfbidCanary123?mibextid=tracking"
        )

        result = self.task.correlate_and_extract_permalink(
            caption="Telemetry canary test — September 25, 2026",
            media_type="post",
        )

        self.assertEqual(
            result["post_url"],
            "https://www.facebook.com/fatfrog/posts/pfbidCanary123",
        )
        self.task.human.click.assert_called_with(1348, 871)

    @patch("time.sleep", return_value=None)
    def test_does_not_bind_unrelated_recent_timestamp_to_caption(self, _):
        self.task.vision.read_text.return_value = [
            {"text": "Just now", "confidence": 0.95, "center": (50, 50)},
            {"text": "Special announcement today", "confidence": 0.95, "center": (300, 250)},
        ]

        result = self.task.correlate_and_extract_permalink(
            caption="Special announcement today",
            media_type="post",
            max_scans=1,
        )

        self.assertIsNone(result["post_url"])
        self.task.human.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
