"""Auto warming visits: varied read-only activities that stop at safe points."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.behavior_profile import BehaviorSession
from engine.resource_signal import read_yield
from skills.active_visit import ActiveVisit

NEUTRAL = {"hold": False, "elastic_scale": 1.0, "publish_slots_full": False, "reason": ""}


def fast_clock():
    """Sleeping advances a fake clock, so waits take no real time."""
    clock = [0.0]

    def sleep(seconds):
        clock[0] += max(0.1, seconds)
    return (patch("skills.active_visit.time.sleep", sleep), patch("skills.active_visit.time.monotonic", lambda: clock[0]))


def task(signal=None, texts=()):
    session = BehaviorSession("medium", 77, "visit")
    host = SimpleNamespace(profile_id="profile_test", log=Mock(), behavior_session=session, human=Mock(),
                           client=Mock(), vision=Mock(), navigate_to=Mock(), click_reversible=Mock(),
                           current_url=lambda: "https://www.facebook.com/")
    host.rng = lambda channel: session.stream(channel)
    host.resource_signal = lambda: signal or dict(NEUTRAL)
    host.client.screenshot.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    host.vision.read_text.return_value = [{"text": text} for text in texts]
    return host


class PlanTests(unittest.TestCase):
    def test_a_visit_starts_where_the_browser_is_and_varies(self):
        chains = set()
        for run in range(20):
            host = task()
            host.behavior_session = BehaviorSession("medium", 77, f"visit-{run}")
            host.rng = lambda channel, s=host.behavior_session: s.stream(channel)
            with patch("skills.active_visit.read_yield", return_value=None):
                chain = ActiveVisit(host, 120).plan("profile" if run % 2 else "news_feed")
            self.assertEqual(chain[0], "own_page" if run % 2 else "feed")
            self.assertTrue(2 <= len([a for a in chain if a != "idle"]) <= 4)
            chains.add(tuple(chain))
        self.assertGreater(len(chains), 5)

    def test_a_busy_machine_shortens_the_visit(self):
        with patch("skills.active_visit.time.monotonic", return_value=100.0):
            calm = ActiveVisit(task(), 200)
            busy = ActiveVisit(task(dict(NEUTRAL, elastic_scale=0.5)), 200)
        self.assertEqual(calm.deadline, 300.0)
        self.assertEqual(busy.deadline, 200.0)


class SafePointTests(unittest.TestCase):
    def test_a_yield_request_stops_the_visit(self):
        visit = ActiveVisit(task(), 120)
        with patch("skills.active_visit.read_yield", return_value="own_post"):
            self.assertTrue(visit.should_stop())
        self.assertEqual(visit.stop_reason, "yield:own_post")

    def test_machine_pressure_stops_the_visit(self):
        host = task()
        visit = ActiveVisit(host, 120)
        host.resource_signal = lambda: dict(NEUTRAL, hold=True)
        with patch("skills.active_visit.read_yield", return_value=None):
            self.assertTrue(visit.should_stop())
        self.assertEqual(visit.stop_reason, "machine_pressure")

    def test_old_or_stale_yield_requests_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yield.json"
            path.write_text(json.dumps({"profiles": {"profile_test": {"reason": "own_post", "at": 1000.0}}}))
            self.assertEqual(read_yield("profile_test", 990.0, path, now=lambda: 1010.0), "own_post")
            self.assertIsNone(read_yield("profile_test", 1005.0, path, now=lambda: 1010.0))
            self.assertIsNone(read_yield("profile_test", 990.0, path, now=lambda: 5000.0))
            self.assertIsNone(read_yield("other", 990.0, path, now=lambda: 1010.0))


class ActivityTests(unittest.TestCase):
    def test_notifications_open_read_and_close(self):
        host = task(texts=["Notifications"])
        sleep, monotonic = fast_clock()
        with patch("skills.active_visit.read_yield", return_value=None), sleep, monotonic:
            ActiveVisit(host, 120)._notifications()
        host.human.click.assert_called_once_with(1821, 114)
        self.assertEqual(host.human.key_press.call_args.args, ("Escape",))

    def test_a_click_that_opens_nothing_is_closed_right_away(self):
        host = task(texts=["Something else"])
        sleep, monotonic = fast_clock()
        with patch("skills.active_visit.read_yield", return_value=None), sleep, monotonic:
            ActiveVisit(host, 120)._notifications()
        host.human.scroll.assert_not_called()
        host.human.key_press.assert_called_once_with("Escape")

    def test_reels_tab_is_skipped_when_not_found(self):
        host = task(texts=["Posts", "About"])
        with patch("skills.active_visit.read_yield", return_value=None):
            ActiveVisit(host, 120)._reels()
        host.click_reversible.assert_not_called()

    def test_nothing_but_navigation_scrolls_and_panel_clicks_happens(self):
        host = task(texts=["Notifications", "Reels"])
        host.vision.similarity.return_value = 0.5
        host.client.get_mouse_position.return_value = (900, 600)
        sleep, monotonic = fast_clock()
        with patch("skills.active_visit.read_yield", return_value=None), sleep, monotonic:
            visit = ActiveVisit(host, 30)
            result = visit.run("news_feed")
        self.assertIn(result["stop_reason"], ("finished", "time_up"))
        host.human.type_text.assert_not_called()
        for call in host.human.click.call_args_list:
            self.assertEqual(call.args, (1821, 114))  # only the notifications bell


if __name__ == "__main__":
    unittest.main()
