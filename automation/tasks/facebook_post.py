"""Guarded visual state machine for Facebook photo/text publishing."""

import json
from pathlib import Path
import random
import time
import re
from engine.screen_state import ScreenState
from engine.telemetry import timed_telemetry_step
from .base_task import BaseTask


class FacebookPostTask(BaseTask):
    CAPTION_LABELS = (
        "what's on your mind",
        "what’s on your mind",
        "whats on your mind",
        "what s on your mind",
        "write something",
        "create a public post",
    )

    def __init__(
        self,
        profile_id: str,
        caption: str,
        comment_link: str | None = None,
        media_path: str | None = None,
        brain_package=None,
    ):
        super().__init__(profile_id)
        self.caption = caption
        self.comment_link = comment_link
        self.media_path = media_path
        self.brain_metadata = brain_package.metadata() if brain_package is not None else None
        self.brain_targets = {}
        if brain_package is not None:
            targets_path = Path(brain_package.root) / "config" / "targets.json"
            try:
                loaded_targets = json.loads(targets_path.read_text(encoding="utf-8"))
                if isinstance(loaded_targets, dict):
                    self.brain_targets = loaded_targets
            except (OSError, json.JSONDecodeError) as exc:
                self.log("WARN", f"Brain target configuration could not be loaded: {exc}")

    def _brain_labels(self, name: str, fallback) -> tuple[str, ...]:
        configured = getattr(self, "brain_targets", {}).get(name)
        if isinstance(configured, list) and configured and all(isinstance(value, str) for value in configured):
            return tuple(configured)
        return tuple(fallback)

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

    def _stable_ocr_target(self, labels, prefer_lower_half=False, region=None):
        def locate():
            match = self.vision.find_text_cascaded(
                labels,
                region=region,
                prefer_lower_half=prefer_lower_half,
            )
            return match["center"] if match else None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    @staticmethod
    def _is_caption_prompt(text: str) -> bool:
        """Recognize the caption prompt while tolerating narrow OCR substitutions."""
        normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
        words = set(normalized.split())
        # EasyOCR commonly reads the final 't' as lowercase 'l' or uppercase 'I'
        # and may split the apostrophe-s into a separate 5/$ token.
        has_what = bool(words & {"what", "whats", "what5", "whal", "whai"})
        return has_what and {"on", "your", "mind"}.issubset(words)

    def _stable_feed_composer_target(self):
        """Locate the profile feed composer using a stable, region-gated phrase."""
        def locate():
            started = time.perf_counter()
            screen = self.client.screenshot()
            region = self.vision.get_pixel_region(screen.shape, "profile_post_stream")
            candidates = self.vision.read_text(screen, region=region, min_confidence=0.20)
            match = next(
                (item for item in candidates if self._is_caption_prompt(item["text"])),
                None,
            )
            self.record_locator_telemetry(
                "profile_composer_prompt",
                "region_phrase_ocr",
                "profile_post_stream",
                (time.perf_counter() - started) * 1000.0,
                bool(match),
                confidence=match.get("confidence") if match else None,
                fallback_reason=None if match else "caption_phrase_missing",
            )
            return match["center"] if match else None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    @staticmethod
    def _is_photo_video_label(text: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
        return normalized in {
            "photo video",
            "photos videos",
            "photolvideo",
            "photoivideo",
        }

    def _stable_profile_media_target(self):
        """Locate Photo/video only when it is anchored to the profile composer.

        Profile pages can contain other photo imagery above the composer, most
        notably the cover photo.  A full-screen template or green-pixel match can
        therefore produce a plausible but unsafe target.  Require the composer
        prompt first, then accept only the action row immediately beneath it.
        """
        def locate():
            started = time.perf_counter()
            screen = self.client.screenshot()
            stream_region = self.vision.get_pixel_region(
                screen.shape,
                "profile_post_stream",
            )
            candidates = self.vision.read_text(
                screen,
                region=stream_region,
                min_confidence=0.20,
            )
            prompts = [
                item for item in candidates
                if self._is_caption_prompt(item["text"])
            ]
            labels = [
                item for item in candidates
                if self._is_photo_video_label(item["text"])
            ]

            match = None
            prompt = None
            for candidate_prompt in prompts:
                px, py = candidate_prompt["center"]
                anchored = [
                    item for item in labels
                    if 20 <= item["center"][1] - py <= 150
                    and abs(item["center"][0] - px) <= 600
                ]
                if anchored:
                    prompt = candidate_prompt
                    match = min(
                        anchored,
                        key=lambda item: (
                            item["center"][1] - py,
                            abs(item["center"][0] - px),
                        ),
                    )
                    break

            target = match["center"] if match else None
            tier = "composer_anchored_ocr"

            # OCR can miss the label even when the icon remains clear. Restrict
            # visual matching to the narrow action-row band under the confirmed
            # composer prompt; never search the cover-photo portion of the page.
            if target is None and prompts:
                height = screen.shape[0]
                prompt = max(prompts, key=lambda item: item["center"][1])
                px, py = prompt["center"]
                x1 = max(stream_region[0], px - 500)
                x2 = min(stream_region[0] + stream_region[2], px + 350)
                y1 = max(stream_region[1], py + 20)
                y2 = min(height, py + 150)
                if x2 > x1 and y2 > y1:
                    target = self.vision.find_photo_video_button(
                        screen=screen,
                        region=(x1, y1, x2 - x1, y2 - y1),
                    )
                    tier = "composer_anchored_visual"

            self.record_locator_telemetry(
                "profile_photo_video_action",
                tier,
                "profile_post_stream",
                (time.perf_counter() - started) * 1000.0,
                bool(target),
                confidence=match.get("confidence") if match else None,
                fallback_reason=None if target else "composer_anchored_action_missing",
            )
            return target

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _stable_blue_text_target(self, labels, region="bottom_action_bar"):
        """Require an exact label token inside the detected blue button bounds."""
        def locate():
            started = time.perf_counter()
            screen = self.client.screenshot()
            candidates = self.vision.find_blue_action_buttons(
                screen=screen,
                region=region,
            )
            if not isinstance(candidates, list) or not candidates:
                self.record_locator_telemetry(
                    "enabled_action:" + "|".join(labels),
                    "enabled_blue_geometry",
                    region,
                    (time.perf_counter() - started) * 1000.0,
                    False,
                    fallback_reason="no_enabled_blue_button",
                )
                return None
            button = candidates[0]
            x1, y1, x2, y2 = button["bounds"]
            wanted = {label.casefold().strip() for label in labels}
            for item in self.vision.read_text(
                screen,
                region=(x1, y1, x2 - x1, y2 - y1),
                min_confidence=0.15,
            ):
                words = set(re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).split())
                if words & wanted:
                    self.record_locator_telemetry(
                        "enabled_action:" + "|".join(labels),
                        "enabled_blue_plus_ocr",
                        region,
                        (time.perf_counter() - started) * 1000.0,
                        True,
                        confidence=item["confidence"],
                    )
                    self.remember_reversible_click_bounds(button["center"], button["bounds"])
                    return button["center"]
            self.record_locator_telemetry(
                "enabled_action:" + "|".join(labels),
                "enabled_blue_plus_ocr",
                region,
                (time.perf_counter() - started) * 1000.0,
                False,
                fallback_reason="button_label_mismatch",
            )
            return None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _stable_review_post_target(self):
        """Use geometry only after the Post settings review was independently seen."""
        def locate():
            started = time.perf_counter()
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            if (
                observation.state != ScreenState.POST_ENABLED
                or "post settings review" not in observation.signals
            ):
                self.record_locator_telemetry(
                    "review_post_action", "state_gated_geometry", "bottom_action_bar",
                    (time.perf_counter() - started) * 1000.0, False,
                    confidence=observation.confidence, fallback_reason="review_state_not_confirmed",
                )
                return None
            candidates = self.vision.find_blue_action_buttons(
                screen=screen,
                region="bottom_action_bar",
            )
            if not isinstance(candidates, list) or len(candidates) != 1:
                self.record_locator_telemetry(
                    "review_post_action", "state_gated_geometry", "bottom_action_bar",
                    (time.perf_counter() - started) * 1000.0, False,
                    confidence=observation.confidence, fallback_reason="action_not_unique",
                )
                return None
            self.record_locator_telemetry(
                "review_post_action", "state_gated_geometry", "bottom_action_bar",
                (time.perf_counter() - started) * 1000.0, True,
                confidence=observation.confidence,
            )
            return candidates[0]["center"]

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    @timed_telemetry_step("publish_readiness")
    def _stable_post_target(self, review_confirmed=False):
        target = self._stable_blue_text_target(
            self._brain_labels("publish_button", ("post", "publish")),
            region="bottom_action_bar",
        )
        if target:
            return target
        if review_confirmed:
            target = self._stable_review_post_target()
            if target:
                return target
        # Irreversible action: semantic fallback proposal triggers 'needs_review'
        screen = self.client.screenshot()
        allow, reason, semantic_pos = self.rank_candidates_semantically(
            goal="identify the publish action",
            screen=screen,
            region="bottom_action_bar",
            is_reversible=False,
        )
        if reason == "needs_review":
            self._requires_review = True
        return None

    def _stable_next_target(self):
        target = self._stable_blue_text_target(
            self._brain_labels("next_button", ("next",)),
            region="bottom_action_bar",
        )
        if target:
            return target
        # Reversible action: guarded semantic fallback
        screen = self.client.screenshot()
        allow, reason, semantic_pos = self.rank_candidates_semantically(
            goal="identify the next action",
            screen=screen,
            region="bottom_action_bar",
            is_reversible=True,
        )
        if allow and semantic_pos:
            return semantic_pos
        return None

    def _stable_caption_target(self):
        """Locate the caption prompt inside a visually confirmed composer modal."""
        def locate():
            started = time.perf_counter()
            screen = self.client.screenshot()
            region = self.vision.get_pixel_region(
                screen.shape,
                "composer_modal",
            )
            candidates = self.vision.read_text(
                screen=screen,
                region=region,
                min_confidence=0.20,
            )
            title_target = None
            caption_target = None
            for item in candidates:
                normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                words = set(normalized.split())
                if {"create", "post"}.issubset(words) and len(words) <= 4:
                    title_target = item
                    continue
                # Requiring the full phrase inside the modal caption region is
                # strict enough to reject the neighboring AI-label control.
                if self._is_caption_prompt(item["text"]):
                    caption_target = item
                if "write something" in normalized or "create a public post" in normalized:
                    caption_target = item

            # Before any caption is entered, Facebook's Next button is disabled
            # and gray. Confirm the modal using its title instead of requiring an
            # enabled blue action. The vertical ordering rejects the identical
            # prompt that remains visible in the dimmed feed behind the modal.
            found = bool(
                title_target
                and caption_target
                and title_target["center"][1] < caption_target["center"][1]
            )
            self.record_locator_telemetry(
                "composer_caption_prompt",
                "modal_title_plus_ocr",
                "composer_modal",
                (time.perf_counter() - started) * 1000.0,
                found,
                confidence=caption_target.get("confidence") if found else None,
                fallback_reason=None if found else "modal_title_or_caption_missing",
            )
            return caption_target["center"] if found else None

        # OCR can occasionally miss the low-contrast placeholder for one frame
        # while Facebook finishes rendering the attached-media preview. Keep the
        # click gated on two matching coordinates, but tolerate one intervening
        # miss instead of discarding otherwise consistent visual evidence.
        return self.vision.find_stable(
            locate,
            attempts=2,
            tolerance_px=16.0,
            max_intermittent_misses=1,
        )

    @timed_telemetry_step("media_processing")
    def _wait_until_media_ready(self, timeout: float = 75.0):
        deadline = time.time() + timeout
        last = None
        last_state = None
        feed_streak = 0
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            last = (observation, screen)
            if observation.state != last_state:
                self.log_decision(
                    "Media readiness",
                    "Create post modal with an enabled Next or Post action",
                    f"state={observation.state.value}, confidence={observation.confidence:.2f}, signals={observation.signals or 'none'}",
                    "continue" if observation.state in {ScreenState.MEDIA_READY, ScreenState.POST_ENABLED} else "wait",
                    level="INFO",
                )
                last_state = observation.state
            if observation.state == ScreenState.ERROR_DIALOG:
                return last
            if observation.state in {ScreenState.MEDIA_READY, ScreenState.POST_ENABLED}:
                return last
            if observation.state == ScreenState.FEED_READY:
                feed_streak += 1
                if feed_streak >= 3:
                    return last
            else:
                feed_streak = 0
            time.sleep(1.5)
        return last

    def _is_recent_post_on_screen(self, screen, caption: str | None = None) -> bool:
        if screen is None:
            return False
        ocr = self.vision.read_text(screen, min_confidence=0.18)
        recent_indicators = {"just now", "a few seconds ago", "few seconds", "1m", "2m", "moment ago"}
        for item in ocr:
            clean = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
            if any(ind in clean for ind in recent_indicators):
                return True
        if caption:
            cap_words = [w for w in re.sub(r"[^a-z0-9]+", " ", caption.casefold()).split() if len(w) >= 3]
            if cap_words:
                text_blob = " ".join(item["text"].casefold() for item in ocr)
                matches = sum(1 for w in cap_words if w in text_blob)
                if matches >= min(2, len(cap_words)):
                    return True
        return False

    @timed_telemetry_step("publication_verification")
    def _verify_publication(self, before_publish, timeout: float = 300.0):
        """
        Confirm publication.
        Note: Post-publish prompt ('Not now') does not always appear; if absent,
        composer closing and return to feed confirms publication.
        """
        deadline = time.time() + timeout
        last = None
        feed_streak = 0
        prompt_dismissed = False
        confirmation_seen = False
        while time.time() < deadline:
            screen = self.client.screenshot()

            # Dismiss post-publish prompts if present (e.g. "Speak With People Directly" -> "Not now")
            # This prompt is optional. Once dismissed, the publish click has been
            # accepted and the comment stage may begin without another navigation.
            if not prompt_dismissed and self.check_and_dismiss_post_prompt(screen):
                prompt_dismissed = True
                self.log("INFO", "Dismissed post-publish prompt ('Not now').")
                # Clicking the optional prompt is not itself the transition
                # boundary. Wait until Facebook removes every publishing modal.
                time.sleep(1.5)
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
                time.sleep(1.5)
                continue

            # Do not use caption text as confirmation while Facebook is still
            # showing the review modal. The caption is visible behind "Posting"
            # and previously caused verification to finish before the optional
            # prompt appeared.
            if observation.state in {
                ScreenState.PUBLISHING,
                ScreenState.POST_ENABLED,
                ScreenState.COMPOSER_OPEN,
                ScreenState.MEDIA_UPLOADING,
                ScreenState.MEDIA_READY,
            }:
                feed_streak = 0
                time.sleep(1.5)
                continue

            # Require two stable feed observations after the composer disappears.
            # A single changed frame can be a transient dialog or navigation state.
            if observation.state == ScreenState.FEED_READY and similarity < 0.985:
                feed_streak += 1
                if feed_streak >= 2:
                    prompt_note = " after dismissing 'Not now'" if prompt_dismissed else " without an optional prompt"
                    confirmation_note = " after publication confirmation" if confirmation_seen else ""
                    self.log("INFO", f"All publishing modals closed; Facebook returned to a stable profile feed{prompt_note}{confirmation_note}.")
                    return "published", last
            else:
                feed_streak = 0

            time.sleep(1.5)

        # Never navigate away or begin commenting while a publishing/review modal
        # may still be open. This avoids confusing slow network processing with
        # the no-prompt branch.
        self.log("WARN", "Publishing modal did not fully close before the maximum verification wait.")
        return "uncertain", last

    def run(self) -> bool:
        self.set_stage("preparing")
        self.log("STEP", "Starting Facebook auto-post task...")

        if not self.client.is_running():
            return self._fail("container_stopped", f"Container {self.client.container_name} is not running.")

        # Step 1: Pre-task login gate
        if not self.verify_logged_in(target_url="https://www.facebook.com/me"):
            return self.skip_unverified_session()

        # Login verification already opened and validated the profile page.
        self.log("INFO", "Reusing the verified profile page for post composition.")

        self.set_stage("composing")
        # Step 3: Handle media attachment (Photo/Video) or text composer
        if self.media_path:
            self.log("STEP", f"Locating Photo/video button on profile for: {self.media_path}")

            # Locate the action only inside the confirmed profile composer card.
            # Full-screen photo matching can confuse the Page cover image with
            # the media action and is intentionally not used here.
            photo_btn = self._stable_profile_media_target()

            self.log_decision(
                "Click Photo/video on profile",
                "stable Photo/video action anchored below the profile composer prompt",
                f"target={photo_btn}",
                "click target to trigger GTK file chooser" if photo_btn else "fail safe",
            )
            if not photo_btn:
                return self._fail("photo_button_not_found", "Photo/video button on profile could not be located confidently.")

            self.capture_evidence("before_click_photo_video", target=list(photo_btn))
            self.human.click(*photo_btn)
            time.sleep(random.uniform(1.5, 2.5))

            # Trigger Zero-CDP GTK file chooser
            container_path = self.media_path
            # If path is relative to shared_media on host, map to /data/shared_media
            if not container_path.startswith("/"):
                container_path = f"/data/shared_media/{container_path}"

            if not self.attach_file_gtk(container_path):
                return self._fail("file_chooser_failed", "The media file chooser could not be completed safely.")

            self.log("INFO", "Waiting for the attached-media composer to become ready...")
            media_result = self._wait_until_media_ready()
            if media_result is None:
                return self._fail("media_ready_timeout", "No visual media-ready state was detected.")
            media_observation, media_screen = media_result
            self.capture_evidence(
                "media_state",
                media_screen,
                state=media_observation.state.value,
                confidence=media_observation.confidence,
                signals=media_observation.signals,
            )
            if media_observation.state == ScreenState.ERROR_DIALOG:
                return self._fail("upload_error", "Facebook displayed an upload or composer error.", media_screen)
            if media_observation.state == ScreenState.FEED_READY:
                return self._fail(
                    "media_attachment_rejected",
                    "The file chooser closed, but Facebook returned to the feed without attaching the media.",
                    media_screen,
                )
            if media_observation.state not in {ScreenState.MEDIA_READY, ScreenState.POST_ENABLED}:
                return self._fail("media_ready_timeout", "Media never reached a confirmed ready state.", media_screen)
        else:
            self.log("STEP", "Locating post composer ('What\\'s on your mind?')...")
            composer_pos = self.vision.find_stable(
                lambda: self.vision.find_element("composer_input") or self.vision.find_element("composer_button"),
                attempts=2,
                tolerance_px=8.0,
            )
            if not composer_pos:
                composer_pos = self._stable_feed_composer_target()
            if not composer_pos:
                composer_pos = self._stable_ocr_target(
                    self._brain_labels("composer_entry", (
                        "what's on your mind",
                        "what’s on your mind",
                        "whats on your mind",
                        "what s on your mind",
                        "write something",
                    )),
                    region="profile_post_stream",
                )
            if not composer_pos:
                return self._fail("composer_not_found", "Post composer could not be located confidently.")
            self.human.click(*composer_pos)
            time.sleep(random.uniform(2.0, 3.0))

        if self.caption:
            # Locate the composer text area only when text actually needs to be entered.
            caption_target = self._stable_caption_target()
            self.log_decision(
                "Enter caption",
                "What's on your mind text inside the modal caption region",
                f"target={caption_target}" if caption_target else "no caption input target",
                "click and enter caption" if caption_target else "stop without typing",
            )
            if not caption_target:
                return self._fail(
                    "caption_input_not_found",
                    "Caption input could not be visually confirmed; no fallback coordinate was used.",
                )

            self.human.click(*caption_target)
            time.sleep(0.5)
            self.log("STEP", f"Entering post caption ({len(self.caption)} chars)...")
            if any(ord(c) > 127 for c in self.caption) or len(self.caption) > 80:
                self.paste_text(self.caption)
            else:
                self.human.type_text(self.caption, wpm=random.randint(52, 65))

            review_pause = random.uniform(2.0, 3.5)
            self.log("INFO", f"Human review pause ({review_pause:.1f}s)...")
            time.sleep(review_pause)
        else:
            self.log("INFO", "No caption requested; skipping caption-field detection and text-entry pause.")

        # Some Facebook composer variants use a two-step flow: caption/media,
        # then Next, then the final Post confirmation screen.
        next_btn = self._stable_next_target()
        self.log_decision(
            "Next action",
            "the word Next inside the enabled blue action button",
            f"target={next_btn}" if next_btn else "Next was not found on the blue action",
            "click Next and wait for final Post" if next_btn else "check for a direct Post action",
        )
        if next_btn:
            before_next = self.client.screenshot()
            self.capture_evidence("before_next", before_next, target=list(next_btn))
            self.log("STEP", f"Clicking the visually confirmed Next action at {next_btn}...")
            self.click_reversible(next_btn, label="post_next", max_offset_px=5)
            review_result = self.wait_for_states(
                {ScreenState.POST_ENABLED, ScreenState.ERROR_DIALOG},
                timeout=30.0,
                poll_interval=1.0,
            )
            if not review_result:
                return self._fail("post_review_timeout", "No review screen appeared after clicking Next.")
            review_observation, review_screen = review_result
            self.log_decision(
                "Post review",
                "Post settings or Post preview with Post text on the enabled blue button",
                f"state={review_observation.state.value}, confidence={review_observation.confidence:.2f}, signals={review_observation.signals or 'none'}",
                "continue to final Post" if review_observation.state == ScreenState.POST_ENABLED else "stop without publishing",
                level="INFO",
            )
            self.capture_evidence(
                "post_review_state",
                review_screen,
                state=review_observation.state.value,
                confidence=review_observation.confidence,
                signals=review_observation.signals,
            )
            if review_observation.state == ScreenState.ERROR_DIALOG:
                return self._fail("post_review_error", "Facebook displayed an error after clicking Next.", review_screen)
            if review_observation.state != ScreenState.POST_ENABLED:
                return self._fail(
                    "post_review_timeout",
                    "The final enabled Post action did not appear after clicking Next.",
                    review_screen,
                )

        self.log("STEP", "Locating a stable enabled Post action...")
        self._requires_review = False
        post_btn = self._stable_post_target(review_confirmed=bool(next_btn))
        self.log_decision(
            "Final publish",
            "Post or Publish text inside the enabled blue action button",
            f"target={post_btn}" if post_btn else "no enabled Post action",
            "click once and verify publication" if post_btn else "stop before publishing",
        )
        if not post_btn:
            if getattr(self, "_requires_review", False):
                return self._needs_review(
                    "semantic_publish_gated",
                    "Final publish action was semantically proposed but requires operator review before execution.",
                )
            return self._fail("post_button_not_found", "Enabled Post button could not be confirmed.")

        self.set_stage("ready_to_publish", target=list(post_btn))
        before_publish = self.client.screenshot()
        self.capture_evidence("before_publish", before_publish, target=list(post_btn))
        self.log("STEP", f"Clicking final Post action once at {post_btn}...")
        if not self.execute_publish_gate(post_btn):
            return self._fail(
                "publish_gate_rejected",
                "The engine-owned publish gate rejected the final action; no click was sent.",
            )

        self.set_stage("verifying")
        publication_status, verification = self._verify_publication(before_publish)
        if verification:
            observation, final_screen, similarity = verification
            self.capture_evidence(
                "publication_verification",
                final_screen,
                state=observation.state.value,
                confidence=observation.confidence,
                signals=observation.signals,
                similarity_to_pre_publish=round(similarity, 5),
            )
        else:
            final_screen = None

        if publication_status == "failed":
            return self._fail("publish_rejected", "Facebook displayed an error after the publish action.", final_screen)
        if publication_status != "published":
            return self._uncertain(
                "publish_unconfirmed",
                "The final Post action was clicked once, but publication could not be positively confirmed. Automatic retry is blocked.",
                final_screen,
            )

        if self.comment_link:
            comment_status, permalink_info = self.post_first_comment_with_page_reuse(
                self.comment_link,
                caption=self.caption,
                media_type="photo" if self.media_path else "post",
            )
            return self.set_outcome(
                "published",
                None,
                first_comment=comment_status,
                first_comment_method=getattr(self, "last_comment_method", None),
                **permalink_info,
            )

        permalink_info = self.permalink_not_requested()
        self.log("INFO", "No first comment requested; skipping permalink correlation and recovery.")
        self.log("SUCCESS", "Facebook publication was visually confirmed.")
        return self.set_outcome(
            "published",
            None,
            first_comment="not_requested",
            **permalink_info,
        )
