"""Standalone first-post comment task for safe workflow testing."""

import time

from .facebook_post import FacebookPostTask


class FacebookCommentTask(FacebookPostTask):
    def __init__(self, profile_id: str, comment_text: str):
        super().__init__(
            profile_id=profile_id,
            caption="",
            comment_link=comment_text,
            media_path=None,
        )
        self.comment_text = comment_text

    def run(self) -> bool:
        self.log("STEP", "Starting standalone first-post comment task...")
        if not self.comment_text.strip():
            return self._fail("comment_text_missing", "Comment text is required.")
        if not self.client.is_running():
            return self._fail(
                "container_stopped",
                f"Container {self.client.container_name} is not running.",
            )
        if not self.verify_logged_in():
            return self._fail(
                "session_unverified",
                "Facebook session is logged out or could not be verified.",
            )

        target, screen = self._open_profile_first_comment_input()
        if not target:
            return self._fail(
                "comment_input_not_found",
                "The first post's Comment as field could not be visually confirmed.",
            )

        self.log_decision(
            "Standalone comment",
            "first Comment as field under Posts/List view",
            f"target={target}",
            "click, paste the test comment, and submit once",
        )
        self.capture_evidence("before_comment", screen, target=list(target))
        self.human.click(*target)
        time.sleep(0.8)
        self.paste_text(self.comment_text)
        time.sleep(0.5)
        self.human.key_press("Return")
        time.sleep(3.0)
        self.capture_evidence("after_comment")
        self.log("SUCCESS", "Standalone comment submitted once; no retry attempted.")
        return self.set_outcome("completed", None, first_comment="submitted_unverified")
