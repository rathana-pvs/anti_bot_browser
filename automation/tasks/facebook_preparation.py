"""Passive, bounded Facebook session preparation for scheduled work."""

from __future__ import annotations

import random
import time

from .base_task import BaseTask
from engine.screen_state import ScreenState


class FacebookPreparationTask(BaseTask):
    DURATION_RANGES = {"brief": (40.0, 55.0), "extended": (55.0, 70.0)}

    def __init__(self, profile_id: str, mode: str = "brief"):
        super().__init__(profile_id)
        if mode not in self.DURATION_RANGES:
            raise ValueError("Preparation mode must be 'brief' or 'extended'")
        self.mode = mode

    def _current_session_is_stable(self) -> bool:
        valid_states = {
            ScreenState.FEED_READY,
            ScreenState.COMPOSER_OPEN,
            ScreenState.MEDIA_READY,
            ScreenState.POST_ENABLED,
        }
        try:
            first = self.client.screenshot()
            self.detect_visual_theme(first)
            first_observation = self.recognizer.observe(first)
            if first_observation.state not in valid_states:
                return False
            time.sleep(1.0)
            second = self.client.screenshot()
            second_observation = self.recognizer.observe(second)
            return (
                second_observation.state in valid_states
                and self.vision.screen_similarity(first, second) >= 0.985
            )
        except Exception as exc:
            self.log("DEBUG", f"Passive current-session check could not establish stability: {exc}")
            return False

    def run(self) -> bool:
        self.set_stage("preparing", preparation_mode=self.mode)
        if not self.client.is_running():
            return self.set_outcome(
                "failed_before_publish",
                f"Container {self.client.container_name} is not running.",
            )
        session_already_stable = self._current_session_is_stable()
        if session_already_stable:
            self.log("INFO", "Authenticated Facebook session is already stable; skipping navigation.")
        elif not self.verify_logged_in():
            self.capture_evidence("preparation_requires_review")
            return self.set_outcome(
                "needs_review",
                "Passive preparation could not verify an authenticated, unrestricted Facebook session.",
            )

        duration_seconds = random.uniform(*self.DURATION_RANGES[self.mode])
        deadline = time.time() + duration_seconds
        previous = self.client.screenshot()
        stable_observations = 0
        scroll_actions = 0
        while time.time() < deadline:
            notches = random.randint(2, 4)
            self.human.scroll("down", notches=notches)
            scroll_actions += 1
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0:
                break
            time.sleep(min(remaining, random.uniform(3.0, 6.0)))
            current = self.client.screenshot()
            similarity = self.vision.screen_similarity(previous, current)
            stable_observations = stable_observations + 1 if similarity >= 0.985 else 0
            previous = current

        if scroll_actions:
            self.human.key_press("Home")
            time.sleep(1.0)
            previous = self.client.screenshot()

        self.capture_evidence(
            "preparation_ready",
            previous,
            preparation_mode=self.mode,
            duration_seconds=round(duration_seconds, 2),
            scroll_actions=scroll_actions,
            navigation_skipped=session_already_stable,
            stable_observations=stable_observations,
        )
        self.set_stage("ready", preparation_mode=self.mode)
        return self.set_outcome(
            "completed",
            preparation_mode=self.mode,
            duration_seconds=round(duration_seconds, 2),
            scroll_actions=scroll_actions,
            stable_observations=stable_observations,
        )
