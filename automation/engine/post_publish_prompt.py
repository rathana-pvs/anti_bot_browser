"""Shared handler for Facebook's optional post-publish prompt."""

from __future__ import annotations

import time


class PostPublishPromptHandler:
    """Try three clicks at most, confirming dismissal after each attempt."""

    def __init__(self, task, *, close_timeout: float = 5.0, poll_interval: float = 0.5,
                 click_buffer: float = 5.0):
        self.task = task
        self.close_timeout = close_timeout
        self.poll_interval = poll_interval
        self.click_buffer = click_buffer
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
        for attempt in range(1, 4):
            self.task.log(
                "INFO",
                f"Detected post-publish prompt ('Not now'). Attempt {attempt}/3: clicking at {match['center']}...",
            )
            self.task.capture_evidence(
                "dismiss_post_prompt", screen, target=list(match["center"]), attempt=attempt,
            )
            self.task.click_reversible(
                match["center"], label="dismiss_post_prompt",
                bounds=match.get("bounds"), max_offset_px=4,
            )
            time.sleep(self.click_buffer)
            deadline = time.monotonic() + self.close_timeout
            while True:
                # Reacquire the button on fresh screenshots so retries follow
                # the current prompt rather than reusing stale coordinates.
                screen = self.task.client.screenshot()
                match = self.task.find_post_publish_prompt(screen)
                if not match:
                    self.dismissed = True
                    self.last_status = "dismissed"
                    return "dismissed"
                if time.monotonic() >= deadline:
                    break
                time.sleep(self.poll_interval)
        self.task.log("ERROR", "Post-publish prompt remained open after 3 Not now click attempts.")
        self.task.capture_evidence("post_publish_prompt_failed", screen, attempts=3)
        self.last_status = "failed"
        return "failed"
