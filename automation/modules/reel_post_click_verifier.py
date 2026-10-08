"""Shared post-click prompt, upload, and closure verification for every Reel template.

This module never clicks Post or navigates. It returns profile_fallback only
once the composer has closed; profile navigation must recheck that guard.
"""

from __future__ import annotations

import time

from engine.screen_state import ScreenState


class ReelPostClickVerifier:
    """Own the Not now -> fresh upload wait -> confirmation sequence."""

    def __init__(self, task):
        self.task = task

    def composer_has_closed(self, screen, observation) -> bool:
        """Require feed/confirmation evidence and exclude the full Reel composer."""
        if screen is None or observation.state not in {
            ScreenState.FEED_READY, ScreenState.POST_CONFIRMED,
        }:
            return False
        # The publication gate only sees the bottom-left action area. An overlay
        # can hide those controls while the composer is still open elsewhere.
        items = self.task.vision.read_text(screen, min_confidence=0.15)
        blob = " ".join(self.task._normalized_ocr_text(item.get("text", "")) for item in items)
        composer_markers = (
            "create reel", "reels create", "describe your reel", "uploaded media", "replace media",
            "choose thumbnail", "remixing and use of original audio", "uploading",
            "add whatsapp button", "make it easier to contact you",
            "speak with people directly", "post settings", "post audience",
        )
        if any(marker in blob for marker in composer_markers):
            return False
        if observation.state == ScreenState.POST_CONFIRMED:
            return True
        feed_markers = (
            "manage page", "professional dashboard", "meta business suite",
            "what s on your mind", "whats on your mind", "photo video",
            "your story", "create story",
        )
        # A Facebook logo alone cannot prove that the composer closed.
        return (
            any(marker in blob for marker in feed_markers)
            or all(word in blob.split() for word in ("like", "comment", "share"))
            or all(word in blob.split() for word in ("posts", "friends", "photos"))
        )

    def allow_profile_navigation(self) -> bool:
        """Recheck closure immediately before every profile verification navigation."""
        screen = self.task.client.screenshot()
        observation = self.task.recognizer.observe(screen)
        if self.composer_has_closed(screen, observation):
            return True
        self.task._uncertain(
            "reel_composer_closure_unconfirmed",
            "The Reel composer has not been confirmed closed; profile refresh was suppressed.",
            screen,
        )
        return False

    def verify(
        self,
        before_publish,
        timeout: float = 90.0,
        popup_timeout: float = 30.0,
    ):
        upload_deadline = time.time() + timeout
        popup_deadline = None
        last = None
        prompt_dismissed = False
        unconfirmed_feed_logged = False
        audio_policy_saved = False
        local_misses = 0
        while True:
            now = time.time()
            screen = self.task.client.screenshot()
            # Handle the blocking prompt before expensive publication OCR, from
            # the first poll. Start a fresh upload wait after it closes.
            if not prompt_dismissed:
                prompt_status = self.task.handle_post_publish_prompt(screen)
                if prompt_status == "failed":
                    return "failed", last
                if prompt_status == "dismissed":
                    prompt_dismissed = True
                    now = time.time()
                    upload_deadline = now + timeout
                    popup_deadline = None
                    self.task.log("INFO", "Dismissed post-publish prompt ('Not now').")
                    self.task.log("INFO", f"Starting a fresh {timeout:g}-second composer/upload wait after Not now closed.")
                    screen = self.task.client.screenshot()

            observation = self.task.recognizer.observe_publication_gate(
                screen,
                region=self.task.LEFT_PUBLICATION_REGION,
            )
            if observation.state == ScreenState.UNKNOWN:
                broad_observation = self.task.recognizer.observe(screen)
                if broad_observation.state != ScreenState.UNKNOWN:
                    observation = broad_observation
            similarity = self.task.vision.similarity(before_publish, screen)
            last = (observation, screen, similarity)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "failed", last
            composer_closed = self.composer_has_closed(screen, observation)
            if observation.state == ScreenState.POST_CONFIRMED and composer_closed:
                self.task.log("INFO", "Facebook showed Reel publication confirmation.")
                return "published", last

            # Facebook can insert this settings step after the final Post click.
            # It is part of the same publication attempt, so confirm the current
            # selection once and continue verification without clicking Post again.
            should_check_audio_policy = local_misses in {1, 4}
            if not audio_policy_saved and should_check_audio_policy:
                audio_policy_status = self.task._handle_remix_audio_dialog(
                    screen,
                    region="composer_modal",
                )
                if audio_policy_status == "saved":
                    audio_policy_saved = True
                    time.sleep(2.0)
                    continue
                if audio_policy_status == "waiting":
                    time.sleep(2.0)
                    continue

            local_misses += 1
            if not composer_closed and now >= upload_deadline:
                self.task.log("WARN", f"Reel composer closure was not confirmed within {timeout:g} seconds; stopping without refreshing it.")
                return "timed_out", last
            if composer_closed and popup_deadline is not None and now >= popup_deadline:
                self.task.log("WARN", f"Reel success popup was not detected within {popup_timeout:g} seconds after the loading composer closed; checking the profile feed.")
                return "profile_fallback", last
            if not composer_closed:
                popup_deadline = None
                time.sleep(2.0)
                continue
            if observation.state == ScreenState.FEED_READY:
                if not unconfirmed_feed_logged:
                    self.task.log(
                        "INFO",
                        f"The loading composer closed and the profile feed is visible; waiting up to {popup_timeout:g} seconds for the Reel success popup.",
                    )
                    unconfirmed_feed_logged = True
                if popup_deadline is None:
                    popup_deadline = now + popup_timeout
            time.sleep(2.0)
