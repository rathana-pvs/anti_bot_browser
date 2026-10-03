"""Shared handler for Facebook's optional post-publish prompt."""

from __future__ import annotations

import time


class PostPublishPromptHandler:
    """Dismiss the recognized prompt once and confirm that it disappears."""

    def __init__(self, task, *, close_timeout: float = 5.0, poll_interval: float = 0.5):
        self.task = task
        self.close_timeout = close_timeout
        self.poll_interval = poll_interval
        self.dismissed = False
        self.last_status = "not_checked"

    def handle(self, screen=None) -> str:
        if self.dismissed:
            self.last_status = "dismissed"
            return "dismissed"
        screen = screen if screen is not None else self.task.client.screenshot()
        match = self.task.find_post_publish_prompt(screen)
        if not match:
            self.last_status = "absent"
            return "absent"
        self.task.log(
            "INFO",
            f"Detected post-publish prompt ('Not now'). Clicking at {match['center']}...",
        )
        self.task.capture_evidence(
            "dismiss_post_prompt", screen, target=list(match["center"])
        )
        self.task.click_reversible(
            match["center"], label="dismiss_post_prompt",
            bounds=match.get("bounds"), max_offset_px=4,
        )
        deadline = time.monotonic() + self.close_timeout
        while time.monotonic() < deadline:
            current = self.task.client.screenshot()
            if not self.task.find_post_publish_prompt(current):
                self.dismissed = True
                self.last_status = "dismissed"
                return "dismissed"
            time.sleep(self.poll_interval)
        self.last_status = "failed"
        return "failed"
