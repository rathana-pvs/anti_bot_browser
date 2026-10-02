import os
import threading
import unittest
from collections import OrderedDict
from unittest.mock import Mock, patch

import cv2
import numpy as np

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.vision import VisionEngine
from ocr_worker import OcrRuntime, _watch_parent


class SharedOcrRuntimeTests(unittest.TestCase):
    def test_runtime_reuses_results_for_identical_images(self):
        runtime = OcrRuntime.__new__(OcrRuntime)
        runtime.device = "cuda"
        runtime.reader = Mock()
        runtime.reader.readtext.return_value = [
            ([[1, 2], [11, 2], [11, 8], [1, 8]], "Post", 0.9),
        ]
        runtime.error = None
        runtime.ready = threading.Event()
        runtime.ready.set()
        runtime.inference_lock = threading.Lock()
        runtime.cache_lock = threading.Lock()
        runtime.cache = OrderedDict()

        ok, encoded = cv2.imencode(".png", np.zeros((32, 64, 3), dtype=np.uint8))
        self.assertTrue(ok)
        first = runtime.read(encoded.tobytes(), ("en",))
        second = runtime.read(encoded.tobytes(), ("en",))

        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["items"], second["items"])
        self.assertEqual(runtime.reader.readtext.call_count, 1)

    def test_parent_watchdog_stops_worker_after_backend_exits(self):
        server = Mock()
        with patch("ocr_worker._parent_is_alive", side_effect=[True, False]), \
             patch("ocr_worker.time.sleep"):
            _watch_parent(1234, server, poll_interval=0.01)

        server.shutdown.assert_called_once_with()


class SharedOcrVisionTests(unittest.TestCase):
    def test_vision_prefers_shared_worker_over_local_reader(self):
        client = Mock(profile_id="shared_ocr_profile")
        vision = VisionEngine(client)
        screen = np.zeros((100, 200, 3), dtype=np.uint8)
        response = {
            "device": "cuda",
            "cache_hit": False,
            "items": [
                {
                    "box": [[10, 10], [80, 10], [80, 30], [10, 30]],
                    "text": "Post",
                    "confidence": 0.95,
                }
            ],
        }

        with patch("engine.vision.shared_worker_configured", return_value=True), \
             patch("engine.vision.read_text_with_shared_worker", return_value=response), \
             patch.object(VisionEngine, "_get_ocr_reader") as local_reader:
            result = vision.read_text(screen)

        self.assertEqual(result[0]["text"], "Post")
        self.assertEqual(vision.ocr_device, "cuda")
        local_reader.assert_not_called()

    def test_vision_falls_back_when_shared_worker_is_unavailable(self):
        client = Mock(profile_id="shared_ocr_fallback_profile")
        vision = VisionEngine(client)
        screen = np.zeros((100, 200, 3), dtype=np.uint8)
        local_reader = Mock()
        local_reader.readtext.return_value = []

        from engine.ocr_worker_client import SharedOcrUnavailable

        with patch("engine.vision.shared_worker_configured", return_value=True), \
             patch("engine.vision.read_text_with_shared_worker", side_effect=SharedOcrUnavailable("offline")), \
             patch.object(VisionEngine, "_get_ocr_reader", return_value=(local_reader, "cpu")):
            result = vision.read_text(screen)

        self.assertEqual(result, [])
        self.assertEqual(local_reader.readtext.call_count, 1)
        self.assertEqual(vision.ocr_device, "cpu")


if __name__ == "__main__":
    unittest.main()
