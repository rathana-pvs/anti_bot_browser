import unittest

from engine.automation_context import AutomationContext
from engine.fixed_orchestrator import FixedAutomationOrchestrator, PIPELINE_ORDER
from engine.module_contract import FAILED_SAFE, ModuleResult, SKIPPED, UNCERTAIN


class StubModule:
    def __init__(self, module_id, calls, result=None, enabled=True, error=None):
        self.module_id = module_id
        self.calls = calls
        self.result = result or ModuleResult.success(**{f"{module_id}_completed": True})
        self.is_enabled = enabled
        self.error = error

    def enabled(self, context):
        return self.is_enabled

    def run(self, context):
        self.calls.append(self.module_id)
        if self.error:
            raise self.error
        return self.result


def make_context():
    return AutomationContext.create(
        job_id="job_test",
        profile_id="profile_test",
        inputs={"content_type": "image"},
    )


def make_modules(calls, overrides=None):
    overrides = overrides or {}
    return {
        module_id: overrides.get(module_id, StubModule(module_id, calls))
        for module_id in PIPELINE_ORDER
    }


class FixedAutomationOrchestratorTests(unittest.TestCase):
    def test_success_runs_the_fixed_order(self):
        calls = []
        context = make_context()
        result = FixedAutomationOrchestrator(make_modules(calls)).run(context)

        self.assertEqual(calls, list(PIPELINE_ORDER))
        self.assertEqual(result.outcome, "success")
        self.assertIsNone(result.stopped_at)
        self.assertEqual(list(result.module_results), list(PIPELINE_ORDER))

    def test_disabled_optional_module_is_recorded_and_pipeline_continues(self):
        calls = []
        warming = StubModule("warming", calls, enabled=False)
        result = FixedAutomationOrchestrator(
            make_modules(calls, {"warming": warming})
        ).run(make_context())

        self.assertNotIn("warming", calls)
        self.assertEqual(result.module_results["warming"].outcome, SKIPPED)
        self.assertIn("publish", calls)
        self.assertEqual(calls[-1], "finalize")

    def test_failure_skips_browser_modules_but_always_finalizes(self):
        calls = []
        publish = StubModule(
            "publish",
            calls,
            result=ModuleResult(FAILED_SAFE, "composer_not_found"),
        )
        result = FixedAutomationOrchestrator(
            make_modules(calls, {"publish": publish})
        ).run(make_context())

        self.assertEqual(calls, ["startup", "warming", "publish", "finalize"])
        self.assertEqual(result.outcome, FAILED_SAFE)
        self.assertEqual(result.stopped_at, "publish")
        self.assertEqual(result.module_results["comment"].outcome, SKIPPED)
        self.assertEqual(result.finalization_result.outcome, "success")

    def test_exception_after_publish_attempt_is_uncertain(self):
        calls = []

        class FailingPublish(StubModule):
            def run(self, context):
                self.calls.append(self.module_id)
                context.publish_attempted = True
                raise RuntimeError("actuator result unknown")

        publish = FailingPublish("publish", calls)
        result = FixedAutomationOrchestrator(
            make_modules(calls, {"publish": publish})
        ).run(make_context())

        self.assertEqual(result.outcome, UNCERTAIN)
        self.assertEqual(result.stopped_at, "publish")
        self.assertEqual(calls[-1], "finalize")

    def test_finalize_error_does_not_replace_original_failure(self):
        calls = []
        publish = StubModule(
            "publish",
            calls,
            result=ModuleResult(FAILED_SAFE, "upload_failed"),
        )
        finalize = StubModule(
            "finalize",
            calls,
            error=RuntimeError("persistence unavailable"),
        )
        result = FixedAutomationOrchestrator(
            make_modules(calls, {"publish": publish, "finalize": finalize})
        ).run(make_context())

        self.assertEqual(result.outcome, FAILED_SAFE)
        self.assertEqual(result.stopped_at, "publish")
        self.assertEqual(result.finalization_result.outcome, "needs_review")

    def test_context_rejects_conflicting_output_ownership(self):
        context = make_context()
        context.record_result("startup", ModuleResult.success(theme="dark"))
        with self.assertRaisesRegex(ValueError, "replace output"):
            context.record_result("warming", ModuleResult.success(theme="light"))


if __name__ == "__main__":
    unittest.main()
