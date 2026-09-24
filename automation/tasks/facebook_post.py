"""Guarded visual state machine for Facebook photo/text publishing."""

import random
import time
import math
import re
from engine.screen_state import ScreenState
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
    ):
        super().__init__(profile_id)
        self.caption = caption
        self.comment_link = comment_link
        self.media_path = media_path

    def _fail(self, code: str, message: str, screen=None) -> bool:
        self.log("ERROR", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("failed", code, message=message)

    def _uncertain(self, code: str, message: str, screen=None) -> bool:
        self.log("WARN", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("uncertain", code, message=message)

    def _stable_ocr_target(self, labels, prefer_lower_half=False):
        def locate():
            match = self.vision.find_text(labels, prefer_lower_half=prefer_lower_half)
            return match["center"] if match else None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _stable_blue_text_target(self, labels):
        """Require action OCR text to overlap the enabled blue action region."""
        def locate():
            screen = self.client.screenshot()
            blue = self.vision.find_blue_action_button(
                screen=screen,
                region=(0, screen.shape[0] // 2, screen.shape[1], screen.shape[0] // 2),
            )
            if not blue:
                return None
            text_match = self.vision.find_text(
                labels,
                screen=screen,
                region=(max(0, blue[0] - 260), max(0, blue[1] - 50), 520, 100),
                min_confidence=0.20,
            )
            if not text_match:
                return None
            if math.hypot(text_match["center"][0] - blue[0], text_match["center"][1] - blue[1]) > 180:
                return None
            return blue

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _stable_post_target(self):
        return self._stable_blue_text_target(("post", "publish"))

    def _stable_next_target(self):
        return self._stable_blue_text_target(("next",))

    def _stable_caption_target(self):
        """Locate the caption prompt inside the active composer modal only."""
        def locate():
            screen = self.client.screenshot()
            blue = self.vision.find_blue_action_button(
                screen=screen,
                region=(0, screen.shape[0] // 2, screen.shape[1], screen.shape[0] // 2),
            )
            if not blue:
                return None
            # The caption row is above the modal's bottom CTA. Cropping avoids
            # matching the feed composer or the adjacent AI-label control.
            region = (
                max(0, blue[0] - 280),
                max(0, blue[1] - 540),
                560,
                360,
            )
            candidates = self.vision.read_text(
                screen,
                region=region,
                min_confidence=0.20,
            )
            for item in candidates:
                normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                words = set(normalized.split())
                # OCR commonly renders the apostrophe as 5, $, or whitespace.
                # Requiring all four words inside the modal caption region is
                # strict enough to reject the neighboring AI-label control.
                has_what = "what" in words or "whats" in words or "what5" in words
                if has_what and {"on", "your", "mind"}.issubset(words):
                    return item["center"]
                if "write something" in normalized or "create a public post" in normalized:
                    return item["center"]
            return None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=16.0)

    def _find_first_comment_input(self, screen):
        """Return the top-most Comment as/Write a comment field on the screen."""
        candidates = []
        for item in self.vision.read_text(screen, min_confidence=0.18):
            normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
            words = set(normalized.split())
            if normalized.startswith("comment as") or {"write", "comment"}.issubset(words):
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["center"][1])
        return candidates[0]["center"]

    def _open_profile_first_comment_input(self):
        """Open the active profile and locate the first post card's comment field."""
        self.client.navigate_to("https://www.facebook.com/me")
        time.sleep(3.0)
        posts_section_seen = False

        for scan in range(1, 9):
            screen = self.client.screenshot()
            texts = self.vision.read_text(screen, min_confidence=0.20)
            normalized = [
                re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                for item in texts
            ]
            has_posts = any(text == "posts" or text.startswith("posts ") for text in normalized)
            has_list_view = any("list view" in text for text in normalized)
            if has_posts and has_list_view:
                posts_section_seen = True

            comment_target = self._find_first_comment_input(screen) if posts_section_seen else None
            self.log_decision(
                "Find first post comment",
                "Posts/List view followed by the first Comment as field",
                f"scan={scan}, posts_section={posts_section_seen}, target={comment_target}",
                "use first post comment field" if comment_target else "scroll and scan again",
                level="INFO",
            )
            if comment_target:
                return comment_target, screen

            self.human.scroll("down", notches=3)
            time.sleep(1.0)

        return None, None

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

    def _verify_publication(self, before_publish, timeout: float = 35.0):
        """Confirm publication or return an explicitly ambiguous outcome."""
        deadline = time.time() + timeout
        feed_streak = 0
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

            # A changed screen followed by two stable feed observations means the
            # composer closed and Facebook returned to the feed.
            if observation.state == ScreenState.FEED_READY and similarity < 0.985:
                feed_streak += 1
                if feed_streak >= 2:
                    return "published", last
            else:
                feed_streak = 0
            time.sleep(1.5)
        return "uncertain", last

    def run(self) -> bool:
        self.log("STEP", "Starting Facebook auto-post task...")

        if not self.client.is_running():
            return self._fail("container_stopped", f"Container {self.client.container_name} is not running.")

        # Step 1: Pre-task login gate
        if not self.verify_logged_in():
            return self._fail("session_unverified", "Facebook session is logged out or could not be verified after loading.")

        # Step 3: Handle media attachment (Photo/Video)
        if self.media_path:
            self.log("STEP", "Opening the Create Post composer before attaching media...")
            composer_pos = self._stable_ocr_target((
                "what's on your mind",
                "what’s on your mind",
                "whats on your mind",
                "what s on your mind",
                "write something",
            ))
            self.log_decision(
                "Open composer",
                "stable What's on your mind text",
                f"target={composer_pos}" if composer_pos else "no stable text target",
                "click target and verify Create post modal" if composer_pos else "use the Photo/video shortcut",
            )
            if composer_pos:
                self.human.click(*composer_pos)
                composer_result = self.wait_for_states(
                    {ScreenState.COMPOSER_OPEN, ScreenState.POST_ENABLED},
                    timeout=15.0,
                    poll_interval=1.0,
                )
                if not composer_result or composer_result[0].state not in {
                    ScreenState.COMPOSER_OPEN,
                    ScreenState.POST_ENABLED,
                }:
                    return self._fail(
                        "composer_open_failed",
                        "The Create Post composer did not open after the visual click.",
                        composer_result[1] if composer_result else None,
                    )

            self.log("STEP", f"Opening media picker for: {self.media_path}")
            # Prefer the modal label over a same-colored icon elsewhere on the
            # feed; use visual icon detection only when OCR cannot read it.
            photo_btn = self._stable_ocr_target((
                "photo/video",
                "photo / video",
                "photolvideo",
                "photo video",
                "photos/videos",
            ))
            if not photo_btn:
                photo_btn = self.vision.find_stable(
                    self.vision.find_photo_video_button,
                    attempts=2,
                    tolerance_px=8.0,
                )
            if not photo_btn:
                return self._fail("photo_button_not_found", "Photo/video control could not be located confidently.")

            self.log_decision(
                "Open media picker",
                "stable Photo/video label or green media icon",
                f"target={photo_btn}",
                "click target and require a visible file chooser",
            )
            self.capture_evidence("before_open_media_picker", target=list(photo_btn))
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
                composer_pos = self._stable_ocr_target((
                    "what's on your mind",
                    "what’s on your mind",
                    "whats on your mind",
                    "what s on your mind",
                    "write something",
                ))
            if not composer_pos:
                return self._fail("composer_not_found", "Post composer could not be located confidently.")
            self.human.click(*composer_pos)
            time.sleep(random.uniform(2.0, 3.0))

        # Locate the composer text area after the modal/media preview is ready.
        caption_target = self._stable_caption_target()

        self.log_decision(
            "Enter caption",
            "What's on your mind text inside the modal caption region",
            f"target={caption_target}" if caption_target else "no caption input target",
            "click and enter caption" if caption_target else "stop without typing",
        )

        if caption_target:
            self.human.click(*caption_target)
            time.sleep(0.5)
        elif self.caption:
            return self._fail(
                "caption_input_not_found",
                "Caption input could not be visually confirmed; no fallback coordinate was used.",
            )

        self.log("STEP", f"Entering post caption ({len(self.caption)} chars)...")
        # Use clipboard paste for emojis / long copy, otherwise human type
        if any(ord(c) > 127 for c in self.caption) or len(self.caption) > 80:
            self.paste_text(self.caption)
        else:
            self.human.type_text(self.caption, wpm=random.randint(52, 65))

        # "Reviewing post" hesitation
        review_pause = random.uniform(2.0, 3.5)
        self.log("INFO", f"Human review pause ({review_pause:.1f}s)...")
        time.sleep(review_pause)

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
            self.human.click(*next_btn)
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
        post_btn = self._stable_post_target()
        self.log_decision(
            "Final publish",
            "Post or Publish text inside the enabled blue action button",
            f"target={post_btn}" if post_btn else "no enabled Post action",
            "click once and verify publication" if post_btn else "stop before publishing",
        )
        if not post_btn:
            return self._fail("post_button_not_found", "Enabled Post button could not be confirmed.")

        before_publish = self.client.screenshot()
        self.capture_evidence("before_publish", before_publish, target=list(post_btn))
        self.log("STEP", f"Clicking final Post action once at {post_btn}...")
        self.human.click(*post_btn)

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
                "The publish action was sent once, but publication could not be visually confirmed.",
                final_screen,
            )

        # Step 6: First-comment destination link (if provided)
        if self.comment_link:
            self.log("STEP", "Opening the active profile to comment on its first post...")
            time.sleep(random.uniform(2.0, 3.5))
            comment_box_pos, comment_screen = self._open_profile_first_comment_input()
            if not comment_box_pos:
                self.log_decision(
                    "Submit first comment",
                    "first Comment as field under Posts/List view",
                    "no verified comment field",
                    "keep post published and skip comment",
                    level="WARN",
                )
                return self.set_outcome("published", None, first_comment="failed_input_not_found")

            self.capture_evidence("before_first_comment", comment_screen, target=list(comment_box_pos))
            self.log_decision(
                "Submit first comment",
                "first Comment as field under Posts/List view",
                f"target={comment_box_pos}",
                "click, paste configured comment, and submit once",
            )
            self.human.click(*comment_box_pos)
            time.sleep(1.0)
            self.paste_text(self.comment_link)
            time.sleep(0.5)
            self.human.key_press("Return")
            self.log("SUCCESS", "First comment was submitted once; no automatic retry will be attempted.")
            time.sleep(3.0)
            after_comment = self.client.screenshot()
            self.capture_evidence("after_first_comment", after_comment)
            return self.set_outcome("published", None, first_comment="submitted_unverified")

        self.log("SUCCESS", "Facebook publication was visually confirmed.")
        return self.set_outcome("published")
