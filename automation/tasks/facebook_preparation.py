"""Passive, bounded Facebook session preparation for scheduled work."""

from __future__ import annotations

import random
import time

from .base_task import BaseTask
from engine.screen_state import ScreenState
from engine.passive_browsing import PassiveBrowsing


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
            first_observation = self.recognizer.observe_session_gate(first)
            if first_observation.state == ScreenState.UNKNOWN:
                first_observation = self.recognizer.observe(first)
            if first_observation.state not in valid_states:
                return False
            time.sleep(1.0)
            second = self.client.screenshot()
            second_observation = self.recognizer.observe_session_gate(second)
            if second_observation.state == ScreenState.UNKNOWN:
                second_observation = self.recognizer.observe(second)
            return (
                second_observation.state in valid_states
                and self.vision.similarity(first, second) >= 0.985
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
        requested_surface = None
        already_open_surface = None
        if session_already_stable:
            self.log("INFO", "Authenticated Facebook session is already stable; skipping navigation.")
        else:
            requested_surface = random.choice(tuple(self.WARMING_SURFACES))
            if not self.verify_logged_in(
                target_url=self.WARMING_SURFACES[requested_surface]
            ):
                return self.skip_unverified_session()
            already_open_surface = requested_surface

        requested_surface, warm_surface, fallback_used, surface_screen = (
            self.select_and_open_warming_surface(
                requested=requested_surface,
                already_open=already_open_surface,
            )
        )
        if warm_surface is None:
            if getattr(self, "session_check_status", "") == "auth_required":
                return self.skip_unverified_session()
            return self.set_outcome(
                "failed_before_publish",
                "No Facebook warming surface reached a verified ready state.",
                warming_surface_requested=requested_surface,
            )

        rng = self.behavior_session.stream("warming")
        duration_seconds = rng.uniform(*self.DURATION_RANGES[self.mode])
        started = time.monotonic()
        deadline = started + duration_seconds
        previous = surface_screen if surface_screen is not None else self.client.screenshot()
        height, width = previous.shape[:2]
        browsing = PassiveBrowsing(self.behavior_session, width, height)
        scroll_actions = 0
        visible_changes = 0
        return_scroll_notches = 0
        self.log("STEP", f"Passive browsing on {warm_surface} for up to {duration_seconds:.0f}s.")
        while time.monotonic() < deadline:
            step = browsing.next_step()
            if step is None:
                self.log("INFO", "The page did not respond after a bounded direction check; ending passive browsing.")
                break
            target = browsing.pointer_target(self.client.get_mouse_position(), first=scroll_actions == 0)
            if target is not None:
                self.human.move_to(*target)
            if time.monotonic() >= deadline:
                break
            self.human.scroll(step.direction, notches=step.notches)
            scroll_actions += 1
            remaining = max(0.0, deadline - time.monotonic())
            self.log("INFO", f"Preparation: {step.reason}, {step.direction} {step.notches} notches; "
                     f"reading pause {min(remaining, step.pause_seconds):.1f}s; {remaining:.0f}s remaining.")
            if remaining > 0:
                time.sleep(min(remaining, step.pause_seconds))
            current = self.client.screenshot()
            # Compare scrollable content, excluding the static toolbar/sidebar.
            left, top, right, bottom = browsing.bounds
            changed = self.vision.similarity(previous[top:bottom, left:right],
                                              current[top:bottom, left:right]) < .985
            browsing.record(step, changed)
            visible_changes += int(changed)
            previous = current

        # Return gradually, without Home while focus may be in Chrome's address
        # bar. The handoff has its own short cap; do not add an unbounded rewind.
        return_deadline = time.monotonic() + 8.0
        if browsing.net_down:
            self.log("INFO", "Scrolling back up in short bursts before publishing handoff.")
        while browsing.net_down > 0 and time.monotonic() < return_deadline:
            target = browsing.pointer_target(self.client.get_mouse_position())
            if target is not None:
                self.human.move_to(*target)
            if time.monotonic() >= return_deadline:
                break
            notches = min(browsing.net_down, rng.randint(2, 4))
            self.human.scroll("up", notches=notches)
            browsing.net_down -= notches
            return_scroll_notches += notches
            remaining = max(0.0, return_deadline - time.monotonic())
            if remaining > 0:
                time.sleep(min(remaining, rng.uniform(.2, .6)))
        if scroll_actions:
            previous = self.client.screenshot()
        stable_observations = browsing.unchanged
        actual_browsing_seconds = round(max(0, time.monotonic() - started), 2)

        self.capture_evidence(
            "preparation_ready",
            previous,
            preparation_mode=self.mode,
            duration_seconds=round(duration_seconds, 2),
            scroll_actions=scroll_actions,
            return_scroll_notches=return_scroll_notches,
            remaining_scroll_offset_notches=browsing.net_down,
            visible_scroll_changes=visible_changes,
            browsing_stop_reason=browsing.stopped_reason or "duration_complete",
            actual_browsing_seconds=actual_browsing_seconds,
            warming_surface_requested=requested_surface,
            warming_surface=warm_surface,
            warming_surface_fallback=fallback_used,
            session_preflight_navigation_skipped=session_already_stable,
            warming_surface_navigation_skipped=False,
            stable_observations=stable_observations,
        )
        self.set_stage("ready", preparation_mode=self.mode, warming_surface=warm_surface)
        return self.set_outcome(
            "completed",
            preparation_mode=self.mode,
            duration_seconds=round(duration_seconds, 2),
            scroll_actions=scroll_actions,
            return_scroll_notches=return_scroll_notches,
            remaining_scroll_offset_notches=browsing.net_down,
            visible_scroll_changes=visible_changes,
            browsing_stop_reason=browsing.stopped_reason or "duration_complete",
            actual_browsing_seconds=actual_browsing_seconds,
            stable_observations=stable_observations,
            warming_surface_requested=requested_surface,
            warming_surface=warm_surface,
            warming_surface_fallback=fallback_used,
        )
