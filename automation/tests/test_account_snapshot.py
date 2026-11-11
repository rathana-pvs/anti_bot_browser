import unittest
from unittest.mock import Mock, patch

import numpy as np

from skills.account_snapshot import capture, parse_followers, read_snapshot
from tasks.facebook_warming import FacebookWarmingTask

W, H = 1280, 800


def item(text, top, left=744, height=16):
    return {"text": text, "bounds": (left, top, left + 8 * len(text), top + height), "confidence": 0.99}


PAGE_HEADER = [item("News 429M", 196, height=32), item("1.2K likes • 3.4K followers", 236)]


class ParseFollowersTests(unittest.TestCase):
    def test_follower_formats(self):
        self.assertEqual(parse_followers("3K followers • 13 following"), 3000)
        self.assertEqual(parse_followers("1.2K likes • 3.4K followers"), 3400)
        self.assertEqual(parse_followers("12,345 followers"), 12345)
        self.assertEqual(parse_followers("1.5M followers"), 1_500_000)
        self.assertEqual(parse_followers("1 follower"), 1)
        self.assertEqual(parse_followers("950 followers"), 950)

    def test_no_follower_count(self):
        self.assertIsNone(parse_followers("500 friends"))
        self.assertIsNone(parse_followers("13 following"))
        self.assertIsNone(parse_followers(""))


class ReadSnapshotTests(unittest.TestCase):
    def test_a_page_header_gives_name_kind_and_followers(self):
        self.assertEqual(read_snapshot(PAGE_HEADER, W, H, "page"), {"name": "News 429M", "kind": "page", "followers": 3400})

    def test_a_personal_profile_with_friends_only_has_no_followers(self):
        snapshot = read_snapshot([item("Dara Sok", 196, height=32), item("500 friends", 236)], W, H, "personal")
        self.assertEqual(snapshot, {"name": "Dara Sok", "kind": "personal"})

    def test_text_that_is_not_the_header_gives_nothing(self):
        # A scrolled page: big text with no count line right under it.
        self.assertIsNone(read_snapshot([item("Breaking story today", 196, height=32), item("Read more", 300)], W, H, "page"))
        self.assertIsNone(read_snapshot([item("Manage Page", 196, height=32), item("3K followers", 236)], W, H, "page"))
        self.assertIsNone(read_snapshot([], W, H, None))

    def test_an_unknown_identity_is_left_out(self):
        self.assertIsNone(read_snapshot(PAGE_HEADER, W, H, "something")["kind"])


class CaptureTests(unittest.TestCase):
    def task(self):
        task = Mock()
        task.client.screenshot.return_value = np.zeros((H, W, 3), dtype=np.uint8)
        task.facebook_identity = "page"
        return task

    def test_reads_the_header_once(self):
        task = self.task()
        task.vision.read_text.return_value = PAGE_HEADER
        with patch("modules.reel_profile_readiness.browser_navigation_state", return_value="idle"):
            self.assertEqual(capture(task)["followers"], 3400)
        task.vision.read_text.assert_called_once()
        task.human.assert_not_called()

    def test_any_error_or_doubt_gives_none(self):
        task = self.task()
        task.vision.read_text.side_effect = RuntimeError("OCR worker down")
        with patch("modules.reel_profile_readiness.browser_navigation_state", return_value="idle"):
            self.assertIsNone(capture(task))
        with patch("modules.reel_profile_readiness.browser_navigation_state", return_value="loading"):
            self.assertIsNone(capture(self.task()))
        broken = self.task()
        broken.client.screenshot.side_effect = OSError("no display")
        self.assertIsNone(capture(broken))


class WarmingVisitTests(unittest.TestCase):
    def make_task(self, surface):
        task = FacebookWarmingTask.__new__(FacebookWarmingTask)
        task.profile_id = "profile_test"
        task.scroll_count = 1
        task.warming_options = {"surface": surface, "max_seconds": 60}
        task.client = Mock()
        task.client.is_running.return_value = True
        task.client.get_screen_dimensions.return_value = (W, H)
        task.human = Mock()
        task.verify_logged_in = Mock(return_value=True)
        task.session_check_status = "authenticated"
        task.select_and_open_warming_surface = Mock(return_value=(surface, surface, False, None))
        task.capture_evidence = Mock()
        task.log = Mock()
        task.set_stage = Mock()
        task.set_outcome = Mock(side_effect=lambda status, **_: status == "completed")
        return task

    def test_a_profile_visit_reads_the_header_and_still_completes_when_it_fails(self):
        task = self.make_task("profile")
        with patch("tasks.facebook_warming.capture_account_snapshot", side_effect=lambda t: None) as read, \
                patch("tasks.facebook_warming.time.sleep"):
            self.assertTrue(task.run())
        read.assert_called_once_with(task)
        self.assertEqual(task.set_outcome.call_args.args, ("completed",))

    def test_a_news_feed_visit_does_not_read_it(self):
        task = self.make_task("news_feed")
        with patch("tasks.facebook_warming.capture_account_snapshot") as read, \
                patch("tasks.facebook_warming.time.sleep"):
            self.assertTrue(task.run())
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
