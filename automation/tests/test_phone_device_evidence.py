"""Phone evidence shows the frame a decision was judged on, not a later live frame."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(1, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from phone import device as device_module
from phone.device import JUDGED_MAX_AGE_S, PhoneDevice

LOADING = np.full((1280, 592, 3), 240, dtype=np.uint8)
LOADED = np.full((1280, 592, 3), 90, dtype=np.uint8)


class FakeReader:
    def readtext(self, image):
        return []


class FakeSession:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


def make_device(directory):
    dev = PhoneDevice.__new__(PhoneDevice)
    dev.evidence_dir = Path(directory)
    dev.reader = FakeReader()
    dev.session = FakeSession()
    dev.width, dev.height = 592, 1280
    dev._frame = LOADING
    dev.frame_count = 1
    dev._lock = threading.Lock()
    dev._judged = None
    dev._acted_at = 0.0
    dev.client = None
    dev.serial, dev.user_id = "fixture", 10
    return dev


def saved(directory, name):
    image = cv2.imread(str(Path(directory) / f"{name}.png"))
    return None if image is None else int(image[0, 0, 0])


class PhoneEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.dev = make_device(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_saves_the_read_frame_and_the_newer_screen_after(self):
        asyncio.run(self.dev.read())
        self.dev._frame = LOADED  # the stream moved on while OCR ran
        self.dev.snapshot("fail_step")
        self.assertEqual(saved(self.dir, "fail_step"), 240)
        self.assertEqual(saved(self.dir, "fail_step_after"), 90)

    def test_unchanged_screen_saves_no_after_frame(self):
        asyncio.run(self.dev.read())
        self.dev.snapshot("fail_step")
        self.assertEqual(saved(self.dir, "fail_step"), 240)
        self.assertIsNone(saved(self.dir, "fail_step_after"))

    def test_after_a_tap_the_live_screen_is_saved(self):
        asyncio.run(self.dev.read())
        self.dev._frame = LOADED
        with patch.object(device_module.asyncio, "sleep", return_value=None), \
                patch.object(PhoneDevice, "_clear_of_approval", return_value=None):
            asyncio.run(self.dev.key(4))
        self.dev.snapshot("comment_posted")
        self.assertEqual(saved(self.dir, "comment_posted"), 90)
        self.assertIsNone(saved(self.dir, "comment_posted_after"))

    def test_icon_search_counts_as_judging(self):
        self.dev.templates = {"comment": np.zeros((10, 10, 3), dtype=np.uint8)}
        with patch.object(device_module, "find_template", return_value=None):
            self.dev.find_icon("comment")
        self.dev._frame = LOADED
        self.assertEqual(int(self.dev.judged_image()[0, 0, 0]), 240)

    def test_old_judged_frame_is_not_used(self):
        asyncio.run(self.dev.read())
        image, _ = self.dev._judged
        self.dev._judged = (image, time.monotonic() - JUDGED_MAX_AGE_S - 1)
        self.dev._frame = LOADED
        self.assertEqual(int(self.dev.judged_image()[0, 0, 0]), 90)

    def test_without_any_read_the_live_frame_is_used(self):
        self.assertEqual(int(self.dev.judged_image()[0, 0, 0]), 240)


if __name__ == "__main__":
    unittest.main()
