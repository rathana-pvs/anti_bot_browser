"""Guarded visual workflow for Facebook Reel publishing."""

from __future__ import annotations

import math
import time

from engine.screen_state import ScreenState
from .base_task import BaseTask


class FacebookReelTask(BaseTask):
    def __init__(
        self,
        profile_id: str,
        video_path: str,
        caption: str,
        comment_link: str | None = None,
    ):
        super().__init__(profile_id)
        self.video_path = video_path
        self.caption = caption
        self.comment_link = comment_link

    def _fail(self, code: str, message: str, screen=None) -> bool:
        self.log("ERROR", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("failed", code, message=message)

    def _uncertain(self, code: str, message: str, screen=None) -> bool:
        self.log("WARN", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("uncertain", code, message=message)

    def _find_stable_text(self, labels, prefer_lower_half=False):
        def locate():
            match = self.vision.find_text(labels, prefer_lower_half=prefer_lower_half)
            return match["center"] if match else None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _find_stable_enabled_action(self, labels, prefer_lower_half=True):
        """Match action text with an enabled blue button on the same screen."""
        def locate():
            screen = self.client.screenshot()
            text_match = self.vision.find_text(
                labels,
                screen=screen,
                min_confidence=0.45,
                prefer_lower_half=prefer_lower_half,
            )
            region = (
                0,
                screen.shape[0] // 2 if prefer_lower_half else 0,
                screen.shape[1],
                screen.shape[0] // 2 if prefer_lower_half else screen.shape[0],
            )
            blue = self.vision.find_blue_action_button(screen=screen, region=region)
            if not text_match or not blue:
                return None
            if math.hypot(text_match["center"][0] - blue[0], text_match["center"][1] - blue[1]) > 180:
                return None
            return blue

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _wait_for_target(self, locator, timeout: float, label: str):
        deadline = time.time() + timeout
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "error", None, screen
            target = locator()
            if target:
                self.capture_evidence(label, screen, target=list(target))
                return "ready", target, screen
            time.sleep(2.0)
        return "timeout", None, None

    def _verify_reel_publication(self, before_publish, timeout: float = 45.0):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            similarity = self.vision.similarity(before_publish, screen)
            last = (observation, screen, similarity)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "failed", last
            if observation.state == ScreenState.POST_CONFIRMED:
                return "published", last
            time.sleep(2.0)
        return "uncertain", last

    def run(self) -> bool:
        self.log("STEP", "Starting guarded Facebook Reel task...")

        if not self.client.is_running():
            return self._fail("container_stopped", f"Container {self.client.container_name} is not running.")
        if not self.verify_logged_in():
            return self._fail("session_unverified", "Facebook session is logged out or could not be verified after loading.")

        self.log("STEP", "Navigating to Facebook Reel Studio...")
        self.client.navigate_to("https://www.facebook.com/reel/create/")

        dropzone_status, dropzone, screen = self._wait_for_target(
            lambda: self._find_stable_text((
                "add video",
                "upload video",
                "select video",
                "add videos",
                "create reel",
            )),
            timeout=35.0,
            label="reel_dropzone_ready",
        )
        if dropzone_status == "error":
            return self._fail("reel_studio_error", "Facebook displayed an error in Reel Studio.", screen)
        if not dropzone:
            return self._fail("reel_dropzone_not_found", "Reel video dropzone could not be located confidently.")

        self.human.click(*dropzone)
        time.sleep(1.5)

        container_path = self.video_path
        if not container_path.startswith("/"):
            container_path = f"/data/shared_media/{container_path}"
        if not self.attach_file_gtk(container_path):
            return self._fail("file_chooser_failed", "The Reel file chooser could not be completed safely.")

        self.log("INFO", "Waiting for video processing and an enabled Next action...")
        next_status, next_button, screen = self._wait_for_target(
            lambda: self._find_stable_enabled_action(("next",)),
            timeout=120.0,
            label="reel_next_ready",
        )
        if next_status == "error":
            return self._fail("reel_processing_error", "Facebook displayed a Reel processing error.", screen)
        if not next_button:
            return self._fail("reel_processing_timeout", "An enabled Next action did not appear after processing.")

        self.human.click(*next_button)

        description_status, description, screen = self._wait_for_target(
            lambda: self._find_stable_text((
                "describe your reel",
                "describe your reel...",
                "write a caption",
                "description",
                "describe",
            )),
            timeout=30.0,
            label="reel_description_ready",
        )
        if description_status == "error":
            return self._fail("reel_description_error", "Facebook displayed an error before caption entry.", screen)
        if self.caption and not description:
            return self._fail("reel_description_not_found", "Reel description input could not be located confidently.")
        if description and self.caption:
            self.human.click(*description)
            self.paste_text(self.caption)

        publish_status, publish_button, screen = self._wait_for_target(
            lambda: self._find_stable_enabled_action(("publish", "share")),
            timeout=35.0,
            label="reel_publish_ready",
        )
        if publish_status == "error":
            return self._fail("reel_publish_error", "Facebook displayed an error before publication.", screen)
        if not publish_button:
            return self._fail("reel_publish_not_found", "Enabled Reel Publish action could not be confirmed.")

        before_publish = self.client.screenshot()
        self.capture_evidence("before_reel_publish", before_publish, target=list(publish_button))
        self.log("STEP", f"Clicking final Reel Publish action once at {publish_button}...")
        self.human.click(*publish_button)

        result, verification = self._verify_reel_publication(before_publish)
        if verification:
            observation, final_screen, similarity = verification
            self.capture_evidence(
                "reel_publication_verification",
                final_screen,
                state=observation.state.value,
                confidence=observation.confidence,
                signals=observation.signals,
                similarity_to_pre_publish=round(similarity, 5),
            )
        else:
            final_screen = None

        if result == "failed":
            return self._fail("reel_publish_rejected", "Facebook displayed an error after Reel publication.", final_screen)
        if result != "published":
            return self._uncertain(
                "reel_publish_unconfirmed",
                "Reel Publish was clicked once, but the result could not be visually confirmed.",
                final_screen,
            )

        if self.comment_link:
            self.log("WARN", "Reel was confirmed, but automated first-comment posting is not yet verified; skipping it.")
        self.log("SUCCESS", "Facebook Reel publication was visually confirmed.")
        return self.set_outcome(
            "published",
            None,
            first_comment="not_requested" if not self.comment_link else "skipped_unverified",
        )
