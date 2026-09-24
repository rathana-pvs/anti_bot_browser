"""Facebook screen-state classification from screenshots and OCR signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math

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

        matches = self._contains(blob, self.ERROR_TEXT)
        if matches:
            return StateObservation(ScreenState.ERROR_DIALOG, 0.98, matches, texts)

        matches = self._contains(blob, self.LOGIN_TEXT)
        if matches:
            return StateObservation(ScreenState.LOGIN_REQUIRED, 0.97, matches, texts)

        matches = self._contains(blob, self.CONFIRMED_TEXT)
        if matches:
            return StateObservation(ScreenState.POST_CONFIRMED, 0.98, matches, texts)

        if "publishing" in blob or "posting" in blob:
            return StateObservation(ScreenState.PUBLISHING, 0.90, ["publishing text"], texts)

        if "uploading" in blob or "processing" in blob:
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
        for item in ocr:
            normalized = item["text"].casefold().strip()
            if (
                normalized in ("post", "publish")
                and item["confidence"] >= 0.45
                and item["center"][1] >= screen.shape[0] // 2
            ):
                if post_button is None or item["confidence"] > post_button["confidence"]:
                    post_button = item
            if (
                normalized == "next"
                and item["confidence"] >= 0.45
                and item["center"][1] >= screen.shape[0] // 2
            ):
                if next_button is None or item["confidence"] > next_button["confidence"]:
                    next_button = item
        # The feed itself contains "What's on your mind" and can contain
        # unrelated Post labels. Only the actual modal title proves it opened.
        composer_open = create_post_title
        blue_button = self.vision.find_blue_action_button(
            screen=screen,
            region=(0, screen.shape[0] // 2, screen.shape[1], screen.shape[0] // 2),
        )
        # Full-screen OCR can miss small white CTA text after the screenshot is
        # downscaled. Re-read only the blue action area at native resolution.
        if (composer_open or review_open) and blue_button:
            bx, by = blue_button
            focused_action = self.vision.find_text(
                ("next", "post", "publish"),
                screen=screen,
                region=(max(0, bx - 260), max(0, by - 50), 520, 100),
                min_confidence=0.20,
            )
            if focused_action:
                normalized = focused_action["text"].casefold().strip()
                if normalized == "next":
                    next_button = focused_action
                elif normalized in ("post", "publish"):
                    post_button = focused_action
        post_is_blue = bool(
            post_button
            and blue_button
            and math.hypot(
                post_button["center"][0] - blue_button[0],
                post_button["center"][1] - blue_button[1],
            ) <= 180
        )
        next_is_blue = bool(
            next_button
            and blue_button
            and math.hypot(
                next_button["center"][0] - blue_button[0],
                next_button["center"][1] - blue_button[1],
            ) <= 180
        )
        if (composer_open or review_open) and post_is_blue:
            return StateObservation(
                ScreenState.POST_ENABLED,
                min(0.95, 0.60 + post_button["confidence"] * 0.35),
                [
                    "post settings review" if review_open else "create post modal",
                    f"action text: {post_button['text']}",
                    "enabled blue action",
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
        if composer_open:
            return StateObservation(ScreenState.COMPOSER_OPEN, 0.82, ["composer text"], texts)

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
            "stories",
            "reels",
            "photos",
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
