"""Unit tests for Phase 2: Measured Local Vision Optimization.

Tests:
  1. Resolution-independent normalized bounding box conversions.
  2. Safe boundary-clipped region expansion logic.
  3. 3-Tier cascaded OCR localization (tight region -> expanded region -> full screen fallback).
  4. Resilient element cache with TTL expiration, consecutive failure invalidation,
     and atomic file persistence.
  5. Backwards compatibility with legacy cache formats.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import numpy as np

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.vision import VisionEngine, NORMALIZED_REGIONS


class VisionRegionTests(unittest.TestCase):
    def test_get_pixel_region_resolutions(self):
        # 1920x1080
        shape_1080 = (1080, 1920, 3)
        region_1080 = VisionEngine.get_pixel_region(shape_1080, "bottom_action_bar")
        self.assertEqual(region_1080[0], 0)
        self.assertEqual(region_1080[1], int(round(1080 * 0.68)))
        self.assertEqual(region_1080[2], 1920)
        self.assertEqual(region_1080[3], 1080 - int(round(1080 * 0.68)))

        # 1280x720
        shape_720 = (720, 1280, 3)
        region_720 = VisionEngine.get_pixel_region(shape_720, "bottom_action_bar")
        self.assertEqual(region_720[0], 0)
        self.assertEqual(region_720[1], int(round(720 * 0.68)))
        self.assertEqual(region_720[2], 1280)
        self.assertEqual(region_720[3], 720 - int(round(720 * 0.68)))

        # 1366x768
        shape_768 = (768, 1366, 3)
        region_768 = VisionEngine.get_pixel_region(shape_768, "bottom_action_bar")
        self.assertEqual(region_768[0], 0)
        self.assertEqual(region_768[1], int(round(768 * 0.68)))
        self.assertEqual(region_768[2], 1366)
        self.assertEqual(region_768[3], 768 - int(round(768 * 0.68)))

    def test_get_pixel_region_case_insensitivity(self):
        shape = (1080, 1920, 3)
        lower = VisionEngine.get_pixel_region(shape, "composer_modal")
        upper = VisionEngine.get_pixel_region(shape, "COMPOSER_MODAL")
        mixed = VisionEngine.get_pixel_region(shape, "  Composer_Modal  ")
        self.assertEqual(lower, upper)
        self.assertEqual(lower, mixed)

    def test_get_pixel_region_custom_normalized_tuple(self):
        shape = (1000, 1000, 3)
        custom_norm = (0.10, 0.20, 0.50, 0.60)
        px_region = VisionEngine.get_pixel_region(shape, custom_norm)
        self.assertEqual(px_region, (100, 200, 400, 400))

    def test_expand_region_within_screen_bounds(self):
        shape = (1000, 1000, 3)
        base = (200, 200, 200, 200)
        # 20% expansion on w=200, h=200 -> pad_x=40, pad_y=40
        # new_x = 160, new_y = 160, new_w = 280, new_h = 280
        expanded = VisionEngine.expand_region(shape, base, ratio=0.20)
        self.assertEqual(expanded, (160, 160, 280, 280))

    def test_expand_region_clips_at_boundaries(self):
        shape = (1000, 1000, 3)
        # Edge region near (0, 0)
        near_origin = (10, 10, 100, 100)
        expanded = VisionEngine.expand_region(shape, near_origin, ratio=0.50)
        self.assertEqual(expanded[0], 0)  # Clipped at left boundary
        self.assertEqual(expanded[1], 0)  # Clipped at top boundary
        self.assertLessEqual(expanded[0] + expanded[2], 1000)
        self.assertLessEqual(expanded[1] + expanded[3], 1000)

        # Edge region near bottom right
        near_br = (950, 950, 50, 50)
        expanded_br = VisionEngine.expand_region(shape, near_br, ratio=0.50)
        self.assertLessEqual(expanded_br[0] + expanded_br[2], 1000)
        self.assertLessEqual(expanded_br[1] + expanded_br[3], 1000)


class VisionCascadeTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.profile_id = "test_cascade_profile"
        self.client.screenshot.return_value = np.zeros((1000, 1000, 3), dtype=np.uint8)
        self.vision = VisionEngine(self.client)

    def test_find_text_cascaded_tier1_hit(self):
        """Tier 1: Found in the tight expected region on first attempt."""
        target_item = {
            "text": "Post",
            "confidence": 0.95,
            "bounds": (500, 750, 600, 800),
            "center": (550, 775),
        }
        self.vision.find_text = Mock(return_value=target_item)

        result = self.vision.find_text_cascaded(
            "post",
            region="bottom_action_bar",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["center"], (550, 775))
        # Should only call find_text once for the target region
        self.assertEqual(self.vision.find_text.call_count, 1)

    def test_find_text_cascaded_tier2_expanded_hit(self):
        """Tier 2: Misses initial tight region, but succeeds in expanded region."""
        expanded_item = {
            "text": "Post",
            "confidence": 0.92,
            "bounds": (500, 660, 600, 700),
            "center": (550, 680),
        }

        # First call (tight region) returns None, second call (expanded region) returns item
        self.vision.find_text = Mock(side_effect=[None, expanded_item])

        result = self.vision.find_text_cascaded(
            "post",
            region="bottom_action_bar",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["center"], (550, 680))
        self.assertEqual(self.vision.find_text.call_count, 2)

    def test_find_text_cascaded_tier3_fullscreen_fallback(self):
        """Tier 3: Misses tight and expanded regions, falls back to full screen."""
        fullscreen_item = {
            "text": "Post",
            "confidence": 0.88,
            "bounds": (200, 200, 300, 250),
            "center": (250, 225),
        }

        # Calls: tight -> None, expanded -> None, full screen -> item
        self.vision.find_text = Mock(side_effect=[None, None, fullscreen_item])

        result = self.vision.find_text_cascaded(
            "post",
            region="bottom_action_bar",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["center"], (250, 225))
        self.assertEqual(self.vision.find_text.call_count, 3)
        # Check that the 3rd call passed region=None
        self.assertIsNone(self.vision.find_text.call_args_list[2].kwargs.get("region"))

    def test_find_text_cascaded_total_miss(self):
        """Total miss across all 3 tiers returns None."""
        self.vision.find_text = Mock(return_value=None)

        result = self.vision.find_text_cascaded(
            "missing_button",
            region="bottom_action_bar",
        )

        self.assertIsNone(result)
        self.assertEqual(self.vision.find_text.call_count, 3)


class ResilientElementCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.client = Mock()
        self.client.profile_id = "test_profile_cache"
        self.vision = VisionEngine(self.client)
        # Point cache directly to isolated temporary directory
        self.vision.cache_dir = self.temp_dir
        self.vision.cache_file = os.path.join(self.temp_dir, "element_cache.json")
        self.vision._cache = {}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_and_get_cached_hint_resolution_independent(self):
        screen_1080 = (1080, 1920, 3)
        # Button located at (1440, 810) on 1920x1080 display (75% x, 75% y)
        self.vision.record_cache_hit("test_btn", (1440, 810), screen_1080)

        # Verify entry in memory and on disk
        self.assertIn("test_btn", self.vision._cache)
        entry = self.vision._cache["test_btn"]
        self.assertEqual(entry["normalized_center"], [0.75, 0.75])
        self.assertEqual(entry["screen_size"], [1920, 1080])
        self.assertEqual(entry["success_count"], 1)
        self.assertEqual(entry["failure_count"], 0)

        # File exists on disk
        self.assertTrue(os.path.exists(self.vision.cache_file))

        # Query on 1280x720 screen: should scale (0.75 * 1280, 0.75 * 720) = (960, 540)
        screen_720 = (720, 1280, 3)
        hint = self.vision.get_cached_hint("test_btn", screen_720)
        self.assertEqual(hint, (960, 540))

    def test_cache_ttl_expiration(self):
        screen = (1080, 1920, 3)
        # Create an entry timestamped 73 hours ago (TTL is 72 hours)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
        self.vision._cache["expired_btn"] = {
            "normalized_center": [0.5, 0.5],
            "screen_size": [1920, 1080],
            "last_verified_at": old_time,
            "success_count": 5,
            "failure_count": 0,
        }
        self.vision._save_cache()

        # Cache hint should be None because TTL expired
        hint = self.vision.get_cached_hint("expired_btn", screen)
        self.assertIsNone(hint)
        # Should be evicted from cache
        self.assertNotIn("expired_btn", self.vision._cache)

    def test_consecutive_failure_invalidation(self):
        screen = (1080, 1920, 3)
        self.vision.record_cache_hit("failing_btn", (100, 100), screen)
        self.assertIn("failing_btn", self.vision._cache)

        # 1st miss
        self.vision.record_cache_miss("failing_btn")
        self.assertEqual(self.vision._cache["failing_btn"]["failure_count"], 1)
        self.assertIsNotNone(self.vision.get_cached_hint("failing_btn", screen))

        # 2nd miss
        self.vision.record_cache_miss("failing_btn")
        self.assertEqual(self.vision._cache["failing_btn"]["failure_count"], 2)
        self.assertIsNotNone(self.vision.get_cached_hint("failing_btn", screen))

        # 3rd miss -> MAX_CONSECUTIVE_FAILURES reached! Auto-invalidated
        self.vision.record_cache_miss("failing_btn")
        self.assertNotIn("failing_btn", self.vision._cache)
        self.assertIsNone(self.vision.get_cached_hint("failing_btn", screen))

    def test_atomic_persistence(self):
        screen = (1080, 1920, 3)
        self.vision.record_cache_hit("atomic_btn", (500, 500), screen)

        # Temporary file should have been cleanly replaced
        tmp_file = f"{self.vision.cache_file}.tmp"
        self.assertFalse(os.path.exists(tmp_file))
        self.assertTrue(os.path.exists(self.vision.cache_file))

        # Verify contents on disk
        with open(self.vision.cache_file, "r") as f:
            data = json.load(f)
        self.assertIn("atomic_btn", data)

    def test_legacy_cache_format_compatibility(self):
        screen = (1080, 1920, 3)
        # Legacy cache format: [x, y] coordinates
        self.vision._cache["legacy_btn"] = [450, 600]

        hint = self.vision.get_cached_hint("legacy_btn", screen)
        self.assertEqual(hint, (450, 600))


if __name__ == "__main__":
    unittest.main()
