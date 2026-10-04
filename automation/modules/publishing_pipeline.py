"""Concrete fixed-pipeline modules for Post and Reel publication jobs."""

from __future__ import annotations

from dataclasses import dataclass

from engine.automation_context import AutomationContext
from engine.fixed_orchestrator import FixedAutomationOrchestrator
from engine.module_contract import (
    FAILED_SAFE,
    NEEDS_REVIEW,
    UNCERTAIN,
    ModuleResult,
)

FLOW_VERSION = "1.0.0"


def _task_result(task, *, success_reason: str) -> ModuleResult:
    status = getattr(task, "result_status", "failed_before_publish")
    outputs = {"publication_status": status}
    if getattr(task, "post_template_id", None):
        outputs["post_template"] = task.post_template_id
    if getattr(task, "reel_template_id", None):
        outputs["reel_template"] = task.reel_template_id
        outputs["reel_template_selection"] = task.reel_template_selection
    if getattr(task, "template_selection_details", None):
        outputs["template_selection_details"] = task.template_selection_details
    if status in {"published", "completed"}:
        return ModuleResult.success(success_reason, **outputs)
    if status == "uncertain":
        return ModuleResult(UNCERTAIN, task.result_error or status, outputs)
    if status == "needs_review":
        return ModuleResult(NEEDS_REVIEW, task.result_error or status, outputs)
    return ModuleResult(FAILED_SAFE, task.result_error or status, outputs)


@dataclass
class StartupModule:
    task: object
    module_id: str = "startup"

    def enabled(self, context):
        return True

    def run(self, context):
        if not self.task.client.is_running():
            self.task._fail(
                "container_stopped",
                f"Container {self.task.client.container_name} is not running.",
            )
            return _task_result(self.task, success_reason="startup_complete")
        if not self.task.verify_logged_in(target_url="https://www.facebook.com/me"):
            self.task.skip_unverified_session()
            return _task_result(self.task, success_reason="startup_complete")
        self.task.preflight_complete = True
        return ModuleResult.success(
            "container_and_session_verified",
            auth_preflight=self.task.session_check_status,
            flow_version=FLOW_VERSION,
        )


@dataclass
class WarmingModule:
    task: object
    module_id: str = "warming"

    def enabled(self, context):
        return bool(context.inputs.get("warming_enabled", False))

    def run(self, context):
        # Publish jobs currently receive warming as a separate scheduled task.
        # This hook is intentionally bounded and disabled until recipe settings
        # opt into inline warming.
        return ModuleResult.skipped("inline_warming_not_configured")


@dataclass
class PublishModule:
    task: object
    module_id: str = "publish"

    def enabled(self, context):
        return True

    def run(self, context):
        self.task.defer_comment = True
        self.task.run()
        context.publish_attempted = any(
            entry.get("stage") == "publish_clicked"
            for entry in getattr(self.task, "stage_history", [])
        )
        status = getattr(self.task, "result_status", "failed_before_publish")
        if status in {"published", "completed", "pending_profile_verification"}:
            outputs = {"publish_phase_status": status}
            if getattr(self.task, "post_template_id", None):
                outputs["post_template"] = self.task.post_template_id
            if getattr(self.task, "reel_template_id", None):
                outputs["reel_template"] = self.task.reel_template_id
                outputs["reel_template_selection"] = self.task.reel_template_selection
            return ModuleResult.success("publish_action_completed", **outputs)
        return _task_result(self.task, success_reason="publish_action_completed")


@dataclass
class PostPublishPromptModule:
    task: object
    module_id: str = "post_publish_prompt"

    def enabled(self, context):
        return context.outputs.get("publish_phase_status") in {
            "published",
            "completed",
            "pending_profile_verification",
        }

    def run(self, context):
        handler = getattr(self.task, "post_publish_prompt", None)
        status = getattr(handler, "last_status", "not_checked")
        if status not in {"dismissed", "failed"}:
            status = self.task.handle_post_publish_prompt()
        if status == "failed":
            self.task.result_status = "failed_after_publish"
            self.task.result_error = "known_post_publish_prompt_did_not_close"
            return ModuleResult(
                FAILED_SAFE,
                "known_post_publish_prompt_did_not_close",
                {"post_publish_prompt": status},
            )
        return ModuleResult.success(
            f"optional_prompt_{status}",
            post_publish_prompt=status,
        )


@dataclass
class PublicationResultVerifierModule:
    task: object
    content_type: str
    module_id: str = "publication_result_verifier"

    def enabled(self, context):
        return context.publish_attempted

    def run(self, context):
        status = getattr(self.task, "result_status", "failed_before_publish")
        if self.content_type != "reel" and context.inputs.get("comment_text") and getattr(self.task, "media_path", None) and status in {"published", "pending_profile_verification"}:
            if not self.task._verify_latest_image_post():
                self.task._image_post_not_found()
                return _task_result(self.task, success_reason="image_publication_verified")
            self.task.result_status = "published"
            self.task.result_error = None
            return ModuleResult.success("latest_image_confirmed_on_profile", publication_status="published")
        if status in {"published", "completed"}:
            return ModuleResult.success(
                "publication_already_confirmed",
                publication_status=status,
            )
        if status == "pending_profile_verification" and self.content_type == "reel":
            if self.task._refresh_until_latest_reel_visible():
                self.task._latest_reel_confirmed_on_profile = True
                self.task.result_status = "published"
                self.task.result_error = None
                return ModuleResult.success(
                    "latest_reel_confirmed_on_profile",
                    publication_status="published",
                )
            self.task._failed_after_publish(
                "reel_publish_not_found_after_verification",
                "The success popup was absent and the latest Reel was not found after two profile checks separated by 15 seconds.",
            )
            return ModuleResult(
                FAILED_SAFE,
                "reel_publish_not_found_after_verification",
                {"publication_status": "failed_after_publish"},
            )
        return _task_result(self.task, success_reason="publication_confirmed")


@dataclass
class CommentModule:
    task: object
    content_type: str
    module_id: str = "comment"

    def enabled(self, context):
        return bool(context.inputs.get("comment_text"))

    def run(self, context):
        comment_text = context.inputs["comment_text"]
        if self.content_type == "reel":
            status, permalink = self.task.post_reel_first_comment_after_refresh(comment_text)
        else:
            status, permalink = self.task.post_first_comment_with_page_reuse(
                comment_text,
                caption=self.task.caption,
                media_type="photo" if self.task.media_path else "post",
            )
        outputs = {
            "first_comment": status,
            "first_comment_method": getattr(self.task, "last_comment_method", None),
            **permalink,
        }
        if status == "submitted_verified":
            return ModuleResult.success("comment_submitted_and_verified", **outputs)
        if status in {"submitted_unverified", "submission_pending"}:
            return ModuleResult(UNCERTAIN, status, outputs)
        return ModuleResult(FAILED_SAFE, status, outputs)


@dataclass
class FinalizeModule:
    task: object
    module_id: str = "finalize"

    def enabled(self, context):
        return True

    def run(self, context):
        # Comment status is independent from publication status. The task keeps
        # "published" even if comment submission fails or is uncertain.
        if self.task.result_status == "running":
            stopping_result = next(
                (
                    result
                    for result in context.module_results.values()
                    if result.should_stop
                ),
                None,
            )
            if stopping_result is not None:
                self.task.result_status = {
                    FAILED_SAFE: "failed_before_publish",
                    UNCERTAIN: "uncertain",
                    NEEDS_REVIEW: "needs_review",
                }[stopping_result.outcome]
                self.task.result_error = stopping_result.reason
        final_outputs = dict(context.outputs)
        if "first_comment" not in final_outputs:
            final_outputs["first_comment"] = (
                "not_attempted"
                if context.inputs.get("comment_text")
                else "not_requested"
            )
            final_outputs.update(self.task.permalink_not_requested())
        module_summary = {
            key: {"outcome": value.outcome, "reason": value.reason}
            for key, value in context.module_results.items()
        }
        self.task.finalize_outcome(
            **final_outputs,
            pipeline_modules=module_summary,
            module_durations_ms=dict(
                context.environment.get("module_durations_ms", {})
            ),
        )
        return ModuleResult.success("outcome_persisted")


def build_publishing_pipeline(task, *, content_type: str, comment_text: str | None):
    """Build the shared fixed flow around one already-created browser task."""
    task._pipeline_defer_finalization = True
    context = AutomationContext.create(
        job_id=getattr(task.evidence, "directory", task.profile_id),
        profile_id=task.profile_id,
        inputs={
            "content_type": content_type,
            "comment_text": comment_text,
            "warming_enabled": False,
        },
        profile=getattr(task, "profile_config", {}),
        behavior=getattr(task, "behavior_session", None),
    )
    modules = {
        "startup": StartupModule(task),
        "warming": WarmingModule(task),
        "publish": PublishModule(task),
        "post_publish_prompt": PostPublishPromptModule(task),
        "publication_result_verifier": PublicationResultVerifierModule(task, content_type),
        "comment": CommentModule(task, content_type),
        "finalize": FinalizeModule(task),
    }
    return FixedAutomationOrchestrator(modules), context
