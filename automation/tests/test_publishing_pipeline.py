import unittest
from types import SimpleNamespace

from modules.publishing_pipeline import build_publishing_pipeline


class FakePromptHandler:
    def __init__(self, status="absent"):
        self.last_status = "not_checked"
        self.status = status
        self.calls = 0

    def handle(self):
        self.calls += 1
        self.last_status = self.status
        return self.status


class FakeClient:
    container_name = "fake-container"

    def __init__(self, running=True):
        self.running = running

    def is_running(self):
        return self.running


class FakePublishTask:
    def __init__(self, *, prompt_status="absent", comment_status="submitted_verified"):
        self.profile_id = "profile_test"
        self.profile_config = {"behavior_mode": "medium"}
        self.behavior_session = object()
        self.evidence = SimpleNamespace(directory="job_test")
        self.client = FakeClient()
        self.post_publish_prompt = FakePromptHandler(prompt_status)
        self.comment_status = comment_status
        self.caption = "caption"
        self.media_path = "photo.jpg"
        self.result_status = "running"
        self.result_error = None
        self.result_extra = {}
        self.stage_history = []
        self.session_check_status = "not_checked"
        self.post_template_id = None
        self.finalize_calls = []
        self.verify_calls = 0
        self.run_calls = 0
        self.last_comment_method = None

    def verify_logged_in(self, target_url):
        self.verify_calls += 1
        self.session_check_status = "verified"
        return True

    def run(self):
        self.run_calls += 1
        assert self.preflight_complete
        assert self.defer_comment
        self.stage_history.append({"stage": "publish_clicked"})
        self.post_template_id = "p1"
        self.result_status = "published"
        self.result_extra = {"post_template": "p1", "first_comment": "deferred"}
        return True

    def post_first_comment_with_page_reuse(self, comment_text, *, caption, media_type):
        self.last_comment_method = "profile_first_post"
        return self.comment_status, self.permalink_not_requested()

    def handle_post_publish_prompt(self, screen=None):
        return self.post_publish_prompt.handle()

    def permalink_not_requested(self):
        return {
            "post_url": None,
            "post_url_verified_at": None,
            "post_match_confidence": None,
            "permalink_status": "not_requested",
        }

    def finalize_outcome(self, **extra):
        self.result_extra = {**self.result_extra, **extra}
        self.finalize_calls.append(extra)

    def _fail(self, code, message):
        self.result_status = "failed_before_publish"
        self.result_error = code
        return False

    def skip_unverified_session(self):
        self.result_status = "skipped_session_unverified"
        return False


class PublishingPipelineTests(unittest.TestCase):
    def test_successful_publish_runs_startup_once_and_skips_empty_comment(self):
        task = FakePublishTask()
        orchestrator, context = build_publishing_pipeline(
            task,
            content_type="image",
            comment_text=None,
        )

        result = orchestrator.run(context)

        self.assertEqual(result.outcome, "success")
        self.assertEqual(task.verify_calls, 1)
        self.assertEqual(task.run_calls, 1)
        self.assertEqual(result.module_results["warming"].outcome, "skipped")
        self.assertEqual(result.module_results["comment"].outcome, "skipped")
        self.assertEqual(task.result_status, "published")
        self.assertEqual(task.result_extra["first_comment"], "not_requested")
        self.assertEqual(len(task.finalize_calls), 1)

    def test_comment_failure_does_not_replace_confirmed_publication(self):
        task = FakePublishTask(comment_status="failed_input_not_found")
        orchestrator, context = build_publishing_pipeline(
            task,
            content_type="image",
            comment_text="https://example.test",
        )

        result = orchestrator.run(context)

        self.assertEqual(result.outcome, "failed_safe")
        self.assertEqual(result.stopped_at, "comment")
        self.assertEqual(task.result_status, "published")
        self.assertEqual(task.result_extra["first_comment"], "failed_input_not_found")
        self.assertEqual(len(task.finalize_calls), 1)

    def test_prompt_failure_stops_comment_but_preserves_publication(self):
        task = FakePublishTask(prompt_status="failed")
        orchestrator, context = build_publishing_pipeline(
            task,
            content_type="image",
            comment_text="https://example.test",
        )

        result = orchestrator.run(context)

        self.assertEqual(result.outcome, "uncertain")
        self.assertEqual(result.stopped_at, "post_publish_prompt")
        self.assertEqual(result.module_results["comment"].outcome, "skipped")
        self.assertEqual(task.result_status, "published")
        self.assertEqual(task.result_extra["first_comment"], "not_attempted")
        self.assertEqual(len(task.finalize_calls), 1)


if __name__ == "__main__":
    unittest.main()
