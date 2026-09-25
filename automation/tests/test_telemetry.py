import json
import os
import tempfile
import time
import unittest

import numpy as np

from engine.telemetry import TelemetryRecorder
from engine.vision import VisionEngine


class TelemetryRecorderTests(unittest.TestCase):
    def test_records_stage_ocr_locator_and_atomic_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = TelemetryRecorder("profile_test", "PostTask", temp_dir)
            recorder.set_environment(locale="en-US", configured_screen_resolution="1920x1080")
            recorder.record_stage("preparing")
            time.sleep(0.001)
            recorder.record_stage("composing")
            recorder.record_ocr(12.5, "bottom_action_bar", 2, [0.7, 0.91])
            recorder.record_ocr(0.2, "bottom_action_bar", 2, [0.7, 0.91], outcome="cache_hit")
            recorder.record_locator(
                "post",
                "expanded_region_ocr",
                "bottom_action_bar",
                14.0,
                True,
                confidence=0.91,
                fallback_reason="target_region_miss",
            )

            snapshot = recorder.finalize("published")
            self.assertEqual(snapshot["outcome"], "published")
            self.assertGreater(snapshot["stage_durations_ms"]["preparing"], 0)
            self.assertEqual(snapshot["ocr"]["calls"], 2)
            self.assertEqual(snapshot["ocr"]["inference_calls"], 1)
            self.assertEqual(snapshot["ocr"]["cache_hits"], 1)
            self.assertEqual(snapshot["ocr"]["cache_hit_rate_pct"], 50.0)
            self.assertEqual(snapshot["locators"]["fallback_count"], 1)
            self.assertEqual(snapshot["environment"]["locale"], "en-US")

            path = os.path.join(temp_dir, "telemetry.json")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["schema_version"], "1.0")
            self.assertFalse(os.path.exists(f"{path}.tmp"))

    def test_theme_preflight_detects_dark_light_and_unknown(self):
        dark = np.full((200, 300, 3), 35, dtype=np.uint8)
        light = np.full((200, 300, 3), 235, dtype=np.uint8)
        mixed = np.zeros((200, 300, 3), dtype=np.uint8)
        mixed[:, :150] = 35
        mixed[:, 150:] = 235

        dark_theme, dark_confidence = VisionEngine.infer_theme(dark)
        light_theme, light_confidence = VisionEngine.infer_theme(light)
        unknown_theme, _ = VisionEngine.infer_theme(mixed)

        self.assertEqual(dark_theme, "dark")
        self.assertGreaterEqual(dark_confidence, 0.90)
        self.assertEqual(light_theme, "light")
        self.assertGreaterEqual(light_confidence, 0.90)
        self.assertEqual(unknown_theme, "unknown")


if __name__ == "__main__":
    unittest.main()
