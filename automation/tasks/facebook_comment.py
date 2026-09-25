"""Standalone first-post comment task for safe workflow testing."""

import time

from .facebook_post import FacebookPostTask


class FacebookCommentTask(FacebookPostTask):
    def __init__(self, profile_id: str, comment_text: str, post_url: str | None = None):
        super().__init__(
            profile_id=profile_id,
            caption="",
            comment_link=comment_text,
            media_path=None,
        )
        self.comment_text = comment_text
        self.post_url = post_url

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

        comment_status = self.post_first_comment(self.comment_text, post_url=self.post_url)
        if comment_status == "submitted_unverified":
            self.log("SUCCESS", "Standalone comment submitted once; no retry attempted.")
            return self.set_outcome("completed", None, first_comment="submitted_unverified")
        else:
            return self._fail(
                "comment_input_not_found",
                "The 'Comment as ...' field could not be visually confirmed.",
            )
