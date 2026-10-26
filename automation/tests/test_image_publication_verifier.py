import unittest
from pathlib import Path
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

    @staticmethod
    def card_items(timestamp="Published by Editor · Just now", caption="Expected caption"):
        def item(text, x, y):
            return {"text": text, "confidence": .95, "bounds": (x, y, x + 150, y + 20)}
        return [item("Posts", 1000, 500), item("Manage posts", 1500, 500),
                item("List view", 1100, 550), item("Author Name", 1060, 600),
                item(timestamp, 1060, 625), item(caption, 1060, 660)]

    def test_recent_header_and_caption_work_without_image_match(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Expected caption"
        verifier.task.vision.read_text.return_value = self.card_items()
        verifier._matching_image_bounds = Mock(return_value=None)
        self.assertTrue(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        verifier._matching_image_bounds.assert_not_called()
        self.assertEqual(verifier.last_check["timestamp_reads"], ["Published by Editor · Just now"])
        self.assertTrue(verifier.last_check["caption_matched"])

    def test_downscaled_ocr_miss_is_recovered_by_native_header_read(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Expected caption"
        full_screen = self.card_items(timestamp="Publishen")
        verifier.task.vision.read_text.side_effect = [full_screen, self.card_items(timestamp="Justnow")]
        verifier._matching_image_bounds = Mock(return_value=None)
        self.assertTrue(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        calls = verifier.task.vision.read_text.call_args_list
        self.assertIsNotNone(calls[1].kwargs.get("region"))
        self.assertEqual(verifier.last_check["timestamp_reads"], ["Justnow"])

    def test_timestamp_without_author_still_requires_matching_photo_content(self):
        for stamp in ("1m", "30s", "30 seconds ago"):
            with self.subTest(timestamp=stamp):
                verifier = self.make_verifier()
                verifier.task.caption = "Expected caption"
                items = self.card_items(timestamp=stamp)
                items = [i for i in items if i["text"] != "Author Name"]
                verifier.task.vision.read_text.return_value = items
                verifier._matching_image_bounds = Mock(return_value=None)
                screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
                self.assertTrue(verifier._post_visible(screen))
                verifier.task.caption = "Unrelated words"
                self.assertFalse(verifier._post_visible(screen))

    def test_native_timestamp_at_real_world_confidence_is_accepted(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Expected caption"
        full_screen = self.card_items(timestamp="Publishen")
        native = self.card_items(timestamp="Just now")
        native[4]["confidence"] = .540775
        # Timestamp is a separate box to the right of the attribution.
        native[4]["bounds"] = (1300, 625, 1362, 643)
        native.append({"text": "Published by Editor", "confidence": .78,
                       "bounds": (1060, 625, 1260, 645)})
        verifier.task.vision.read_text.side_effect = [full_screen, native]
        verifier._matching_image_bounds = Mock(return_value=None)
        self.assertTrue(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))

    def test_recent_header_and_image_work_without_caption_match(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Different words"
        verifier.task.vision.read_text.return_value = self.card_items()
        verifier._matching_image_bounds = Mock(return_value=(1050, 700, 1650, 950))
        self.assertTrue(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        self.assertTrue(verifier.last_check["image_matched"])
        self.assertFalse(verifier.last_check["caption_matched"])

    def test_recent_timestamp_alone_is_not_enough(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Different words"
        verifier.task.vision.read_text.return_value = self.card_items()
        verifier._matching_image_bounds = Mock(return_value=None)
        self.assertFalse(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        self.assertEqual(verifier.last_check["reason"], "first_post_content_not_matched")

    def test_feed_video_frame_can_identify_media_without_a_caption(self):
        verifier = self.make_verifier()
        verifier.task.media_path = "/data/shared_media/reel_test.mp4"
        source = np.random.default_rng(4).integers(0, 256, (350, 450, 3), dtype=np.uint8)
        screen = np.zeros((1080, 1920, 3), dtype=np.uint8)
        screen[420:770, 700:1150] = source
        video = Mock()
        video.read.return_value = (True, source)
        with patch("modules.publication_result_verifier.profiles_dir", return_value=Path('/runtime/profiles')), \
                patch("cv2.VideoCapture", return_value=video) as capture:
            self.assertIsNotNone(verifier._matching_image_bounds(screen))
        capture.assert_called_once_with('/runtime/profiles/shared_media/reel_test.mp4')
        video.set.assert_called_once_with(cv2.CAP_PROP_POS_MSEC, 4000)
        video.release.assert_called_once()

    def test_short_video_uses_first_frame_and_unreadable_video_fails_closed(self):
        for readable in (True, False):
            with self.subTest(readable=readable):
                verifier = self.make_verifier()
                verifier.task.media_path = 'short.mp4'
                video = Mock()
                frame = np.zeros((100, 100, 3), dtype=np.uint8)
                video.read.side_effect = [(False, None), (readable, frame if readable else None)]
                with patch('cv2.VideoCapture', return_value=video):
                    self.assertIsNone(verifier._matching_image_bounds(np.zeros((1080, 1920, 3), dtype=np.uint8)))
                self.assertEqual(video.set.call_args.args, (cv2.CAP_PROP_POS_MSEC, 0))
                video.release.assert_called_once()
                if readable:
                    self.assertIs(verifier._source, frame)
                else:
                    self.assertIsNone(verifier._source)

    def test_old_header_cannot_borrow_new_timestamp_from_later_post(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Expected caption"
        items = self.card_items(timestamp="2 days ago")
        items += [{"text": "Just now", "confidence": .95, "bounds": (1060, 925, 1160, 945)}]
        verifier.task.vision.read_text.return_value = items
        verifier._matching_image_bounds = Mock(return_value=(1050, 700, 1650, 950))
        self.assertFalse(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        verifier._matching_image_bounds.assert_not_called()

    def test_sidebar_caption_and_later_post_caption_are_excluded(self):
        verifier = self.make_verifier()
        verifier.task.caption = "Different words"
        items = self.card_items()
        items += [{"text": "Different words", "confidence": .95, "bounds": (600, 670, 800, 690)},
                  {"text": "See insights and ads", "confidence": .95, "bounds": (1060, 800, 1250, 820)},
                  {"text": "Different words", "confidence": .95, "bounds": (1060, 900, 1250, 920)}]
        verifier.task.vision.read_text.return_value = items
        verifier._matching_image_bounds = Mock(return_value=None)
        self.assertFalse(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))

    def test_scan_stops_at_unmatched_first_post(self):
        verifier = self.make_verifier()
        verifier.task.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
        verifier.task.vision.read_text.return_value = self.card_items(timestamp="2 days ago")
        with patch("modules.publication_result_verifier.time.sleep"):
            self.assertFalse(verifier._scan()[0])
        verifier.task.human.scroll.assert_not_called()

    def test_unreadable_timestamp_keeps_raw_header_in_diagnostics(self):
        verifier = self.make_verifier()
        verifier.task.vision.read_text.return_value = self.card_items(timestamp="Jus? n0vv")
        self.assertFalse(verifier._post_visible(np.zeros((1080, 1920, 3), dtype=np.uint8)))
        self.assertIn("Jus? n0vv", verifier.last_check["header_reads"])
        self.assertEqual(verifier.last_check["reason"], "top_timestamp_unreadable")

    def test_unconfirmed_submission_is_uncertain(self):
        from tasks.facebook_post import FacebookPostTask
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.log = Mock()
        task.set_outcome = Mock(return_value=False)
        self.assertFalse(task._image_post_not_found())
        args, kwargs = task.set_outcome.call_args
        self.assertEqual(args, ("uncertain", "image_publication_unconfirmed"))
        self.assertIn("Comment skipped", kwargs["message"])

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
