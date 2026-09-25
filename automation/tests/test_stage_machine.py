"""Unit tests for Phase 0 execution state machine, failure classification, and atomic evidence stage recording."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.evidence import EvidenceRecorder
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask


class StageMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.evidence = EvidenceRecorder.__new__(EvidenceRecorder)
        self.evidence.run_id = "test_run_123"
        self.evidence.directory = self.tmp_dir
        self.evidence._sequence = 0

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_evidence_records_stage_atomically(self):
        history = [
            {"stage": "pending", "timestamp": "2026-09-25T00:00:00Z"},
            {"stage": "composing", "timestamp": "2026-09-25T00:00:05Z"},
        ]
        stage_path = self.evidence.record_stage("composing", history)

        self.assertTrue(os.path.exists(stage_path))
        with open(stage_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["current_stage"], "composing")
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["run_id"], "test_run_123")

    def test_post_task_fail_before_publish_click_is_failed_before_publish(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.evidence = self.evidence
        task.current_stage = "composing"
        task.stage_history = []
        task.logs = []
        task.profile_id = "test_prof"
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()

        success = task._fail("err_code", "Could not find image input")
        self.assertFalse(success)
        self.assertEqual(task.result_status, "failed_before_publish")
        self.assertEqual(task.current_stage, "failed_before_publish")

    def test_post_task_fail_after_publish_click_is_uncertain(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.evidence = self.evidence
        task.current_stage = "publish_clicked"
        task.stage_history = []
        task.logs = []
        task.profile_id = "test_prof"
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()

        success = task._fail("err_timeout", "Dialog did not disappear within timeout")
        self.assertFalse(success)
        self.assertEqual(task.result_status, "uncertain")
        self.assertEqual(task.current_stage, "uncertain")

    def test_post_task_fail_during_verifying_is_uncertain(self):
        task = FacebookPostTask.__new__(FacebookPostTask)
        task.evidence = self.evidence
        task.current_stage = "verifying"
        task.stage_history = []
        task.logs = []
        task.profile_id = "test_prof"
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()

        success = task._fail("err_verify", "Could not find post permalink on profile")
        self.assertFalse(success)
        self.assertEqual(task.result_status, "uncertain")
        self.assertEqual(task.current_stage, "uncertain")

    def test_reel_task_fail_before_and_after_publish(self):
        task = FacebookReelTask.__new__(FacebookReelTask)
        task.evidence = self.evidence
        task.stage_history = []
        task.logs = []
        task.profile_id = "test_prof"
        task.log = Mock()
        task.log_decision = Mock()
        task.capture_evidence = Mock()

        # Before publish click
        task.current_stage = "preparing"
        task._fail("err_prep", "Reel creation button not found")
        self.assertEqual(task.result_status, "failed_before_publish")
        self.assertEqual(task.current_stage, "failed_before_publish")

        # After publish click
        task.current_stage = "publish_clicked"
        task._fail("err_upload", "Timeout waiting for Reel processing")
        self.assertEqual(task.result_status, "uncertain")
        self.assertEqual(task.current_stage, "uncertain")


if __name__ == "__main__":
    unittest.main()
