"""Reel Studio that loads slowly, and failure evidence that shows the judged frame."""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.screen_state import ScreenState, StateObservation
from tasks.base_task import BaseTask
from tasks.facebook_reel import FacebookReelTask

PROFILE = np.full((180, 320, 3), 240, dtype=np.uint8)
# A dialog over the profile dims the page: far from the pre-click frame.
DIALOG = np.full((180, 320, 3), 120, dtype=np.uint8)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ReelEntryLoadingTests(unittest.TestCase):
    def make_task(self, screen, pre_click=PROFILE, ready_at=None):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.client = Mock()
        task.client.screenshot.return_value = screen
        task.client.last_frame = None
        task.recognizer = Mock()
        task.recognizer.observe_safety_gate.return_value = StateObservation(ScreenState.UNKNOWN, 0.0)
        task.recognizer.observe.return_value = StateObservation(ScreenState.UNKNOWN, 0.0)
        task.capture_evidence = Mock()
        task.telemetry = Mock()
        task.vision = Mock()
        task.log = Mock()
        task.find_visible_file_chooser = Mock(return_value=None)
        task._reel_pre_click_frame = pre_click
        self.clock = FakeClock()
        start = self.clock.now

        def finder():
            if ready_at is not None and self.clock.now - start >= ready_at:
                return (290, 981)
            return None

        task._find_reel_upload_target = Mock(side_effect=finder)
        return task

    def wait(self, task):
        with patch("tasks.facebook_reel.time.time", self.clock.time), \
                patch("tasks.facebook_reel.time.sleep", self.clock.sleep):
            result = task._wait_for_reel_entry(timeout=35.0)
        return result, self.clock.now - 1000.0

    def test_slow_dialog_is_waited_for_past_the_normal_limit(self):
        task = self.make_task(DIALOG, ready_at=50)
        (status, target, _), waited = self.wait(task)
        self.assertEqual(status, "reel_studio")
        self.assertEqual(target, (290, 981))
        self.assertGreaterEqual(waited, 50)
        self.assertTrue(any("still loading" in str(call) for call in task.log.call_args_list))

    def test_dialog_that_never_loads_stops_at_the_loading_limit(self):
        task = self.make_task(DIALOG)
        (status, target, screen), waited = self.wait(task)
        self.assertEqual(status, "loading")
        self.assertIsNone(target)
        self.assertIs(screen, DIALOG)
        self.assertGreaterEqual(waited, task.REEL_ENTRY_LOADING_TIMEOUT)
        self.assertLess(waited, task.REEL_ENTRY_LOADING_TIMEOUT + 5)

    def test_click_that_opened_nothing_still_stops_at_35_seconds(self):
        task = self.make_task(PROFILE)
        (status, _, screen), waited = self.wait(task)
        self.assertEqual(status, "timeout")
        self.assertIs(screen, PROFILE)
        self.assertLess(waited, 40)

    def test_without_a_pre_click_frame_the_old_limit_holds(self):
        task = self.make_task(DIALOG, pre_click=None)
        (status, _, _), waited = self.wait(task)
        self.assertEqual(status, "timeout")
        self.assertLess(waited, 40)

    def test_success_evidence_is_the_frame_the_finder_judged(self):
        task = self.make_task(DIALOG, ready_at=0)
        loaded = np.full((180, 320, 3), 60, dtype=np.uint8)
        task.client.last_frame = (loaded, time.monotonic())
        (status, _, screen), _ = self.wait(task)
        self.assertEqual(status, "reel_studio")
        self.assertIs(screen, loaded)
        self.assertIs(task.capture_evidence.call_args.args[1], loaded)

    def test_detect_entry_reports_still_loading(self):
        task = self.make_task(DIALOG)
        task.set_stage = Mock()
        task._wait_for_reel_entry = Mock(return_value=("loading", None, DIALOG))
        task._fail = Mock(return_value=False)
        task._step_detect_entry()
        self.assertEqual(task._fail.call_args.args[0], "reel_studio_still_loading")


class JudgedFrameEvidenceTests(unittest.TestCase):
    def make_task(self, judged, now, age=1.0):
        task = BaseTask.__new__(BaseTask)
        task.client = Mock()
        task.client.last_frame = (judged, time.monotonic() - age) if judged is not None else None
        task.client.screenshot.return_value = now
        task.evidence = Mock()
        task.evidence.capture.side_effect = lambda label, image, meta: f"{label}.png"
        task.log = Mock()
        return task

    def labels(self, task):
        return [(call.args[0], call.args[1]) for call in task.evidence.capture.call_args_list]

    def test_failure_saves_the_judged_frame_and_the_changed_screen_after(self):
        task = self.make_task(DIALOG, PROFILE)
        task.capture_evidence("reel_entry_flow_not_recognized", error_code="x", message="m")
        saved = self.labels(task)
        self.assertEqual(saved[0][0], "reel_entry_flow_not_recognized")
        self.assertIs(saved[0][1], DIALOG)
        self.assertEqual(saved[1][0], "reel_entry_flow_not_recognized_after")
        self.assertIs(saved[1][1], PROFILE)
        self.assertEqual(task.evidence.capture.call_args_list[0].args[2]["frame"], "judged")

    def test_unchanged_screen_saves_no_after_frame(self):
        task = self.make_task(DIALOG, DIALOG.copy())
        task.capture_evidence("fail", error_code="x")
        self.assertEqual([label for label, _ in self.labels(task)], ["fail"])

    def test_given_screen_is_kept(self):
        task = self.make_task(DIALOG, PROFILE)
        given = np.zeros((180, 320, 3), dtype=np.uint8)
        task.capture_evidence("fail", given, error_code="x")
        self.assertEqual(self.labels(task), [("fail", given)])

    def test_progress_evidence_still_takes_a_fresh_screenshot(self):
        task = self.make_task(DIALOG, PROFILE)
        task.capture_evidence("before_click_reel_button", target=[1, 2])
        self.assertEqual(self.labels(task), [("before_click_reel_button", PROFILE)])

    def test_stale_or_missing_judged_frame_falls_back_to_a_fresh_screenshot(self):
        for judged, age in ((DIALOG, BaseTask.JUDGED_FRAME_MAX_AGE_S + 5), (None, 0)):
            task = self.make_task(judged, PROFILE, age=age)
            task.capture_evidence("fail", error_code="x")
            self.assertEqual(self.labels(task), [("fail", PROFILE)])

    def test_mock_client_without_a_frame_is_handled(self):
        task = self.make_task(None, PROFILE)
        task.client = Mock()
        task.client.screenshot.return_value = PROFILE
        task.capture_evidence("fail", error_code="x")
        self.assertEqual(self.labels(task), [("fail", PROFILE)])


if __name__ == "__main__":
    unittest.main()
