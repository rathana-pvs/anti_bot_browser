"""Template-independent publication result checks."""

from __future__ import annotations

import time


class ReelPublicationResultVerifier:
    """Verify a posted Reel on its profile without knowing its composer template."""

    def __init__(self, task, *, attempts: int = 2, retry_delay: float = 15.0):
        self.task = task
        self.attempts = attempts
        self.retry_delay = retry_delay

    def verify_latest_reel(self) -> bool:
        for attempt in range(1, self.attempts + 1):
            self.task.log(
                "INFO",
                f"Refreshing the profile to check for the latest Reel (attempt {attempt}/{self.attempts}).",
            )
            self.task.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
            visible, screen = self.task._scan_profile_for_latest_reel()
            self.task.capture_evidence(
                f"latest_reel_check_{attempt}",
                screen,
                latest_reel_visible=visible,
            )
            if visible:
                return True
            if attempt < self.attempts:
                self.task.log(
                    "INFO",
                    f"Latest Reel is not visible yet; waiting {self.retry_delay:g} seconds before one final refresh.",
                )
                time.sleep(self.retry_delay)
        return False
