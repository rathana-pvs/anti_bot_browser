"""Workflow steps: Brain-listed order, step records, and post-click safety."""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.brain_runtime import BrainRegistry
from modules.publishing_pipeline import build_publishing_pipeline
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask

AUTOMATION = Path(__file__).resolve().parents[1]


def step_task(cls=FacebookPostTask, **step_overrides):
    """A task whose steps are stubs; everything else is the real step runner."""
    task = cls.__new__(cls)
    task.profile_id = "profile_test"
    task.profile_config = {}
    task.behavior_session = None
    task.evidence = Mock(directory="job_test")
    task.telemetry = None
    task.client = Mock()
    task.client.is_running.return_value = True
    task.verify_logged_in = Mock(return_value=True)
    task.session_check_status = "authenticated"
    task.log = Mock()
    task.capture_evidence = Mock()
    task.stage_history = []
    task.current_stage = "pending"
    task.result_status = "running"
    task.result_error = None
    task.result_extra = {}
    task.preflight_complete = False
    task.defer_comment = False
    task.caption = "caption"
    task.media_path = None
    task.comment_link = None
    task.brain_workflow = None
    calls = []

    def stub(name):
        def run():
            calls.append(name)
            return None
        return run

    for name in cls.STEPS:
        setattr(task, f"_step_{name}", stub(name))
    task._step_finish = lambda: calls.append("finish") or task.set_outcome("published")
    for name, method in step_overrides.items():
        setattr(task, f"_step_{name}", method)
    task.calls = calls
    return task


class WorkflowStepTests(unittest.TestCase):
    def test_bundled_post_workflow_lists_the_engine_steps(self):
        package = BrainRegistry(AUTOMATION / "brains").resolve("facebook_post")
        task = step_task()
        task.brain_workflow = package.workflow
        self.assertEqual(task.workflow_steps(FacebookPostTask.STEPS), FacebookPostTask.STEPS)

    def test_bundled_reel_workflow_lists_the_engine_steps(self):
        package = BrainRegistry(AUTOMATION / "brains").resolve("facebook_reel")
        task = step_task(FacebookReelTask)
        task.brain_workflow = package.workflow
        self.assertEqual(task.workflow_steps(FacebookReelTask.STEPS), FacebookReelTask.STEPS)

    def test_reel_steps_run_in_order(self):
        task = step_task(FacebookReelTask)
        task._pipeline_defer_finalization = True
        self.assertTrue(task.run())
        self.assertEqual(task.calls, list(FacebookReelTask.STEPS))

    def test_older_workflow_without_steps_uses_the_built_in_order(self):
        task = step_task()
        task.brain_workflow = {
            "initial_state": "feed_ready",
            "states": {"feed_ready": {"on_success": "composer_open"}, "composer_open": {"on_success": "completed"}},
        }
        self.assertEqual(task.workflow_steps(FacebookPostTask.STEPS), FacebookPostTask.STEPS)

    def test_older_reel_workflow_sharing_a_step_name_uses_the_built_in_order(self):
        task = step_task(FacebookReelTask)
        task.brain_workflow = {
            "initial_state": "profile_ready",
            "states": {
                "profile_ready": {"on_success": "select_video"},
                "select_video": {"on_success": "composer_ready"},
                "composer_ready": {"on_success": "publish"},
                "publish": {"on_success": "verify_publication"},
                "verify_publication": {"on_success": "completed"},
            },
        }
        self.assertEqual(task.workflow_steps(FacebookReelTask.STEPS), FacebookReelTask.STEPS)

    def test_reordered_steps_stop_before_any_action(self):
        steps = list(FacebookPostTask.STEPS)
        steps.remove("enter_caption")
        steps.insert(steps.index("verify"), "enter_caption")
        states = {name: {"on_success": following} for name, following in zip(steps, steps[1:] + ["completed"])}
        task = step_task()
        task.brain_workflow = {"initial_state": steps[0], "states": states}
        task._pipeline_defer_finalization = True
        self.assertFalse(task.run())
        self.assertEqual(task.calls, [])
        self.assertEqual(task.result_status, "failed_before_publish")
        self.assertEqual(task.result_error, "workflow_steps_invalid")

    def test_steps_run_in_order_and_are_recorded(self):
        task = step_task()
        task._pipeline_defer_finalization = True
        self.assertTrue(task.run())
        self.assertEqual(task.calls, list(FacebookPostTask.STEPS))
        self.assertEqual([item["step"] for item in task.step_results], list(FacebookPostTask.STEPS))
        self.assertEqual(task.step_results[-1]["outcome"], "published")

    def test_a_stopping_step_ends_the_run_and_names_itself(self):
        task = step_task(enter_caption=lambda: task._fail("caption_input_not_found", "No caption field."))
        task._pipeline_defer_finalization = True
        self.assertFalse(task.run())
        self.assertEqual(task.calls, ["preflight", "open_composer", "attach_media"])
        self.assertEqual(task.step_results[-1], {**task.step_results[-1], "step": "enter_caption",
                                                 "outcome": "failed_before_publish"})

    def test_exception_after_the_publish_click_is_uncertain(self):
        def publish():
            task._publish_gate_consumed = True
            task.stage_history.append({"stage": "publish_clicked"})

        def verify():
            raise RuntimeError("screen capture failed")

        task = step_task(publish=publish, verify=verify)
        orchestrator, context = build_publishing_pipeline(task, content_type="text", comment_text=None)
        result = orchestrator.run(context)
        self.assertEqual(result.outcome, "uncertain")
        self.assertEqual(result.stopped_at, "publish")
        self.assertEqual(task.result_status, "uncertain")
        self.assertEqual(task.step_results[-1]["step"], "verify")
        self.assertEqual(task.step_results[-1]["outcome"], "exception")
        recorded = task.evidence.write_result.call_args
        self.assertEqual(recorded.args[0], "uncertain")
        self.assertEqual(recorded.kwargs["workflow_steps"][-1]["step"], "verify")


if __name__ == "__main__":
    unittest.main()
