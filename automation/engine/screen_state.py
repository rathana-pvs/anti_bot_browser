"""Facebook screen-state classification from screenshots and OCR signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import re

import numpy as np

from .vision import VisionEngine


class ScreenState(str, Enum):
    LOGIN_REQUIRED = "login_required"
    FEED_READY = "feed_ready"
    COMPOSER_OPEN = "composer_open"
    MEDIA_UPLOADING = "media_uploading"
    MEDIA_READY = "media_ready"
    POST_ENABLED = "post_enabled"
    PUBLISHING = "publishing"
    POST_CONFIRMED = "post_confirmed"
    ERROR_DIALOG = "error_dialog"
    UNKNOWN = "unknown"


@dataclass
class StateObservation:
    state: ScreenState
    confidence: float
    signals: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


class FacebookStateRecognizer:
    """Classify important posting screens using several visual signals."""

    LOGIN_TEXT = ("log in", "forgot password", "create new account")
    ERROR_TEXT = (
        "something went wrong",
        "couldn't post",
        "could not post",
        "upload failed",
        "try again",
    )
    BLOCKING_TEXT = (
        "captcha",
        "security check",
        "verify your account",
        "confirm your identity",
        "account restricted",
        "account suspended",
        "account disabled",
        "community standards",
    )
    CONFIRMED_TEXT = (
        "your post is now published",
        "post published",
        "your post was shared",
        "post shared",
        "your reel is being processed",
        "your reel was published",
        "reel published",
    )

    def __init__(self, vision: VisionEngine):
        self.vision = vision

    @staticmethod
    def _contains(text_blob: str, phrases: tuple[str, ...]) -> list[str]:
        return [phrase for phrase in phrases if phrase in text_blob]

    def observe(self, screen: np.ndarray | None = None) -> StateObservation:
        screen = self.vision.capture_screen() if screen is None else screen
        ocr = self.vision.read_text(screen, min_confidence=0.35)
        texts = [item["text"] for item in ocr]
        blob = " ".join(texts).casefold()

        leave_matches = self._contains(blob, ("leave site", "changes you made", "may not be saved"))
        if leave_matches:
            return StateObservation(ScreenState.UNKNOWN, 0.95, ["leave_site_dialog", *leave_matches], texts)

        matches = self._contains(blob, self.BLOCKING_TEXT)
        if matches:
            return StateObservation(ScreenState.ERROR_DIALOG, 0.99, matches, texts)

        matches = self._contains(blob, self.ERROR_TEXT)
        if matches:
            return StateObservation(ScreenState.ERROR_DIALOG, 0.98, matches, texts)

        matches = self._contains(blob, self.LOGIN_TEXT)
        if matches:
            return StateObservation(ScreenState.LOGIN_REQUIRED, 0.97, matches, texts)

        matches = self._contains(blob, self.CONFIRMED_TEXT)
        if matches:
            return StateObservation(ScreenState.POST_CONFIRMED, 0.98, matches, texts)

        normalized_items = {
            re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
            for item in ocr
        }
        publishing_statuses = {
            "publishing",
            "posting",
            "publishing post",
            "posting post",
            "publishing your post",
            "posting your post",
        }
        if normalized_items.intersection(publishing_statuses):
            return StateObservation(ScreenState.PUBLISHING, 0.90, ["publishing text"], texts)

        upload_statuses = {
            "uploading",
            "processing",
            "uploading media",
            "processing media",
            "uploading video",
            "processing video",
        }
        if normalized_items.intersection(upload_statuses):
            return StateObservation(ScreenState.MEDIA_UPLOADING, 0.88, ["upload progress text"], texts)

        COMPOSER_PHRASES = (
            "create post",
            "what's on your mind",
            "what’s on your mind",
            "whats on your mind",
            "what s on your mind",
            "write something",
        )
        create_post_title = COMPOSER_PHRASES[0] in blob
        if not create_post_title:
            create_items = [
                it for it in ocr
                if "create" in it["text"].casefold().strip().split()
                and it["center"][1] <= screen.shape[0] * 0.6
            ]
            post_items = [
                it for it in ocr
                if "post" in it["text"].casefold().strip().split()
                and it["center"][1] <= screen.shape[0] * 0.6
            ]
            for c_it in create_items:
                for p_it in post_items:
                    if (
                        abs(c_it["center"][1] - p_it["center"][1]) <= 25
                        and 0 < p_it["center"][0] - c_it["center"][0] <= 140
                    ):
                        create_post_title = True
                        break
                if create_post_title:
                    break

        review_phrases = (
            "post settings",
            "post preview",
            "post audience",
            "scheduling options",
        )
        review_matches = self._contains(blob, review_phrases)
        review_open = "post settings" in blob or len(review_matches) >= 2
        post_button = None
        next_button = None

        has_composer_modal_markers = (
            create_post_title
            or "ai label" in blob
            or "add to your post" in blob
            or any("add to your post" in it["text"].casefold() for it in ocr)
        )
        composer_open = has_composer_modal_markers

        blue_candidates = self.vision.find_blue_action_buttons(
            screen=screen,
            region=(0, screen.shape[0] // 2, screen.shape[1], screen.shape[0] // 2),
        )
        # Some test doubles and older VisionEngine implementations expose only
        # the center API. They can still use OCR matching, but not the structural
        # review invariant because uniqueness is then unknown.
        uniqueness_known = isinstance(blue_candidates, list)
        if not uniqueness_known:
            blue_candidates = []
        if blue_candidates:
            primary_blue = blue_candidates[0]
        else:
            legacy_center = self.vision.find_blue_action_button(
                screen=screen,
                region=(0, screen.shape[0] // 2, screen.shape[1], screen.shape[0] // 2),
            )
            primary_blue = (
                {
                    "center": legacy_center,
                    "bounds": (
                        max(0, legacy_center[0] - 100),
                        max(0, legacy_center[1] - 30),
                        min(screen.shape[1], legacy_center[0] + 100),
                        min(screen.shape[0], legacy_center[1] + 30),
                    ),
                }
                if legacy_center
                else None
            )
        blue_button = primary_blue["center"] if primary_blue else None
        # Full-screen OCR can miss small white CTA text after the screenshot is
        # downscaled. Re-read only the blue action area at native resolution using
        # a tight crop to avoid picking up text from the background feed.
        if primary_blue:
            x1, y1, x2, y2 = primary_blue["bounds"]
            button_region = (x1, y1, x2 - x1, y2 - y1)
            cands = self.vision.read_text(screen, region=button_region, min_confidence=0.15)
            for cand in cands:
                cand_words = [w for w in re.sub(r"[^a-z0-9]+", " ", cand["text"].casefold()).split() if w]
                if any(w in ("post", "publish") for w in cand_words):
                    if post_button is None or cand["confidence"] > post_button["confidence"]:
                        post_button = cand
                elif any(w == "next" for w in cand_words):
                    if next_button is None or cand["confidence"] > next_button["confidence"]:
                        next_button = cand

        # OCR may miss white CTA text. Only a strongly identified review modal
        # with exactly one blue footer action may use the structural invariant.
        structural_review_action = bool(
            review_open
            and uniqueness_known
            and len(blue_candidates) == 1
            and blue_button
        )
        if structural_review_action and not post_button:
            post_button = {
                "text": "Post (review-modal invariant)",
                "center": blue_button,
                "confidence": 0.70,
            }

        post_is_blue = bool(
            post_button
            and blue_button
            and math.hypot(
                post_button["center"][0] - blue_button[0],
                post_button["center"][1] - blue_button[1],
            ) <= 140
        )
        next_is_blue = bool(
            next_button
            and blue_button
            and math.hypot(
                next_button["center"][0] - blue_button[0],
                next_button["center"][1] - blue_button[1],
            ) <= 140
        )
        if next_is_blue or (post_is_blue and ("edit" in blob or "add to your post" in blob)):
            composer_open = True
        if (composer_open or review_open) and post_is_blue:
            return StateObservation(
                ScreenState.POST_ENABLED,
                min(0.95, 0.60 + post_button["confidence"] * 0.35),
                [
                    "post settings review" if review_open else "create post modal",
                    f"action text: {post_button['text']}",
                    "enabled blue action",
                    *(["unique review-modal footer action"] if structural_review_action else []),
                ],
                texts,
            )
        if composer_open and next_is_blue:
            return StateObservation(
                ScreenState.MEDIA_READY,
                min(0.95, 0.60 + next_button["confidence"] * 0.35),
                ["create post modal", "action text: Next", "enabled blue action"],
                texts,
            )
        if composer_open or review_open:
            return StateObservation(
                ScreenState.COMPOSER_OPEN,
                0.82,
                ["post settings review" if review_open else "composer text"],
                texts,
            )

        PHOTO_VIDEO_PHRASES = (
            "photo/video",
            "photo / video",
            "photolvideo",
            "photo video",
            "photo/ video",
            "photos/videos",
        )
        if any(phrase in blob for phrase in PHOTO_VIDEO_PHRASES):
            return StateObservation(ScreenState.FEED_READY, 0.84, ["photo/video text"], texts)

        LOGGED_IN_PHRASES = (
            "manage page",
            "professional dashboard",
            "meta business suite",
            "your story",
            "create story",
            "whats on your mind",
            "what s on your mind",
            "what's on your mind",
            "what’s on your mind",
        )
        if any(phrase in blob for phrase in LOGGED_IN_PHRASES):
            return StateObservation(ScreenState.FEED_READY, 0.80, ["logged-in dashboard/feed text"], texts)

        if self.vision.find_photo_video_button(screen=screen) is not None:
            return StateObservation(ScreenState.FEED_READY, 0.75, ["photo/video button visual"], texts)

        if self.vision.find_template(screen, "facebook_logo", threshold=0.45):
            return StateObservation(ScreenState.FEED_READY, 0.68, ["facebook logo"], texts)

        return StateObservation(ScreenState.UNKNOWN, 0.0, [], texts)

    def observe_session_gate(self, screen: np.ndarray | None = None) -> StateObservation:
        """Classify authentication safety from a targeted crop before full-screen fallback."""
        screen = self.vision.capture_screen() if screen is None else screen
        ocr = self.vision.read_text(screen, region="session_gate", min_confidence=0.30)
        texts = [item["text"] for item in ocr]
        blob = " ".join(texts).casefold()

        leave_matches = self._contains(blob, ("leave site", "changes you made", "may not be saved"))
        if leave_matches:
            return StateObservation(ScreenState.UNKNOWN, 0.95, ["leave_site_dialog", *leave_matches], texts)

        matches = self._contains(blob, self.BLOCKING_TEXT)
        if matches:
            return StateObservation(ScreenState.ERROR_DIALOG, 0.99, matches, texts)
        matches = self._contains(blob, self.ERROR_TEXT)
        if matches:
            return StateObservation(ScreenState.ERROR_DIALOG, 0.98, matches, texts)
        matches = self._contains(blob, self.LOGIN_TEXT)
        if matches:
            return StateObservation(ScreenState.LOGIN_REQUIRED, 0.97, matches, texts)

        feed_phrases = (
            "what's on your mind",
            "what’s on your mind",
            "whats on your mind",
            "what s on your mind",
            "photo/video",
            "photo / video",
            "create story",
            "your story",
            "professional dashboard",
        )
        matches = self._contains(blob, feed_phrases)
        if matches:
            return StateObservation(ScreenState.FEED_READY, 0.84, matches, texts)
        return StateObservation(ScreenState.UNKNOWN, 0.0, [], texts)
