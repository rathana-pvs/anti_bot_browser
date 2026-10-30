"""One status vocabulary, one writer, and the post-click rule."""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.outcome import PublicationStatus, resolve_status
from tasks.base_task import BaseTask

AUTOMATION = Path(__file__).resolve().parents[1]


def bare_task():
    task = BaseTask.__new__(BaseTask)
    task.stage_history = []
    task.current_stage = "composing"
    task.result_extra = {}
    task._pipeline_defer_finalization = True
    task.set_stage = Mock()
    return task


class OutcomeTests(unittest.TestCase):
    def test_failure_after_publish_click_becomes_uncertain(self):
        self.assertEqual(
            resolve_status("failed_before_publish", publish_clicked=True),
            PublicationStatus.UNCERTAIN,
        )
        self.assertEqual(
            resolve_status("failed_after_publish", publish_clicked=True),
            PublicationStatus.FAILED_AFTER_PUBLISH,
        )

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_status("posted", publish_clicked=False)

    def test_set_outcome_applies_the_rule_once_the_gate_fired(self):
        task = bare_task()
        task._publish_gate_consumed = True
        self.assertFalse(task.set_outcome("failed_before_publish", "late_error"))
        self.assertEqual(task.result_status, "uncertain")
        self.assertEqual(task.result_error, "late_error")

    def test_failure_before_publish_stays_a_failure(self):
        task = bare_task()
        task.set_outcome("failed_before_publish", "caption_input_not_found")
        self.assertEqual(task.result_status, "failed_before_publish")

    def test_update_outcome_keeps_earlier_details(self):
        task = bare_task()
        task.set_outcome("pending_profile_verification", None, reel_template="t3")
        task.update_outcome("published")
        self.assertEqual(task.result_status, "published")
        self.assertEqual(task.result_extra["reel_template"], "t3")

    def test_only_the_status_writer_assigns_result_status(self):
        writers = []
        for folder in ("engine", "modules", "skills", "tasks"):
            for path in (AUTOMATION / folder).rglob("*.py"):
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(r"\bresult_status\s*=[^=]", line):
                        writers.append(f"{path.relative_to(AUTOMATION)}:{number}")
        self.assertEqual(len(writers), 2, writers)
        self.assertTrue(all(item.startswith("tasks/base_task.py:") for item in writers), writers)


if __name__ == "__main__":
    unittest.main()
