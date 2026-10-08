"""Guarded visual workflow for Facebook Reel publishing."""

from __future__ import annotations

import math
from pathlib import Path
import re
import time

from composer_templates import (
    ComposerTemplateDetector,
    ComposerTemplateRegistry,
    RecognitionProfileRegistry,
    TemplateObservation,
    validate_recognition_candidate,
)
from engine.screen_state import ScreenState, StateObservation
from engine.telemetry import timed_telemetry_step
from engine.runtime_paths import automation_dir
from engine.text_matcher import OcrTextMatcher
from engine.vision import VisionEngine
from modules.publication_result_verifier import ReelPublicationResultVerifier
from modules.reel_post_click_verifier import ReelPostClickVerifier
from .base_task import BaseTask


class FacebookReelTask(BaseTask):
    VIDEO_COMPOSER_WAIT_TIMEOUT = 120.0
    T1_PRE_FINAL_COMPOSER_WAIT_TIMEOUT = 90.0
    T1_FINAL_POST_BUTTON_WAIT_TIMEOUT = 120.0
    DEFAULT_FINAL_POST_BUTTON_WAIT_TIMEOUT = 90.0
    POST_CLICK_VERIFICATION_TIMEOUT = 90.0
    POST_CONFIRMATION_POPUP_TIMEOUT = 30.0
    RIGHT_REEL_SIDEBAR = "reel_sidebar"
    LEFT_REEL_SIDEBAR = (0.00, 0.08, 0.34, 1.0)
    LEFT_PUBLICATION_REGION = (0.00, 0.50, 0.40, 1.0)
    REEL_SIDEBAR_REGIONS = (RIGHT_REEL_SIDEBAR, LEFT_REEL_SIDEBAR)
    TEMPLATE_LABELS = {
        "t1": "T1",
        "t2": "T2A",
        "t3": "T2B",
    }
    OCR_PHRASE_MATCH_THRESHOLD = 0.78
    OCR_TEXT_MATCHER = OcrTextMatcher(default_threshold=OCR_PHRASE_MATCH_THRESHOLD)

    def __init__(
        self,
        profile_id: str,
        video_path: str,
        caption: str,
        comment_link: str | None = None,
        brain_package=None,
        preflight_complete: bool = False,
        defer_comment: bool = False,
    ):
        super().__init__(profile_id)
        self.video_path = video_path
        self.caption = caption
        self.comment_link = comment_link
        self.preflight_complete = preflight_complete
        self.defer_comment = defer_comment
        self.brain_metadata = brain_package.metadata() if brain_package is not None else None
        configured = (
            (getattr(self, "profile_config", {}).get("automation") or {})
            .get("reel_template", "auto")
        )
        self.reel_template_selection = (
            configured if configured in {"auto", "t1", "t2", "t3"} else "auto"
        )
        self.reel_template_id = None
        self.template_selection_details = None
        package_root = Path(brain_package.root) if brain_package is not None else (
            automation_dir()
            / "brains" / "facebook_reel" / "bundled_default"
        )
        self.reel_templates = ComposerTemplateRegistry(package_root).load("reel")
        self.reel_entry_profiles = RecognitionProfileRegistry(
            package_root / "routing" / "entry.yaml"
        ).load()
        self.reel_entry_detector = ComposerTemplateDetector()
        self.reel_composer_detector = ComposerTemplateDetector()
        self.reel_entry_route = None
        self.entry_detection_details = None
        self._t2b_final_stage_confirmed = False

    def _reel_template_entry(self, template_id: str) -> str | None:
        required = {rule.signal for rule in self.reel_templates[template_id].required}
        if "reel_studio_surface" in required:
            return "reel_studio"
        if "direct_file_chooser" in required:
            return "direct_file_chooser"
        return None

    def _reel_template_label(self, template_id: str) -> str:
        return self.TEMPLATE_LABELS.get(template_id, template_id.upper())

    def _reel_template_uses_share_review(self, template_id: str) -> bool:
        return any(
            step.get("expect") == "share_review"
            for step in self.reel_templates[template_id].steps
        )

    @staticmethod
    def _result_details(result) -> dict:
        return {
            "selected": result.template.template_id if result.template else None,
            "score": result.score,
            "runner_up_score": result.runner_up_score,
            "candidates": list(result.candidates),
            "outcome": result.outcome,
            "reason": result.reason,
        }

    def _entry_observation(self, entry_status: str) -> TemplateObservation | None:
        if entry_status == "reel_studio":
            return TemplateObservation({
                "reel_studio_surface": 1.0,
                "upload_control": 1.0,
                "studio_sidebar": 0.95,
            })
        if entry_status == "direct_file_chooser":
            return TemplateObservation({"direct_file_chooser": 1.0})
        return None

    def _composer_observation(self, screen) -> TemplateObservation:
        next_share = self._is_reel_next_share_composer(screen)
        final_composer = self._is_reel_final_composer(
            screen,
            allow_layout_fallback=True,
        )
        return TemplateObservation({
            "direct_file_chooser": 1.0,
            "uploaded_media_visible": 0.95,
            "next_share_surface": 0.98 if next_share else 0.0,
            "reel_edit_controls": 0.95 if next_share else 0.0,
            "final_reel_composer": 0.95 if final_composer else 0.0,
        })

    def _detect_reel_entry_route(self, entry_status: str) -> str | None:
        observation = self._entry_observation(entry_status)
        if observation is None:
            return None
        result = self.reel_entry_detector.detect(
            self.reel_entry_profiles.values(),
            [observation, observation],
        )
        self.entry_detection_details = self._result_details(result)
        if result.outcome != "selected" or result.template is None:
            self.log(
                "WARN",
                f"Automatic Reel entry detection failed: {result.outcome} ({result.reason}).",
            )
            return None
        self.reel_entry_route = result.template.template_id
        self.log(
            "INFO",
            f"Reel entry detector selected {self.reel_entry_route!r} "
            f"(confidence={result.score:.2f}).",
        )
        return self.reel_entry_route

    def _select_studio_template(self) -> str | None:
        candidates = [
            template
            for template in self.reel_templates.values()
            if self._reel_template_entry(template.template_id) == "reel_studio"
        ]
        if len(candidates) != 1:
            self.log(
                "WARN",
                "The Studio entry route does not map to exactly one Reel template.",
            )
            return None
        self.reel_template_id = candidates[0].template_id
        self.template_selection_details = {
            "policy": "auto",
            "selected": self.reel_template_id,
            "selected_label": self._reel_template_label(self.reel_template_id),
            "entry_detection": self.entry_detection_details,
            "composer_detection": {"outcome": "not_required"},
        }
        self.log(
            "INFO",
            f"Studio entry selected Reel template {self._reel_template_label(self.reel_template_id)}.",
        )
        return self.reel_template_id

    def _select_manual_template_for_entry(self, entry_status: str) -> str | None:
        selected = self.reel_template_selection
        if selected not in self.reel_templates:
            return None
        expected_entry = self._reel_template_entry(selected)
        if entry_status != expected_entry:
            return None
        self.reel_entry_route = "studio" if entry_status == "reel_studio" else "direct"
        self.reel_template_id = selected
        self.template_selection_details = {
            "policy": "manual",
            "selected": selected,
            "selected_label": self._reel_template_label(selected),
            "entry_detection": {"outcome": "bypassed", "validated": True},
            "composer_detection": {"outcome": "bypassed"},
        }
        self.log(
            "INFO",
            f"Using manually assigned Reel template {self._reel_template_label(selected)}; selection detectors bypassed.",
        )
        return selected

    def _validate_manual_direct_composer(self, screen) -> bool:
        template = self.reel_templates[self.reel_template_id]
        matched, score, reason = validate_recognition_candidate(
            template,
            self._composer_observation(screen),
        )
        self.template_selection_details["composer_validation"] = {
            "matched": matched,
            "score": score,
            "reason": reason,
        }
        return matched

    def _detect_direct_composer_template(self, screen) -> str | None:
        candidates = [
            template
            for template in self.reel_templates.values()
            if self._reel_template_entry(template.template_id) == "direct_file_chooser"
        ]
        observation = self._composer_observation(screen)
        result = self.reel_composer_detector.detect(
            candidates,
            [observation, observation],
        )
        details = self._result_details(result)
        if result.outcome != "selected" or result.template is None:
            self.log(
                "WARN",
                f"Automatic Reel composer detection failed: {result.outcome} ({result.reason}).",
            )
            return None
        self.reel_template_id = result.template.template_id
        self.template_selection_details = {
            "policy": "auto",
            "selected": self.reel_template_id,
            "selected_label": self._reel_template_label(self.reel_template_id),
            "entry_detection": self.entry_detection_details,
            "composer_detection": details,
        }
        self.log(
            "INFO",
            f"Reel composer detector selected {self._reel_template_label(self.reel_template_id)} "
            f"(confidence={result.score:.2f}).",
        )
        return self.reel_template_id

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

    def _failed_after_publish(self, code: str, message: str, screen=None) -> bool:
        """Record a terminal failure after Post without enabling automatic retry."""
        self.log("ERROR", message)
        self.capture_evidence(code, screen, error_code=code, message=message)
        return self.set_outcome("failed_after_publish", code, message=message)

    def _remaining_video_composer_wait(self, requested: float | None = None) -> float:
        """Return time left in this template's shared composer budget."""
        budget = (
            self.T1_PRE_FINAL_COMPOSER_WAIT_TIMEOUT
            if getattr(self, "reel_template_id", None) == "t1"
            else self.VIDEO_COMPOSER_WAIT_TIMEOUT
        )
        started_at = getattr(self, "_video_composer_started_at", None)
        if started_at is None:
            return budget if requested is None else requested
        remaining = max(
            0.0,
            budget - (time.monotonic() - started_at),
        )
        return remaining if requested is None else min(requested, remaining)

    def _video_composer_timeout(self, screen=None) -> bool:
        limit = 90 if getattr(self, "reel_template_id", None) == "t1" else 120
        return self._fail(
            "reel_composer_timeout",
            f"The video upload composer did not become ready within {limit} seconds; stopping this profile so the next profile can run.",
            screen,
        )

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
        normalized = cls.OCR_TEXT_MATCHER.normalize(text)
        words = normalized.split()
        if "mind" in words:
            normalized = " ".join(words[: words.index("mind") + 1])
        return cls.OCR_TEXT_MATCHER.evaluate(
            expected="What's on your mind",
            received=normalized,
            required_tokens=("on", "your", "mind"),
        ).matched

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
            if match:
                self.remember_reversible_click_bounds(match["center"], match.get("bounds"))
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
                            self.remember_reversible_click_bounds(button["center"], button["bounds"])
                            return button["center"]

            # Secondary control: exact Add video label in the left dropzone.
            sidebar = VisionEngine.get_pixel_region(screen.shape, (0.0, 0.05, 0.25, 0.78))
            candidates = self.vision.read_text(screen, region=sidebar, min_confidence=0.20)
            for item in sorted(candidates, key=lambda candidate: candidate["confidence"], reverse=True):
                normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                if normalized in {"add video", "upload video", "select video"}:
                    self.remember_reversible_click_bounds(item["center"], item.get("bounds"))
                    return item["center"]
            return None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _find_stable_enabled_action(
        self,
        labels,
        prefer_lower_half=True,
        region="bottom_action_bar",
        allow_semantic_fallback=True,
        allow_full_screen_fallback=True,
    ):
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
            blue_buttons = self.vision.find_blue_action_buttons(screen=screen, region=search_region)
            if not isinstance(blue_buttons, list) or not blue_buttons:
                return None
            button = blue_buttons[0]
            blue = button["center"]
            x1, y1, x2, y2 = button["bounds"]
            wanted = {self._normalized_ocr_text(label) for label in labels}
            # Action labels are exact after normalization. Scored phrase
            # matching is reserved for supporting page text and must never
            # turn ``Boost Post`` into ``Post``.
            text_match = self.vision.find_text_cascaded(
                labels,
                screen=screen,
                region=(x1, y1, x2 - x1, y2 - y1),
                min_confidence=0.20,
                allow_full_screen_fallback=False,
            )
            if (
                text_match
                and self._normalized_ocr_text(text_match.get("text", "")) in wanted
            ):
                if math.hypot(text_match["center"][0] - blue[0], text_match["center"][1] - blue[1]) <= 180:
                    self.remember_reversible_click_bounds(blue, button["bounds"])
                    return blue

            if not allow_semantic_fallback or not allow_full_screen_fallback:
                return None

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

    def _is_reel_share_surface(self, screen) -> bool:
        """Recognize the final left-side Share review used by newer Reel flows.

        This screen appears only after Next and no longer contains the caption
        editor.  Requiring its heading, audience control, and another review
        option keeps the structural publish fallback away from upload/edit
        screens that can also contain a blue Next button.
        """
        items = self.vision.read_text(
            screen,
            region=self.LEFT_REEL_SIDEBAR,
            min_confidence=0.18,
        )
        normalized = [
            self._normalized_ocr_text(item.get("text", ""))
            for item in items
        ]
        has_share_heading = any(text == "share" for text in normalized)
        has_audience = any(
            self.OCR_TEXT_MATCHER.evaluate(
                expected="Post audience",
                received=text,
                required_tokens=("post", "audience"),
            ).matched
            for text in normalized
        )
        has_review_option = any(
            self.OCR_TEXT_MATCHER.evaluate(
                expected="Remixing and use of original audio",
                received=text,
                threshold=0.75,
                required_tokens=("remixing", "original"),
            ).matched
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Share to groups",
                received=text,
                required_tokens=("share", "groups"),
            ).matched
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Share to story",
                received=text,
                required_tokens=("share", "story"),
            ).matched
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Add to playlist",
                received=text,
                required_tokens=("add", "playlist"),
            ).matched
            or text.startswith("boost post")
            for text in normalized
        )
        return has_share_heading and has_audience and has_review_option

    def _find_exact_reel_publish_action(self, screen):
        """Bind an exact publish label to its enabled blue button."""
        is_t2b = getattr(self, "reel_template_id", None) == "t3"
        labels = ("post",) if is_t2b else ("post", "publish", "share")
        blue_buttons = self.vision.find_blue_action_buttons(
            screen=screen,
            region=self.LEFT_PUBLICATION_REGION,
        )
        if not isinstance(blue_buttons, list) or not blue_buttons:
            return None
        if is_t2b and len(blue_buttons) != 1:
            return None

        for button in blue_buttons:
            x1, y1, x2, y2 = button["bounds"]
            text_match = self.vision.find_text_cascaded(
                labels,
                screen=screen,
                region=(x1, y1, x2 - x1, y2 - y1),
                min_confidence=0.15,
                allow_full_screen_fallback=False,
            )
            if (
                text_match
                and self._normalized_ocr_text(text_match.get("text", ""))
                in set(labels)
            ):
                self.remember_reversible_click_bounds(
                    button["center"],
                    button["bounds"],
                )
                return button["center"]
        return None

    def _find_stable_reel_publish_action(self):
        """Find the same exact enabled publish action on consecutive frames."""
        def locate():
            return self._find_exact_reel_publish_action(self.client.screenshot())

        return self.vision.find_stable(
            locate,
            attempts=2,
            tolerance_px=8.0,
            max_intermittent_misses=1,
        )

    def _observe_direct_reel_publish_gate(self, screen, target) -> StateObservation:
        """Authorize the labeled CTA on Facebook's direct final composer.

        The direct layout does not show the newer ``safe to publish`` status.
        Require the independent final-composer structure, then bind the exact
        requested coordinate to a fresh enabled blue button with an explicit
        Post/Publish/Share label on the same screenshot.
        """
        t2b_final_confirmed = bool(
            getattr(self, "reel_template_id", None) == "t3"
            and getattr(self, "_t2b_final_stage_confirmed", False)
        )
        composer_confirmed = t2b_final_confirmed or self._is_reel_final_composer(
            screen,
            allow_layout_fallback=True,
        )
        if (
            not composer_confirmed
            and getattr(self, "caption", "")
            and getattr(self, "_reel_caption_entered", False)
            and getattr(self, "_reel_description_target", None)
        ):
            sidebar_items = self.vision.read_text(
                screen,
                region=self.LEFT_REEL_SIDEBAR,
                min_confidence=0.18,
            )
            normalized = [
                self._normalized_ocr_text(item.get("text", ""))
                for item in sidebar_items
            ] if isinstance(sidebar_items, list) else []
            has_create_reel = any(
                self.OCR_TEXT_MATCHER.evaluate(
                    expected="Create reel",
                    received=text,
                    required_tokens=("create", "reel"),
                ).matched
                for text in normalized
            )
            caption_confidence = self._reel_caption_match_confidence(screen)
            composer_confirmed = has_create_reel and caption_confidence >= 0.60

        if not composer_confirmed:
            return StateObservation(ScreenState.UNKNOWN, 0.0)

        requested_x, requested_y = target
        current_target = self._find_exact_reel_publish_action(screen)
        if current_target and math.dist(current_target, (requested_x, requested_y)) <= 12.0:
            context = "t2b final stage" if t2b_final_confirmed else "direct reel final composer"
            return StateObservation(
                ScreenState.POST_ENABLED,
                0.99,
                [context, "target-bound exact publish action"],
                [],
            )
        return StateObservation(ScreenState.UNKNOWN, 0.0)

    def _find_explicit_legacy_reel_publish_action(self):
        """Preserve the old labeled Post fallback without semantic guessing."""
        return self._find_stable_enabled_action(
            ("post", "publish", "share"),
            region="bottom_action_bar",
            allow_semantic_fallback=False,
            allow_full_screen_fallback=True,
        )

    def _new_share_post_is_still_pending(self, screen, new_flow: bool) -> bool:
        """Fence refreshes while the new flow is still on its Share review."""
        return bool(
            new_flow
            and screen is not None
            and getattr(self, "_t2b_final_stage_confirmed", False)
            and self._find_exact_reel_publish_action(screen)
        )

    def _reel_safety_observation(self, screen, local_misses: int):
        """Use cheap dialog OCR normally and retain bounded full-screen safety fallback."""
        observation = self.recognizer.observe_safety_gate(screen)
        if observation.state == ScreenState.ERROR_DIALOG:
            return observation
        if local_misses >= 3 and local_misses % 3 == 0:
            return self.recognizer.observe(screen)
        return observation

    def _wait_for_target(
        self,
        locator,
        timeout: float,
        label: str,
        fallback_locator=None,
    ):
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
        local_misses = 0
        while time.time() < deadline:
            screen = self.client.screenshot()
            use_fallback = fallback_locator is not None and local_misses >= 2
            target = fallback_locator() if use_fallback else locator()
            if target:
                self.capture_evidence(label, screen, target=list(target))
                finish("ready")
                return "ready", target, screen
            local_misses += 1
            observation = self._reel_safety_observation(screen, local_misses)
            if observation.state == ScreenState.ERROR_DIALOG:
                finish("error")
                return "error", None, screen
            time.sleep(2.0)
        finish("timeout")
        return "timeout", None, None

    def _wait_for_reel_entry(self, timeout: float = 35.0):
        """Wait for either supported Reel entry flow after the profile action.

        Some Facebook Page variants open the native file chooser immediately.
        Others first open Reel Studio and require an Upload/Add video click.
        The native modal is checked first so page OCR and clicks never target the
        inaccessible Facebook window behind an open chooser.
        """
        started = time.perf_counter()
        deadline = time.time() + timeout
        local_misses = 0
        while time.time() < deadline:
            chooser_window = self.find_visible_file_chooser()
            if chooser_window is not None:
                self._preferred_reel_sidebar_region = self.LEFT_REEL_SIDEBAR
                screen = self.client.screenshot()
                self.capture_evidence(
                    "reel_direct_file_chooser_ready",
                    screen,
                    file_dialog_window=chooser_window[0],
                    matched_dialog_name=chooser_window[1],
                    entry_flow="direct_file_chooser",
                )
                telemetry = getattr(self, "telemetry", None)
                if telemetry is not None:
                    telemetry.record_step(
                        "reel_entry_flow",
                        (time.perf_counter() - started) * 1000.0,
                        outcome="ready",
                        entry_flow="direct_file_chooser",
                    )
                return "direct_file_chooser", chooser_window, screen

            screen = self.client.screenshot()
            upload_target = self._find_reel_upload_target()
            if upload_target:
                self._preferred_reel_sidebar_region = self.LEFT_REEL_SIDEBAR
                self.capture_evidence(
                    "reel_studio_upload_ready",
                    screen,
                    target=list(upload_target),
                    entry_flow="reel_studio",
                )
                telemetry = getattr(self, "telemetry", None)
                if telemetry is not None:
                    telemetry.record_step(
                        "reel_entry_flow",
                        (time.perf_counter() - started) * 1000.0,
                        outcome="ready",
                        entry_flow="reel_studio",
                    )
                return "reel_studio", upload_target, screen
            local_misses += 1
            observation = self._reel_safety_observation(screen, local_misses)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "error", None, screen
            time.sleep(2.0)

        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.record_step(
                "reel_entry_flow",
                (time.perf_counter() - started) * 1000.0,
                outcome="timeout",
            )
        return "timeout", None, None

    def _ordered_reel_sidebar_regions(self, allow_layout_fallback: bool = False):
        """Lock to the selected layout, adding the alternate only after local misses."""
        preferred = getattr(self, "_preferred_reel_sidebar_region", None)
        if preferred not in self.REEL_SIDEBAR_REGIONS:
            return self.REEL_SIDEBAR_REGIONS
        if not allow_layout_fallback:
            return (preferred,)
        return (preferred,) + tuple(
            region for region in self.REEL_SIDEBAR_REGIONS if region != preferred
        )

    def _is_reel_final_composer(self, screen, allow_layout_fallback: bool = False) -> bool:
        """Recognize the final Create reel surface without relying on its Post button."""
        for region in self._ordered_reel_sidebar_regions(allow_layout_fallback):
            items = self.vision.read_text(
                screen,
                region=region,
                min_confidence=0.18,
            )
            normalized = [self._normalized_ocr_text(item.get("text", "")) for item in items]
            description_item = next((
                item for item, text in zip(items, normalized)
                if self.OCR_TEXT_MATCHER.evaluate(
                    expected="Describe your reel…",
                    received=text,
                    required_tokens=("your", "reel"),
                ).matched
                or self.OCR_TEXT_MATCHER.evaluate(
                    expected="Write a caption",
                    received=text,
                    required_tokens=("write", "caption"),
                ).matched
            ), None)
            has_description = description_item is not None or (
                getattr(self, "_reel_caption_entered", False)
                and getattr(self, "_reel_description_target", None) is not None
            )
            has_final_context = any(
                self.OCR_TEXT_MATCHER.evaluate(
                    expected="Uploaded media",
                    received=text,
                    required_tokens=("uploaded", "media"),
                ).matched
                or self.OCR_TEXT_MATCHER.evaluate(
                    expected="Post audience",
                    received=text,
                    required_tokens=("post", "audience"),
                ).matched
                # EasyOCR commonly splits this two-line setting into
                # ``Remixing and use of original`` and ``audio``.  The first
                # line is already specific to the final Reel settings page;
                # evaluate it as a long supporting phrase instead of requiring
                # the trailing word on the same OCR line.
                or self.OCR_TEXT_MATCHER.evaluate(
                    expected="Remixing and use of original audio",
                    received=text,
                    threshold=0.75,
                    required_tokens=("remixing", "original"),
                ).matched
                for text in normalized
            )
            if has_description and has_final_context:
                self._preferred_reel_sidebar_region = region
                description_center = description_item.get("center") if description_item else None
                if description_center:
                    self._reel_description_target = tuple(description_center)
                return True
        return False

    def _is_reel_next_share_composer(self, screen) -> bool:
        """Recognize the new Create reel -> Next -> Share -> Post variant.

        Unlike the existing direct composer, this surface already has the
        caption and uploaded media but still exposes editing tools before a
        separate Share review. Keep this classifier narrow so legacy flows are
        not rerouted.
        """
        items = self.vision.read_text(
            screen,
            region=self.LEFT_REEL_SIDEBAR,
            min_confidence=0.18,
        )
        normalized = [
            self._normalized_ocr_text(item.get("text", ""))
            for item in items
        ]
        has_create_reel = any(
            self.OCR_TEXT_MATCHER.evaluate(
                expected="Create reel",
                received=text,
                required_tokens=("create", "reel"),
            ).matched
            for text in normalized
        )
        has_uploaded_media = any(
            self.OCR_TEXT_MATCHER.evaluate(
                expected="Uploaded media",
                received=text,
                required_tokens=("uploaded", "media"),
            ).matched
            for text in normalized
        )
        has_edit_heading = any(text == "edit" for text in normalized)
        short_controls = ("audio", "crop")
        long_controls = ("closed captions", "audio descriptions", "text transcripts")
        edit_controls = sum(
            1 for marker in short_controls if any(text == marker for text in normalized)
        ) + sum(
            1
            for marker in long_controls
            if any(
                self.OCR_TEXT_MATCHER.evaluate(
                    expected=marker,
                    received=text,
                    required_tokens=tuple(marker.split()),
                ).matched
                for text in normalized
            )
        )
        return has_create_reel and has_uploaded_media and has_edit_heading and edit_controls >= 2

    def _find_reel_description_target(self, allow_full_screen_fallback: bool = False):
        """Locate the caption field in either the left- or right-sidebar layout."""
        remembered_target = getattr(self, "_reel_description_target", None)
        if remembered_target:
            return remembered_target

        labels = (
            "describe your reel",
            "describe your reel...",
            "write a caption",
        )

        def locate():
            screen = self.client.screenshot()
            match = self.vision.find_text_cascaded(
                labels,
                screen=screen,
                region=self.LEFT_REEL_SIDEBAR,
                min_confidence=0.20,
                allow_full_screen_fallback=allow_full_screen_fallback,
            )
            if match:
                return match["center"]

            # OCR commonly drops or substitutes the first character in the
            # placeholder (for example, ``pescribe your reel``). Keep this
            # fallback local to the Reel sidebar and require both context
            # words plus a describe-like suffix before treating it as input.
            sidebar_items = self.vision.read_text(
                screen,
                region=self.LEFT_REEL_SIDEBAR,
                min_confidence=0.18,
            )
            if not isinstance(sidebar_items, list):
                sidebar_items = []
            for item in sidebar_items:
                normalized = self._normalized_ocr_text(item.get("text", ""))
                if (
                    self.OCR_TEXT_MATCHER.evaluate(
                        expected="Describe your reel…",
                        received=normalized,
                        required_tokens=("your", "reel"),
                    ).matched
                    or self.OCR_TEXT_MATCHER.evaluate(
                        expected="Write a caption",
                        received=normalized,
                        required_tokens=("write", "caption"),
                    ).matched
                ) and item.get("center"):
                    return tuple(item["center"])

            # The Uploaded media heading sits immediately below the description
            # textarea in the direct Reel layout. It is a safer anchor than Post
            # audience, whose distance changes dramatically with thumbnails and
            # other composer controls.
            media_label = self.vision.find_text_cascaded(
                ("uploaded media",),
                region=self.LEFT_REEL_SIDEBAR,
                screen=screen,
                min_confidence=0.35,
                allow_full_screen_fallback=allow_full_screen_fallback,
            )
            if media_label:
                mx, my = media_label["center"]
                target = (max(150, mx + 90), max(180, my - 78))
                height, width = screen.shape[:2]
                if target[0] < int(width * 0.32) and target[1] < int(height * 0.48):
                    return target
            return None

        return self.vision.find_stable(locate, attempts=2, tolerance_px=8.0)

    def _direct_reel_media_state(self, screen) -> str:
        """Classify whether the direct-layout Reel has actually accepted media."""
        items = self.vision.read_text(
            screen,
            region=self.LEFT_REEL_SIDEBAR,
            min_confidence=0.18,
        )
        normalized = [self._normalized_ocr_text(item.get("text", "")) for item in items]
        media_attached = any(
            text == "thumbnail"
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Uploaded media",
                received=text,
                required_tokens=("uploaded", "media"),
            ).matched
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Replace media",
                received=text,
                required_tokens=("replace", "media"),
            ).matched
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Checking for copyrighted content",
                received=text,
                required_tokens=("checking", "content"),
            ).matched
            for text in normalized
        )
        if media_attached:
            description_item = next((
                item for item, text in zip(items, normalized)
                if text == "description"
                or self.OCR_TEXT_MATCHER.evaluate(
                    expected="Describe your reel…",
                    received=text,
                    required_tokens=("your", "reel"),
                ).matched
                or self.OCR_TEXT_MATCHER.evaluate(
                    expected="Write a caption",
                    received=text,
                    required_tokens=("write", "caption"),
                ).matched
            ), None)
            if description_item and description_item.get("center"):
                self._reel_description_target = tuple(description_item["center"])
            return "attached"
        if any(
            self.OCR_TEXT_MATCHER.evaluate(
                expected="Upload a video in order to see a preview",
                received=text,
                required_tokens=("upload", "video", "preview"),
            ).matched
            or self.OCR_TEXT_MATCHER.evaluate(
                expected="Or drag and drop",
                received=text,
                required_tokens=("drag", "drop"),
            ).matched
            or text in {"add video", "upload video", "select video"}
            for text in normalized
        ):
            return "missing"
        return "unknown"

    def _ensure_direct_reel_media_attached(
        self,
        file_path: str,
        timeout: float = 45.0,
    ) -> tuple[str, object | None]:
        """Handle direct layouts that request the same file a second time.

        On some profiles the chooser opened from the profile card only starts
        navigation to ``/reels/create``. The loaded composer then opens another
        chooser because no media was carried across. Reattach at most once and
        require visible media evidence before caption input is allowed.
        """
        deadline = time.time() + timeout
        reattached = False
        upload_clicked = False
        last_screen = None
        local_misses = 0
        while time.time() < deadline:
            chooser_window = self.find_visible_file_chooser()
            if chooser_window is not None:
                if reattached:
                    self.log("ERROR", "The Reel file chooser reopened after the bounded second attachment.")
                    return "timeout", last_screen
                self.log(
                    "INFO",
                    "The new Reel layout requested the video again; attaching the same file once more.",
                )
                if not self.attach_file_gtk(file_path, chooser_window=chooser_window):
                    return "attach_failed", last_screen
                reattached = True
                continue

            last_screen = self.client.screenshot()
            media_state = self._direct_reel_media_state(last_screen)
            if media_state == "attached":
                self.capture_evidence(
                    "reel_direct_media_attached",
                    last_screen,
                    second_attachment=reattached,
                )
                return "ready", last_screen

            local_misses += 1

            if media_state == "missing" and not upload_clicked:
                upload_target = self._find_reel_upload_target()
                if upload_target:
                    self.log("INFO", "The Reel layout still requires media; opening its upload control once.")
                    self.click_reversible(upload_target, label="reel_second_upload", max_offset_px=6)
                    upload_clicked = True
                    time.sleep(1.0)
                    continue
            observation = self._reel_safety_observation(last_screen, local_misses)
            if observation.state == ScreenState.ERROR_DIALOG:
                return "error", last_screen
            time.sleep(1.0)

        return "timeout", last_screen

    def _reel_caption_match_confidence(
        self,
        screen,
        allow_layout_fallback: bool = False,
    ) -> float:
        """Measure whether the caption prefix is visible inside its input box."""
        target = self._normalized_ocr_text(self.caption or "")
        target_tokens = [token for token in target.split() if len(token) >= 3]
        if not target_tokens:
            return 1.0

        # Once the description field has been located, never verify against the
        # whole sidebar. Common words in controls such as Post audience or
        # Uploaded media previously produced false positives for long captions.
        anchor = getattr(self, "_reel_description_target", None)
        if anchor:
            height, width = screen.shape[:2]
            anchor_x, anchor_y = anchor
            x1 = max(0, int(anchor_x - width * 0.16))
            y1 = max(0, int(anchor_y - height * 0.07))
            x2 = min(width, int(anchor_x + width * 0.16))
            y2 = min(height, int(anchor_y + height * 0.12))
            regions = [(x1, y1, max(1, x2 - x1), max(1, y2 - y1))]
        else:
            regions = self._ordered_reel_sidebar_regions(allow_layout_fallback)

        # Facebook may leave the editor scrolled either to the beginning or to
        # the caret at the end after a paste. Verify distinct tokens from both
        # ends, but only inside the already confirmed description field.
        leading_tokens = []
        for token in target_tokens:
            if token not in leading_tokens:
                leading_tokens.append(token)
            if len(leading_tokens) >= 6:
                break

        trailing_tokens_reversed = []
        for token in reversed(target_tokens):
            if token not in trailing_tokens_reversed:
                trailing_tokens_reversed.append(token)
            if len(trailing_tokens_reversed) >= 6:
                break
        trailing_tokens = list(reversed(trailing_tokens_reversed))
        probe_groups = [leading_tokens]
        if trailing_tokens != leading_tokens:
            probe_groups.append(trailing_tokens)

        best_confidence = 0.0
        for region in regions:
            items = self.vision.read_text(screen, region=region, min_confidence=0.15)
            visible_parts = []
            for item in items:
                normalized = self._normalized_ocr_text(item.get("text", ""))
                is_placeholder = (
                    self.OCR_TEXT_MATCHER.evaluate(
                        expected="Describe your reel…",
                        received=normalized,
                        required_tokens=("your", "reel"),
                    ).matched
                    or self.OCR_TEXT_MATCHER.evaluate(
                        expected="Write a caption",
                        received=normalized,
                        required_tokens=("write", "caption"),
                    ).matched
                )
                if normalized and not is_placeholder:
                    visible_parts.append(normalized)
            visible_text = " ".join(visible_parts)
            if target in visible_text:
                return 1.0
            visible_tokens = set(visible_text.split())
            confidence = max(
                sum(1 for token in probe_tokens if token in visible_tokens) / len(probe_tokens)
                for probe_tokens in probe_groups
            )
            best_confidence = max(best_confidence, confidence)
        return best_confidence

    def _wait_for_reel_caption(self, timeout: float = 8.0) -> tuple[bool, object, float]:
        deadline = time.time() + timeout
        best_confidence = 0.0
        last_screen = None
        local_misses = 0
        while time.time() < deadline:
            last_screen = self.client.screenshot()
            confidence = self._reel_caption_match_confidence(
                last_screen,
                allow_layout_fallback=local_misses >= 2,
            )
            best_confidence = max(best_confidence, confidence)
            meaningful_tokens = [
                token for token in self._normalized_ocr_text(self.caption or "").split()
                if len(token) >= 3
            ]
            # Short captions must match completely. Longer captions must match
            # most of their distinct leading words inside the editor itself.
            minimum_confidence = 1.0 if len(set(meaningful_tokens)) <= 2 else 0.6
            if confidence >= minimum_confidence:
                return True, last_screen, confidence
            local_misses += 1
            time.sleep(1.0)
        return False, last_screen, best_confidence

    def _enter_reel_caption(self, timeout: float) -> tuple[str, object | None]:
        """Enter and verify the caption while media processing continues.

        ``unavailable`` is intentionally non-fatal so callers can try early on
        the direct-upload layout, then fall back to the required final-composer
        step used by older Reel Studio variants.
        """
        if not self.caption:
            return "skipped", None

        description_status, description, screen = self._wait_for_target(
            lambda: self._find_reel_description_target(False),
            timeout=timeout,
            label="reel_description_ready",
            fallback_locator=lambda: self._find_reel_description_target(True),
        )
        if description_status == "error":
            return "error", screen
        if not description:
            return "unavailable", screen

        # Preserve the confirmed field location before its placeholder is
        # replaced by caption text. Final-composer recognition uses this to
        # distinguish the direct layout from older Next/Edit flows.
        self._reel_description_target = tuple(description)
        self.human.click(*description)
        self.paste_text(self.caption)
        caption_ready, caption_screen, caption_confidence = self._wait_for_reel_caption()
        if not caption_ready:
            self.log(
                "WARN",
                "The Reel caption was not visible after Ctrl+V; retrying once with the X11 primary selection.",
            )
            self.human.click(*description)
            self.human.key_press("ctrl+a")
            self.paste_text(
                self.caption,
                paste_key="shift+Insert",
                selection="primary",
            )
            caption_ready, caption_screen, caption_confidence = self._wait_for_reel_caption()

        self.capture_evidence(
            "reel_caption_verification",
            caption_screen,
            caption_visible=caption_ready,
            caption_match_confidence=round(caption_confidence, 4),
        )
        if not caption_ready:
            return "verification_failed", caption_screen

        self._reel_caption_entered = True
        self.log(
            "INFO",
            f"Reel caption was visibly verified (confidence={caption_confidence:.2f}).",
        )
        return "entered", caption_screen

    def _wait_for_reel_post_upload_transition(
        self,
        timeout: float,
        label: str,
    ):
        """Wait for either an enabled Next action or the final Reel composer."""
        started = time.perf_counter()

        def finish(status: str, transition: str | None = None) -> None:
            telemetry = getattr(self, "telemetry", None)
            if telemetry is not None:
                metadata = {"target": label}
                if transition is not None:
                    metadata["transition"] = transition
                telemetry.record_step(
                    "media_processing" if label == "reel_next_ready" else "reel_edit_transition",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=status,
                    **metadata,
                )

        deadline = time.time() + timeout
        local_misses = 0
        while time.time() < deadline:
            screen = self.client.screenshot()

            expects_share_review = bool(
                getattr(self, "reel_template_id", None)
                and self._reel_template_uses_share_review(self.reel_template_id)
            )
            if expects_share_review:
                # T2B was already selected from its full edit surface before a
                # long caption could scroll those distinguishing controls out
                # of view. On this route, an exact Next remains reversible and
                # must take precedence over legacy final-composer markers.
                final_post = self._find_stable_reel_publish_action()
                if final_post:
                    self._t2b_final_stage_confirmed = True
                    self.capture_evidence(
                        f"{label}_share_review",
                        screen,
                        transition="share_review",
                        target=list(final_post),
                        supporting_share_text=self._is_reel_share_surface(screen),
                    )
                    finish("ready", "share_review")
                    return "final_composer", None, screen

                next_button = self._find_stable_enabled_action(
                    ("next",),
                    region="bottom_action_bar",
                    allow_semantic_fallback=False,
                    allow_full_screen_fallback=local_misses >= 2,
                )
                if next_button:
                    self.capture_evidence(
                        label,
                        screen,
                        target=list(next_button),
                        transition="next_share_flow",
                    )
                    finish("ready", "next_share_flow")
                    return "next", next_button, screen

                local_misses += 1
                observation = self._reel_safety_observation(screen, local_misses)
                if observation.state == ScreenState.ERROR_DIALOG:
                    finish("error")
                    return "error", None, screen
                time.sleep(2.0)
                continue

            if self._is_reel_next_share_composer(screen):
                # New account-06 flow: this is not final even though it has the
                # same caption/media markers as the legacy direct composer.
                next_button = self._find_stable_enabled_action(
                    ("next",),
                    region="bottom_action_bar",
                    allow_semantic_fallback=False,
                    allow_full_screen_fallback=local_misses >= 2,
                )
                if next_button:
                    self.capture_evidence(
                        label,
                        screen,
                        target=list(next_button),
                        transition="next_share_flow",
                    )
                    finish("ready", "next_share_flow")
                    return "next", next_button, screen
                local_misses += 1
                observation = self._reel_safety_observation(screen, local_misses)
                if observation.state == ScreenState.ERROR_DIALOG:
                    finish("error")
                    return "error", None, screen
                time.sleep(2.0)
                continue

            # Existing direct flow remains first and unchanged.
            if self._is_reel_final_composer(
                screen,
                allow_layout_fallback=local_misses >= 2,
            ):
                self.capture_evidence(
                    f"{label}_final_composer",
                    screen,
                    transition="final_composer",
                )
                finish("ready", "final_composer")
                return "final_composer", None, screen

            next_button = self._find_stable_enabled_action(
                ("next",),
                region="bottom_action_bar",
                allow_semantic_fallback=False,
                allow_full_screen_fallback=local_misses >= 2,
            )
            if next_button:
                self.capture_evidence(label, screen, target=list(next_button), transition="next")
                finish("ready", "next")
                return "next", next_button, screen
            local_misses += 1
            observation = self._reel_safety_observation(screen, local_misses)
            if observation.state == ScreenState.ERROR_DIALOG:
                finish("error")
                return "error", None, screen
            time.sleep(2.0)

        finish("timeout")
        return "timeout", None, None

    def _handle_remix_audio_dialog(self, screen, region=None) -> str:
        """Confirm Facebook's post-click Reel audio policy without changing it.

        Returns ``absent``, ``waiting`` (dialog found but Save not confirmed), or
        ``saved``. The selected radio option is deliberately left untouched.
        """
        items = self.vision.read_text(screen, region=region, min_confidence=0.18)
        normalized = [self._normalized_ocr_text(item.get("text", "")) for item in items]
        dialog_found = any(
            self.OCR_TEXT_MATCHER.evaluate(
                expected="Remixing and use of original audio",
                received=text,
                threshold=0.75,
                required_tokens=("remixing", "original"),
            ).matched
            for text in normalized
        ) or (
            any("remixing" in text and "original audio" in text for text in normalized)
            and any("allow others" in text and "remix" in text for text in normalized)
        )
        if not dialog_found:
            return "absent"

        blue_buttons = self.vision.find_blue_action_buttons(
            screen=screen,
            region="bottom_action_bar",
        )
        for button in blue_buttons if isinstance(blue_buttons, list) else []:
            x1, y1, x2, y2 = button["bounds"]
            button_items = self.vision.read_text(
                screen,
                region=(x1, y1, x2 - x1, y2 - y1),
                min_confidence=0.15,
            )
            if not any(
                self._normalized_ocr_text(item.get("text", "")) == "save"
                for item in button_items
            ):
                continue

            target = button["center"]
            self.capture_evidence(
                "before_reel_audio_policy_save",
                screen,
                target=list(target),
                selection_preserved=True,
            )
            self.log(
                "INFO",
                "Detected Reel remix/original-audio policy dialog; preserving the selected option and clicking Save once.",
            )
            self.human.click(*target)
            return "saved"

        self.log(
            "INFO",
            "Detected Reel remix/original-audio policy dialog; waiting for its enabled Save action.",
        )
        return "waiting"

    def _reel_composer_has_closed(self, screen, observation) -> bool:
        return ReelPostClickVerifier(self).composer_has_closed(screen, observation)

    def _allow_reel_profile_navigation(self) -> bool:
        return ReelPostClickVerifier(self).allow_profile_navigation()

    @timed_telemetry_step("publication_verification")
    def _verify_reel_publication(
        self,
        before_publish,
        timeout: float = POST_CLICK_VERIFICATION_TIMEOUT,
        popup_timeout: float = POST_CONFIRMATION_POPUP_TIMEOUT,
    ):
        return ReelPostClickVerifier(self).verify(
            before_publish, timeout=timeout, popup_timeout=popup_timeout,
        )

    def _latest_reel_is_visible(self, screen) -> bool:
        """Check the top profile feed for this Reel's caption or a fresh timestamp."""
        if screen is None:
            return False
        height, width = screen.shape[:2]
        items = self.vision.read_text(
            screen,
            region=(int(width * 0.28), int(height * 0.16), int(width * 0.67), int(height * 0.78)),
            min_confidence=0.18,
        )
        if any(self._is_recent_timestamp_text(item.get("text", "")) for item in items):
            return True

        caption_words = [
            word for word in re.sub(r"[^a-z0-9]+", " ", (self.caption or "").casefold()).split()
            if len(word) >= 3
        ][:8]
        if not caption_words:
            return False
        visible_text = " ".join(item.get("text", "").casefold() for item in items)
        matched = sum(1 for word in caption_words if word in visible_text)
        return matched >= min(2, len(caption_words))

    def _refresh_until_latest_reel_visible(self) -> bool:
        """Delegate template-independent profile verification to its module."""
        return ReelPublicationResultVerifier(self).verify_latest_reel()

    def _scan_profile_for_latest_reel(self, max_scans: int = 6):
        """Scroll from the profile header until the first post has rendered."""
        # Navigating to the same profile URL can preserve Facebook's previous
        # scroll offset, which may leave the newest post's timestamp above the
        # viewport. Reset before the bounded downward scan.
        self.client.exec_cmd(["xdotool", "key", "ctrl+home"], check=False)
        time.sleep(0.8)
        self.client.exec_cmd(["xdotool", "mousemove", "1150", "500"], check=False)
        last_screen = None
        for scan in range(1, max_scans + 1):
            last_screen = self.client.screenshot()
            if self._latest_reel_is_visible(last_screen):
                self.log("INFO", f"Latest Reel found in the profile feed on scan {scan}/{max_scans}.")
                return True, last_screen
            if scan < max_scans:
                self.log("INFO", f"Latest Reel not visible on scan {scan}/{max_scans}; scrolling toward the first post.")
                self.human.scroll("down", notches=2)
                time.sleep(1.2)
        return False, last_screen

    def post_reel_first_comment_after_refresh(self, comment_link: str) -> tuple[str, dict]:
        """Find the newly published Reel after bounded refreshes, then comment once."""
        reel_already_confirmed = getattr(self, "_latest_reel_confirmed_on_profile", False)
        self._latest_reel_confirmed_on_profile = False
        if not reel_already_confirmed and not self._refresh_until_latest_reel_visible():
            self.log(
                "WARN",
                "Latest Reel was not found after two refreshes; skipping submission and marking the first comment incomplete.",
            )
            return "failed_input_not_found", self.permalink_not_requested()

        comment_status = self.post_first_comment(
            comment_link,
            reuse_profile_page=True,
            warm_down_after_submit=False,
        )
        return comment_status, self.permalink_not_requested()

    def run(self) -> bool:
        self.set_stage("preparing")
        self.log("STEP", "Starting guarded Facebook Reel task...")

        if not self.preflight_complete:
            if not self.client.is_running():
                return self._fail("container_stopped", f"Container {self.client.container_name} is not running.")
            if not self.verify_logged_in(target_url="https://www.facebook.com/me"):
                return self.skip_unverified_session()

        self.log("INFO", "Reusing the verified profile page for Reel creation.")

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
        self.click_reversible(reel_button, label="profile_reel", max_offset_px=4)
        time.sleep(2.5)

        self.set_stage("composing")
        self.log("STEP", "Detecting direct file chooser or Reel Studio upload flow...")
        entry_status, entry_target, screen = self._wait_for_reel_entry(timeout=35.0)
        if entry_status == "error":
            return self._fail("reel_studio_error", "Facebook displayed an error in Reel Studio.", screen)
        if entry_status == "timeout" or not entry_target:
            return self._fail(
                "reel_entry_flow_not_recognized",
                "Neither a direct file chooser nor a Reel Studio upload control could be located confidently.",
            )

        # Auto uses the first detector to choose Studio versus Direct. Manual
        # assignments bypass selection and only validate their expected entry.
        if self.reel_template_selection == "auto":
            entry_route = self._detect_reel_entry_route(entry_status)
            if entry_route is None:
                return self._needs_review(
                    "reel_entry_route_not_recognized",
                    "The Reel entry screen did not match a known Studio or Direct route.",
                    screen,
                )
            if entry_route == "studio" and not self._select_studio_template():
                return self._needs_review(
                    "reel_studio_template_ambiguous",
                    "The Studio entry route does not map to one known template.",
                    screen,
                )
        elif not self._select_manual_template_for_entry(entry_status):
            return self._needs_review(
                "reel_template_mismatch",
                f"The observed Reel entry does not match assigned template {self.reel_template_selection!r}.",
                screen,
            )

        container_path = self.video_path
        if not container_path.startswith("/"):
            container_path = f"/data/shared_media/{container_path}"

        chooser_window = None
        if entry_status == "direct_file_chooser":
            chooser_window = entry_target
            self.log("INFO", "Facebook opened the Reel file chooser directly; using the direct-upload flow.")
        else:
            self.log("INFO", "Facebook opened Reel Studio; using the Upload/Add video flow.")
            self.click_reversible(entry_target, label="reel_upload", max_offset_px=6)
            time.sleep(1.5)

        if not self.attach_file_gtk(container_path, chooser_window=chooser_window):
            return self._fail("file_chooser_failed", "The Reel file chooser could not be completed safely.")

        # T1 uses a 90-second shared budget until the final composer and then a
        # separate 120-second Post-button wait. Other templates retain their
        # existing shared 120-second composer budget.
        self._video_composer_started_at = time.monotonic()
        self._reel_caption_entered = False
        if entry_status == "direct_file_chooser":
            self.log("INFO", "Confirming that the new Reel layout accepted the selected video...")
            media_status, media_screen = self._ensure_direct_reel_media_attached(
                container_path,
                timeout=45.0,
            )
            if media_status == "error":
                return self._fail(
                    "reel_processing_error",
                    "Facebook displayed an error while confirming the selected Reel video.",
                    media_screen,
                )
            if media_status == "attach_failed":
                return self._fail(
                    "reel_second_file_chooser_failed",
                    "The new Reel layout requested the video again, but the second chooser could not be completed safely.",
                    media_screen,
                )
            if media_status != "ready":
                return self._fail(
                    "reel_media_not_attached",
                    "Facebook did not show evidence that the selected video was attached; caption entry and publication were stopped.",
                    media_screen,
                )

            if self.reel_template_selection == "auto":
                if self.reel_entry_route != "direct" or not self._detect_direct_composer_template(media_screen):
                    return self._needs_review(
                        "reel_composer_not_recognized",
                        "The Direct Reel composer did not clearly match T2A or T2B.",
                        media_screen,
                    )
            elif not self._validate_manual_direct_composer(media_screen):
                return self._needs_review(
                    "reel_template_mismatch",
                    f"The Direct Reel composer does not match assigned template {self.reel_template_selection!r}.",
                    media_screen,
                )

        if self.caption and entry_status == "direct_file_chooser":
            caption_timeout = self._remaining_video_composer_wait(15.0)
            if caption_timeout <= 0:
                return self._video_composer_timeout(media_screen)
            self.log(
                "STEP",
                "Entering the Reel caption immediately while the video continues processing...",
            )
            early_caption_status, early_caption_screen = self._enter_reel_caption(timeout=caption_timeout)
            if early_caption_status == "error":
                return self._fail(
                    "reel_description_error",
                    "Facebook displayed an error during early caption entry.",
                    early_caption_screen,
                )
            if early_caption_status == "verification_failed":
                return self._fail(
                    "reel_caption_not_entered",
                    "The Reel caption could not be visually verified after two input methods; publication was stopped.",
                    early_caption_screen,
                )
            if early_caption_status == "unavailable":
                self.log(
                    "INFO",
                    "The caption field is not ready yet; continuing the layout transition and retrying on the final composer.",
                )

        direct_next_share_flow = self._reel_template_uses_share_review(
            self.reel_template_id
        )
        if entry_status == "direct_file_chooser" and not direct_next_share_flow:
            # Preserve the existing direct composer behavior exactly.
            self.log(
                "INFO",
                "Direct Reel composer and attached media are already confirmed; skipping the obsolete Next check.",
            )
            next_status, next_button, screen = "final_composer", None, media_screen
        else:
            if direct_next_share_flow:
                self.log(
                    "INFO",
                    "Detected the new Reel Next -> Share -> Post flow; waiting for its enabled Next action.",
                )
            else:
                self.log("INFO", "Waiting for video processing, an enabled Next action, or the final Reel composer...")
            composer_timeout = self._remaining_video_composer_wait()
            if composer_timeout <= 0:
                return self._video_composer_timeout(screen)
            next_status, next_button, screen = self._wait_for_reel_post_upload_transition(
                timeout=composer_timeout,
                label="reel_next_ready",
            )
        if next_status == "error":
            return self._fail("reel_processing_error", "Facebook displayed a Reel processing error.", screen)
        if next_status == "timeout":
            return self._video_composer_timeout(screen)

        if next_status == "next" and next_button:
            self.log("STEP", f"Clicking Next action at {next_button}...")
            self.click_reversible(next_button, label="reel_next", max_offset_px=5)
            time.sleep(2.0)

            # Check for either an intermediate Edit reel Next action or the
            # final composer. Facebook variants do not all include Edit reel.
            edit_timeout = self._remaining_video_composer_wait(15.0)
            if edit_timeout <= 0:
                return self._video_composer_timeout(screen)
            edit_next_status, edit_next_button, screen = self._wait_for_reel_post_upload_transition(
                timeout=edit_timeout,
                label="reel_edit_next_ready",
            )
            if edit_next_status == "error":
                return self._fail("reel_edit_error", "Facebook displayed an error on the Reel edit step.", screen)
            if edit_next_status == "timeout" and self._remaining_video_composer_wait() <= 0:
                return self._video_composer_timeout(screen)
            if edit_next_status == "next" and edit_next_button:
                self.log("STEP", f"Clicking intermediate Next action on Edit reel step at {edit_next_button}...")
                self.click_reversible(edit_next_button, label="reel_edit_next", max_offset_px=5)
                time.sleep(2.0)
        else:
            self.log("INFO", "Facebook skipped the Reel Next/Edit steps and opened the final composer directly.")

        if self.caption and not self._reel_caption_entered:
            caption_timeout = self._remaining_video_composer_wait(30.0)
            if caption_timeout <= 0:
                return self._video_composer_timeout(screen)
            self.log("STEP", "Locating Reel description input...")
            caption_status, caption_screen = self._enter_reel_caption(timeout=caption_timeout)
            if caption_status == "error":
                return self._fail(
                    "reel_description_error",
                    "Facebook displayed an error before caption entry.",
                    caption_screen,
                )
            if caption_status == "unavailable":
                if self._remaining_video_composer_wait() <= 0:
                    return self._video_composer_timeout(caption_screen)
                return self._fail("reel_description_not_found", "Reel description input could not be located confidently.")
            if caption_status == "verification_failed":
                return self._fail(
                    "reel_caption_not_entered",
                    "The Reel caption could not be visually verified after two input methods; publication was stopped.",
                    caption_screen,
                )
        elif self.caption:
            self.log("INFO", "Reel caption was already entered while the video was processing.")
        else:
            self.log("INFO", "No Reel caption requested; skipping the description-field wait.")

        self._requires_review = False
        if self.reel_template_id == "t1":
            publish_timeout = self.T1_FINAL_POST_BUTTON_WAIT_TIMEOUT
            self.log(
                "INFO",
                f"T1 final Reel stage reached; waiting up to {publish_timeout:g} seconds for Post to become enabled.",
            )
        else:
            publish_timeout = self._remaining_video_composer_wait(
                self.DEFAULT_FINAL_POST_BUTTON_WAIT_TIMEOUT
            )
            self.log(
                "INFO",
                f"Waiting up to {publish_timeout:g} seconds for enabled Post / Publish action (background processing / copyright check).",
            )
            if publish_timeout <= 0:
                return self._video_composer_timeout(screen)
        publish_status, publish_button, screen = self._wait_for_target(
            self._find_stable_reel_publish_action,
            timeout=publish_timeout,
            label="reel_publish_ready",
            fallback_locator=self._find_explicit_legacy_reel_publish_action,
        )
        if publish_status == "error":
            return self._fail("reel_publish_error", "Facebook displayed an error before publication.", screen)
        if not publish_button:
            if self.reel_template_id != "t1" and self._remaining_video_composer_wait() <= 0:
                return self._video_composer_timeout(screen)
            if getattr(self, "_requires_review", False):
                return self._needs_review(
                    "semantic_publish_gated",
                    "Enabled Reel Post / Publish action was semantically proposed but requires operator review before execution.",
                    screen,
                )
            if self.reel_template_id == "t1":
                return self._fail(
                    "reel_publish_button_timeout",
                    "The final T1 Reel Post button did not become enabled within 120 seconds.",
                    screen,
                )
            return self._fail("reel_publish_not_found", "Enabled Reel Post / Publish action could not be confirmed.")

        if direct_next_share_flow:
            self._t2b_final_stage_confirmed = True

        self.set_stage("ready_to_publish", target=list(publish_button))
        before_publish = self.client.screenshot()
        self.capture_evidence("before_reel_publish", before_publish, target=list(publish_button))
        self.log("STEP", f"Clicking final Reel Post action once at {publish_button}...")
        if not self.execute_publish_gate(publish_button, publish_kind="reel"):
            return self._fail(
                "reel_publish_gate_rejected",
                "The engine-owned publish gate rejected the final Reel action; no click was sent.",
                screen,
            )

        self.set_stage("verifying")
        upload_timeout = self.POST_CLICK_VERIFICATION_TIMEOUT
        popup_timeout = self.POST_CONFIRMATION_POPUP_TIMEOUT
        self.log(
            "INFO",
            f"Monitoring an active Reel upload for up to {upload_timeout:g} seconds. After the loading composer closes, the success popup gets up to {popup_timeout:g} seconds before profile verification.",
        )
        result, verification = self._verify_reel_publication(
            before_publish,
            timeout=upload_timeout,
            popup_timeout=popup_timeout,
        )
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
            if getattr(getattr(self, "post_publish_prompt", None), "last_status", None) == "failed":
                return self._failed_after_publish(
                    "known_post_publish_prompt_did_not_close",
                    "Not now prompt remained open after 3 click attempts; publication could not be confirmed.",
                    final_screen,
                )
            return self._fail("reel_publish_rejected", "Facebook displayed an error after Reel publication.", final_screen)
        if result == "timed_out":
            return self._uncertain(
                "reel_publish_loading_timeout",
                f"The Reel composer was not confirmed closed within {upload_timeout:g} seconds. The run was stopped without refreshing it.",
                final_screen,
            )
        if result != "published" and self._new_share_post_is_still_pending(
            final_screen,
            direct_next_share_flow,
        ):
            return self._uncertain(
                "reel_share_post_still_visible",
                "The Post action remained visible after the single click attempt; profile refresh was suppressed to preserve the pending Share page.",
                final_screen,
            )
        if self.defer_comment:
            if result != "published":
                self.log(
                    "INFO",
                    "The success popup was absent; deferring profile verification to the Publication Result Verifier module.",
                )
                return self.set_outcome(
                    "pending_profile_verification",
                    None,
                    reel_template=self.reel_template_id,
                    reel_template_selection=self.reel_template_selection,
                    first_comment="deferred",
                )
            self.log("INFO", "Reel publication confirmed; deferring first comment to the Comment module.")
            return self.set_outcome(
                "published",
                None,
                reel_template=self.reel_template_id,
                reel_template_selection=self.reel_template_selection,
                first_comment="deferred",
            )
        if self.comment_link:
            comment_status, permalink_info = self.post_reel_first_comment_after_refresh(
                self.comment_link,
            )
            if self.result_status == "uncertain":
                return False
            if result != "published" and comment_status == "failed_input_not_found":
                return self._failed_after_publish(
                    "reel_publish_not_found_after_verification",
                    "The success popup was absent and the latest Reel was not found after two profile checks separated by 15 seconds.",
                    final_screen,
                )
            else:
                self.log("SUCCESS", "Facebook Reel publication was visually confirmed.")
            return self.set_outcome(
                "published",
                None,
                reel_template=self.reel_template_id,
                reel_template_selection=self.reel_template_selection,
                first_comment=comment_status,
                first_comment_method=getattr(self, "last_comment_method", None),
                **permalink_info,
            )

        if result != "published" and not self._refresh_until_latest_reel_visible():
            if self.result_status == "uncertain":
                return False
            return self._failed_after_publish(
                "reel_publish_not_found_after_verification",
                "The success popup was absent and the latest Reel was not found after two profile checks separated by 15 seconds.",
                final_screen,
            )

        permalink_info = self.permalink_not_requested()
        self.log("INFO", "No first comment requested; skipping permalink correlation and recovery.")
        self.log("SUCCESS", "Facebook Reel publication was visually confirmed.")
        return self.set_outcome(
            "published",
            None,
            reel_template=self.reel_template_id,
            reel_template_selection=self.reel_template_selection,
            first_comment="not_requested",
            **permalink_info,
        )
