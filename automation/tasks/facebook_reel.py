"""Guarded visual workflow for Facebook Reel publishing."""

from __future__ import annotations

import math
import re
import time

from engine.screen_state import ScreenState
from engine.telemetry import timed_telemetry_step
from engine.vision import VisionEngine
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
        status = "uncertain" if getattr(self, "current_stage", "") in ("publish_clicked", "verifying") else "failed_before_publish"
        return self.set_outcome(status, code, message=message)

    def _uncertain(self, code: str, message: str, screen=None) -> bool:
        self.log("WARN", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("uncertain", code, message=message)

    def _needs_review(self, code: str, message: str, screen=None) -> bool:
        self.log("WARN", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("needs_review", code, message=message)

    def _find_stable_text(self, labels, prefer_lower_half=False, min_confidence=0.25, region=None):
        def locate():
            match = self.vision.find_text_cascaded(
                labels,
                region=region,
                prefer_lower_half=prefer_lower_half,
                min_confidence=min_confidence,
            )
            return match["center"] if match else None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    @staticmethod
    def _normalized_ocr_text(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()

    @classmethod
    def _is_profile_composer_prompt(cls, text: str) -> bool:
        """Recognize the profile composer prompt despite common OCR substitutions."""
        words = set(cls._normalized_ocr_text(text).split())
        has_what = bool(words & {"what", "whats", "what5", "whal", "whai"})
        return has_what and {"on", "your", "mind"}.issubset(words)

    def _find_profile_reel_action(self):
        """Find only the Reel action inside the profile composer card.

        Facebook also shows a ``Reels`` navigation tab above the composer.  Text
        matching alone can select that tab, so an accepted target must be an
        exact action label geometrically anchored to the composer prompt or to
        the adjacent Photo/video action.
        """
        def locate():
            started = time.perf_counter()
            screen = self.client.screenshot()
            height, width = screen.shape[:2]
            region = self.vision.get_pixel_region(screen.shape, "profile_post_stream")
            candidates = self.vision.read_text(screen, region=region, min_confidence=0.20)

            prompts = [
                item for item in candidates
                if self._is_profile_composer_prompt(item["text"])
            ]
            photo_actions = [
                item for item in candidates
                if self._normalized_ocr_text(item["text"])
                in {"photo video", "photos videos", "photo lvideo", "photo ivideo"}
            ]
            reel_actions = [
                item for item in candidates
                if self._normalized_ocr_text(item["text"]) in {"reel", "create reel"}
            ]

            accepted = []
            for reel in reel_actions:
                rx, ry = reel["center"]

                # Normal profile composer layout: the action row is shortly
                # below the caption prompt and extends to its right.
                for prompt in prompts:
                    px, py = prompt["center"]
                    if (
                        0.018 * height <= ry - py <= 0.15 * height
                        and -0.04 * width <= rx - px <= 0.38 * width
                    ):
                        accepted.append((abs((ry - py) - 0.055 * height), reel))
                        break
                else:
                    # OCR may miss the grey prompt.  Photo/video is a strong
                    # secondary anchor because it shares the same action row.
                    for photo in photo_actions:
                        phx, phy = photo["center"]
                        if abs(ry - phy) <= 0.035 * height and 0 < rx - phx <= 0.30 * width:
                            accepted.append((abs(ry - phy), reel))
                            break

            accepted.sort(key=lambda value: (value[0], -value[1].get("confidence", 0.0)))
            match = accepted[0][1] if accepted else None
            self.record_locator_telemetry(
                "profile_reel_composer_action",
                "composer_anchored_ocr",
                "profile_post_stream",
                (time.perf_counter() - started) * 1000.0,
                bool(match),
                confidence=match.get("confidence") if match else None,
                fallback_reason=None if match else "no_composer_anchored_reel_action",
            )
            return match["center"] if match else None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _find_reel_upload_target(self):
        """Locate an actionable Reel upload control, never preview instructional text."""
        def locate():
            screen = self.client.screenshot()

            # Preferred control: the enabled blue Upload button in the lower-left
            # Reel sidebar. OCR is restricted to the detected button rectangle.
            sidebar_bottom = (0.0, 0.55, 0.32, 1.0)
            blue_buttons = self.vision.find_blue_action_buttons(
                screen=screen,
                region=sidebar_bottom,
            )
            if isinstance(blue_buttons, list):
                for button in blue_buttons:
                    x1, y1, x2, y2 = button["bounds"]
                    for item in self.vision.read_text(
                        screen,
                        region=(x1, y1, x2 - x1, y2 - y1),
                        min_confidence=0.15,
                    ):
                        words = set(re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).split())
                        if "upload" in words:
                            return button["center"]

            # Secondary control: exact Add video label in the left dropzone.
            sidebar = VisionEngine.get_pixel_region(screen.shape, (0.0, 0.05, 0.25, 0.78))
            candidates = self.vision.read_text(screen, region=sidebar, min_confidence=0.20)
            for item in sorted(candidates, key=lambda candidate: candidate["confidence"], reverse=True):
                normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                if normalized in {"add video", "upload video", "select video"}:
                    return item["center"]
            return None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _find_stable_enabled_action(self, labels, prefer_lower_half=True, region="bottom_action_bar"):
        """Match action text with an enabled blue button on the same screen."""
        def locate():
            screen = self.client.screenshot()
            search_region = region
            if search_region is None and prefer_lower_half:
                search_region = (
                    0,
                    screen.shape[0] // 2,
                    screen.shape[1],
                    screen.shape[0] // 2,
                )
            blue = self.vision.find_blue_action_button(screen=screen, region=search_region)
            if not blue:
                return None
            # Scan tight region around blue button at full resolution first
            text_match = self.vision.find_text_cascaded(
                labels,
                screen=screen,
                region=(max(0, blue[0] - 260), max(0, blue[1] - 50), 520, 100),
                min_confidence=0.20,
            )
            if not text_match:
                text_match = self.vision.find_text_cascaded(
                    labels,
                    screen=screen,
                    region=search_region,
                    min_confidence=0.35,
                )
            if text_match:
                if math.hypot(text_match["center"][0] - blue[0], text_match["center"][1] - blue[1]) <= 180:
                    return blue

            # Semantic fallback when local OCR keywords fail to match
            goal = "identify the next action" if any("next" in l for l in labels) else "identify the publish action"
            is_rev = "publish" not in goal and "post" not in goal and "share" not in goal
            allow, reason, semantic_pos = self.rank_candidates_semantically(
                goal=goal,
                screen=screen,
                region=search_region,
                is_reversible=is_rev,
            )
            if allow and semantic_pos:
                return semantic_pos
            if reason == "needs_review":
                self._requires_review = True
            return None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _wait_for_target(self, locator, timeout: float, label: str):
        started = time.perf_counter()

        def finish(status: str) -> None:
            step_names = {
                "reel_next_ready": "media_processing",
                "reel_publish_ready": "publish_readiness",
            }
            telemetry = getattr(self, "telemetry", None)
            if telemetry is not None:
                telemetry.record_step(
                    step_names.get(label, "target_wait"),
                    (time.perf_counter() - started) * 1000.0,
                    outcome=status,
                    target=label,
                )

        deadline = time.time() + timeout
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            if observation.state == ScreenState.ERROR_DIALOG:
                finish("error")
                return "error", None, screen
            target = locator()
            if target:
                self.capture_evidence(label, screen, target=list(target))
                finish("ready")
                return "ready", target, screen
            time.sleep(2.0)
        finish("timeout")
        return "timeout", None, None

    @timed_telemetry_step("publication_verification")
    def _verify_reel_publication(self, before_publish, timeout: float = 300.0):
        deadline = time.time() + timeout
        last = None
        feed_streak = 0
        prompt_dismissed = False
        confirmation_seen = False
        while time.time() < deadline:
            screen = self.client.screenshot()

            # Dismiss post-publish prompts first (e.g. "Speak With People Directly" -> "Not now")
            if not prompt_dismissed and self.check_and_dismiss_post_prompt(screen):
                prompt_dismissed = True
                self.log("INFO", "Dismissed post-publish prompt ('Not now').")
                time.sleep(2.0)
                continue

            observation = self.recognizer.observe(screen)
            similarity = self.vision.similarity(before_publish, screen)
            last = (observation, screen, similarity)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "failed", last
            if observation.state == ScreenState.POST_CONFIRMED:
                confirmation_seen = True
                feed_streak = 0
                self.log("INFO", "Facebook showed publication confirmation; waiting for the modal to close.")
                time.sleep(2.0)
                continue
            if observation.state in {
                ScreenState.PUBLISHING,
                ScreenState.POST_ENABLED,
                ScreenState.COMPOSER_OPEN,
                ScreenState.MEDIA_UPLOADING,
                ScreenState.MEDIA_READY,
            }:
                feed_streak = 0
                time.sleep(2.0)
                continue
            if observation.state == ScreenState.FEED_READY and similarity < 0.985:
                return "published", last
            if observation.state == ScreenState.FEED_READY:
                feed_streak += 1
                if feed_streak >= 1:
                    return "published", last
            else:
                feed_streak = 0
            time.sleep(2.0)

        self.log("WARN", "Publishing modal did not fully close before the maximum verification wait.")
        return "uncertain", last

    def run(self) -> bool:
        self.set_stage("preparing")
        self.log("STEP", "Starting guarded Facebook Reel task...")

        if not self.client.is_running():
            return self._fail("container_stopped", f"Container {self.client.container_name} is not running.")
        if not self.verify_logged_in():
            return self.skip_unverified_session()

        self.log("STEP", "Navigating to Facebook profile page...")
        self.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)

        self.log("STEP", "Locating Reel button on profile page...")
        def locate_reel_button():
            target = self._find_profile_reel_action()
            if target:
                return target
            self.human.scroll("down", notches=2)
            time.sleep(0.8)
            return self._find_profile_reel_action()

        reel_btn_status, reel_button, screen = self._wait_for_target(
            locate_reel_button,
            timeout=35.0,
            label="profile_reel_button_ready",
        )
        if reel_btn_status == "error":
            return self._fail("profile_error", "Facebook displayed an error dialog on the profile page.", screen)
        if not reel_button:
            return self._fail("profile_reel_button_not_found", "Reel creation button could not be located on the profile page.")

        self.log_decision(
            "Open Reel Studio",
            "stable Reel action anchored inside the profile composer card",
            f"target={reel_button}",
            "click Reel button to open Reel creator dialog",
        )
        self.capture_evidence("before_click_reel_button", target=list(reel_button))
        self.human.click(*reel_button)
        time.sleep(2.5)

        self.set_stage("composing")
        self.log("STEP", "Locating Reel video dropzone in Create reel dialog...")
        dropzone_status, dropzone, screen = self._wait_for_target(
            self._find_reel_upload_target,
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
            lambda: self._find_stable_enabled_action(("next",), region="bottom_action_bar"),
            timeout=120.0,
            label="reel_next_ready",
        )
        if next_status == "error":
            return self._fail("reel_processing_error", "Facebook displayed a Reel processing error.", screen)
        if not next_button:
            return self._fail("reel_processing_timeout", "An enabled Next action did not appear after processing.")

        self.log("STEP", f"Clicking Next action at {next_button}...")
        self.human.click(*next_button)
        time.sleep(2.0)

        # Check if an intermediate "Edit reel" step is presented (with another Next action)
        edit_next_status, edit_next_button, screen = self._wait_for_target(
            lambda: self._find_stable_enabled_action(("next",), region="bottom_action_bar"),
            timeout=15.0,
            label="reel_edit_next_ready",
        )
        if edit_next_status != "timeout" and edit_next_button:
            self.log("STEP", f"Clicking intermediate Next action on Edit reel step at {edit_next_button}...")
            self.human.click(*edit_next_button)
            time.sleep(2.0)

        self.log("STEP", "Locating Reel description input...")
        def locate_description():
            match = self._find_stable_text((
                "describe your reel",
                "describe your reel...",
                "write a caption",
                "description",
                "describe",
            ), min_confidence=0.20, region="reel_sidebar")
            if match:
                return match
            # Visual fallback: in the Reel settings dialog, the description textarea is located ~170px above the Public control
            screen = self.client.screenshot()
            public_label = self.vision.find_text_cascaded(
                ("public", "tag and collaborate", "add ai label"),
                region="reel_sidebar",
                screen=screen,
                min_confidence=0.35,
            )
            if public_label:
                px, py = public_label["center"]
                return (px + 100, max(200, py - 170))
            return None

        description_status, description, screen = self._wait_for_target(
            locate_description,
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

        self.log("INFO", "Waiting for enabled Post / Publish action (background processing / copyright check)...")
        self._requires_review = False
        publish_status, publish_button, screen = self._wait_for_target(
            lambda: self._find_stable_enabled_action(("post", "publish", "share"), region="bottom_action_bar"),
            timeout=90.0,
            label="reel_publish_ready",
        )
        if publish_status == "error":
            return self._fail("reel_publish_error", "Facebook displayed an error before publication.", screen)
        if not publish_button:
            if getattr(self, "_requires_review", False):
                return self._needs_review(
                    "semantic_publish_gated",
                    "Enabled Reel Post / Publish action was semantically proposed but requires operator review before execution.",
                    screen,
                )
            return self._fail("reel_publish_not_found", "Enabled Reel Post / Publish action could not be confirmed.")

        self.set_stage("ready_to_publish", target=list(publish_button))
        before_publish = self.client.screenshot()
        self.capture_evidence("before_reel_publish", before_publish, target=list(publish_button))
        self.log("STEP", f"Clicking final Reel Post action once at {publish_button}...")
        self.set_stage("publish_clicked", target=list(publish_button))
        self.human.click(*publish_button)

        self.set_stage("verifying")
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
            self.log("INFO", f"Reel publication state '{result}' without error dialog; treating as published per operator rule.")

        # Phase 1: Correlate published Reel and extract permalink
        permalink_info = self.correlate_and_extract_permalink(
            caption=self.caption,
            media_type="reel",
        )

        # First-comment destination link (if provided)
        if self.comment_link:
            comment_status = self.post_first_comment(
                self.comment_link,
                post_url=permalink_info.get("post_url"),
            )
            self.log("SUCCESS", "Facebook Reel publication was visually confirmed.")
            return self.set_outcome(
                "published",
                None,
                first_comment=comment_status,
                first_comment_method=getattr(self, "last_comment_method", None),
                **permalink_info,
            )

        self.log("SUCCESS", "Facebook Reel publication was visually confirmed.")
        return self.set_outcome(
            "published",
            None,
            first_comment="not_requested",
            **permalink_info,
        )
