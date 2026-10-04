import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from modules.publication_result_verifier import ImagePublicationResultVerifier


class ImagePublicationVerifierTests(unittest.TestCase):
    def make_verifier(self):
        task = Mock()
        task.caption = ""
        task._is_recent_timestamp_text.side_effect = lambda text: text == "Just now"
        return ImagePublicationResultVerifier(task)

    def test_first_scan_success_does_not_refresh(self):
        verifier = self.make_verifier()
        verifier._scan = Mock(return_value=(True, object()))
        self.assertTrue(verifier.verify_latest_image())
        verifier.task.navigate_to.assert_not_called()

    def test_miss_refreshes_once_and_rescans(self):
        verifier = self.make_verifier()
        verifier._scan = Mock(side_effect=[(False, object()), (True, object())])
        with patch("modules.publication_result_verifier.time.sleep"):
            self.assertTrue(verifier.verify_latest_image())
        verifier.task.navigate_to.assert_called_once_with("https://www.facebook.com/me", wait_seconds=3.0)

    def test_two_misses_fail_without_another_refresh(self):
        verifier = self.make_verifier()
        verifier._scan = Mock(return_value=(False, object()))
        with patch("modules.publication_result_verifier.time.sleep"):
            self.assertFalse(verifier.verify_latest_image())
        self.assertEqual(verifier._scan.call_count, 2)
        self.assertEqual(verifier.task.navigate_to.call_count, 1)

    def test_image_match_requires_recent_timestamp_and_caption(self):
        verifier = self.make_verifier()
        verifier._matching_image_bounds = Mock(return_value=(600, 500, 1100, 950))
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        verifier.task.vision.read_text.return_value = [{"text": "Yesterday"}]
        self.assertFalse(verifier._post_visible(screen))
        verifier.task.vision.read_text.return_value = [{"text": "Just now"}]
        self.assertTrue(verifier._post_visible(screen))
        verifier.task.caption = "Expected caption"
        self.assertFalse(verifier._post_visible(screen))
        verifier.task.vision.read_text.return_value.append({"text": "Expected caption"})
        self.assertTrue(verifier._post_visible(screen))

    def test_matches_resized_source_and_rejects_different_image(self):
        verifier = self.make_verifier()
        rng = np.random.default_rng(4)
        source = rng.integers(0, 256, (350, 450, 3), dtype=np.uint8)
        # Include recognizable features that survive Facebook resizing.
        for i in range(20):
            cv2.circle(source, (20 + i * 20, 40 + (i % 5) * 55), 12, (255, 255, 255), 3)
        verifier._source = source
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        screen[420:700, 700:1060] = cv2.resize(source, (360, 280))
        bounds = verifier._matching_image_bounds(screen)
        self.assertIsNotNone(bounds)
        self.assertAlmostEqual(bounds[0], 700, delta=8)
        self.assertAlmostEqual(bounds[1], 420, delta=8)
        self.assertIsNone(verifier._matching_image_bounds(np.zeros_like(screen)))


if __name__ == "__main__":
    unittest.main()
