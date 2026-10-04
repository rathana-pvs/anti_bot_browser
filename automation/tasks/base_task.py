"""Base Task Class for Browser Automation."""

import json
import math
import os
import random
import re
import time
import urllib.parse
import uuid
import numpy as np
from datetime import datetime, timezone
from engine.container_client import ContainerClient
from engine.evidence import EvidenceRecorder
from engine.human_input import HumanInput
from engine.behavior_profile import load_behavior_session
from engine.post_publish_prompt import PostPublishPromptHandler
from engine.screen_state import FacebookStateRecognizer, ScreenState, StateObservation
from engine.vision import VisionEngine
from engine.semantic_fallback import (
    EXPECTED_ACTION_STATES,
    SemanticFallbackEngine,
    collect_action_candidates,
)
from engine.telemetry import TelemetryRecorder, timed_telemetry_step


class BaseTask:
    FILE_CHOOSER_DIALOG_NAMES = (
        "Open File",
        "File Upload",
        "Select a File",
        "Choose File",
        "^Open$",
    )

    WARMING_SURFACES = {
        "news_feed": "https://www.facebook.com/",
        "profile": "https://www.facebook.com/me",
    }

    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.client = ContainerClient(profile_id)
        self.behavior_session = load_behavior_session(
            profile_id,
            f"{self.__class__.__name__}:{uuid.uuid4().hex}",
        )
        self.human = HumanInput(self.client, self.behavior_session)
        self.vision = VisionEngine(self.client)
        self.recognizer = FacebookStateRecognizer(self.vision)
        self.evidence = EvidenceRecorder(profile_id, self.__class__.__name__)
        self.telemetry = TelemetryRecorder(
            profile_id,
            self.__class__.__name__,
            self.evidence.directory,
        )
        self.telemetry.record_step(
            "behavior_session",
            0.0,
            outcome="configured",
            **self.behavior_session.summary(),
        )
        self.vision.telemetry = self.telemetry
        self.semantic_fallback = SemanticFallbackEngine()
        self.post_publish_prompt = PostPublishPromptHandler(self)
        self.logs: list[dict] = []
        self.result_status = "running"
        self.result_error: str | None = None
        self.result_extra: dict = {}
        self.session_check_status = "not_checked"
        self.current_stage: str = "pending"
        self.stage_history: list[dict] = []
        self._reversible_click_bounds: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        self._load_environment_context()

    def _load_environment_context(self) -> None:
        """Load stable profile context; runtime screen observations fill the remaining fields."""
        config_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "profiles", self.profile_id, "config.json")
        )
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                config = json.load(handle)
            self.profile_config = config if isinstance(config, dict) else {}
            fingerprint = config.get("fingerprint", {})
            browser_zoom = config.get("browser_zoom")
            self.telemetry.set_environment(
                configured_screen_resolution=fingerprint.get("screen_resolution"),
                locale=fingerprint.get("language"),
                browser_zoom=browser_zoom,
            )
            self.vision.set_runtime_context(
                configured_resolution=fingerprint.get("screen_resolution"),
                locale=fingerprint.get("language"),
                browser_zoom=browser_zoom,
            )
        except Exception:
            self.profile_config = {}
            pass

    def detect_visual_theme(self, screen=None) -> tuple[str, float]:
        """Run visual theme preflight before any Facebook workflow locators."""
        vision = getattr(self, "vision", None)
        if vision is None or not hasattr(vision, "detect_theme"):
            return "unknown", 0.0
        try:
            screen = self.client.screenshot() if screen is None else screen
            theme, confidence = vision.detect_theme(screen)
            self.log("INFO", f"Visual preflight theme={theme}, confidence={confidence:.2f}")
            return theme, confidence
        except Exception as exc:
            self.log("WARN", f"Visual theme preflight failed; using theme-neutral locators: {exc}")
            return "unknown", 0.0

    def set_stage(self, stage: str, **metadata) -> None:
        """Atomically transition execution stage and persist progress."""
        self.current_stage = stage
        entry = {
            "stage": stage,
            "timestamp": datetime.now().isoformat(),
            **metadata,
        }
        if not hasattr(self, "stage_history"):
            self.stage_history = []
        self.stage_history.append(entry)
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.record_stage(stage)
        self.log("STAGE", f"Execution stage: {stage}")
        try:
            if hasattr(self, "evidence") and self.evidence:
                self.evidence.record_stage(stage, self.stage_history)
        except Exception as exc:
            self.log("WARN", f"Could not record stage '{stage}': {exc}")

    def execute_publish_gate(
        self,
        target: tuple[int, int],
        *,
        publish_kind: str = "post",
    ) -> bool:
        """Execute the irreversible publish click through an engine-owned gate.

        Workflow code can request publication, but only this host method may
        cross the publish boundary.  It re-observes the screen, rejects stale or
        out-of-viewport coordinates, and permits at most one click per task.
        """
        if self.current_stage != "ready_to_publish":
            self.log("WARN", f"Publish gate rejected invalid stage: {self.current_stage}")
            return False
        if getattr(self, "_publish_gate_consumed", False):
            self.log("WARN", "Publish gate rejected a duplicate publish request.")
            return False

        screen = self.client.screenshot()
        height, width = screen.shape[:2]
        x, y = int(target[0]), int(target[1])
        if not (0 <= x < width and 0 <= y < height):
            self.log("WARN", f"Publish gate rejected out-of-viewport target: {(x, y)}")
            return False

        if publish_kind == "post":
            observation = self.recognizer.observe(screen)
        elif publish_kind == "reel":
            observation = self.recognizer.observe_publication_gate(
                screen,
                region=getattr(self, "LEFT_PUBLICATION_REGION", None),
            )
            if observation.state == ScreenState.UNKNOWN:
                direct_gate = getattr(self, "_observe_direct_reel_publish_gate", None)
                if callable(direct_gate):
                    observation = direct_gate(screen, (x, y))
        else:
            self.log("WARN", f"Publish gate rejected unknown publish kind: {publish_kind}")
            return False
        if observation.state != ScreenState.POST_ENABLED:
            self.log(
                "WARN",
                "Publish gate rejected stale or unsafe screen state: "
                f"{observation.state.value} ({observation.confidence:.2f})",
            )
            self.capture_evidence(
                "publish_gate_rejected",
                screen,
                target=[x, y],
                state=observation.state.value,
                confidence=observation.confidence,
            )
            return False

        self._publish_gate_consumed = True
        self.set_stage(
            "publish_authorized",
            target=[x, y],
            observation_state=observation.state.value,
            observation_confidence=observation.confidence,
            publish_kind=publish_kind,
        )
        # Set publish_clicked before the input call. If the actuator raises or
        # the process dies, outer recovery must classify the result uncertain.
        self.set_stage("publish_clicked", target=[x, y])
        self.human.click(x, y)
        return True

    def log(self, level: str, message: str) -> None:
        """Record a structured log entry."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "profile_id": self.profile_id,
            "level": level.upper(),
            "message": message,
        }
        self.logs.append(entry)
        print(f"[{entry['timestamp']}] [{self.profile_id}] [{entry['level']}] {message}", flush=True)

    def record_locator_telemetry(self, *args, **kwargs) -> None:
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.record_locator(*args, **kwargs)

    def log_decision(
        self,
        step: str,
        looking_for: str,
        found: str,
        decision: str,
        level: str = "STEP",
    ) -> None:
        """Write one compact, UI-friendly decision record on a single stream line."""
        message = (
            f"{step}:\\n"
            f"- what it is looking for: {looking_for}\\n"
            f"- what it found: {found}\\n"
            f"- decision: {decision}"
        )
        self.log(level, message)

    def capture_evidence(self, label: str, screen=None, **metadata) -> str | None:
        """Capture diagnostic evidence without allowing evidence I/O to abort a task."""
        try:
            screen = self.client.screenshot() if screen is None else screen
            path = self.evidence.capture(label, screen, metadata or None)
            self.log("DEBUG", f"Evidence captured: {path}")
            return path
        except Exception as exc:
            self.log("WARN", f"Could not capture evidence '{label}': {exc}")
            return None

    def remember_reversible_click_bounds(self, target, bounds) -> None:
        """Associate a stable target with its visually confirmed control bounds."""
        if not target or not bounds or len(bounds) != 4:
            return
        x1, y1, x2, y2 = (int(value) for value in bounds)
        if x2 <= x1 or y2 <= y1:
            return
        registry = getattr(self, "_reversible_click_bounds", None)
        if registry is None:
            registry = {}
            self._reversible_click_bounds = registry
        registry[(int(target[0]), int(target[1]))] = (x1, y1, x2, y2)

    def click_reversible(
        self,
        target: tuple[int, int],
        *,
        label: str,
        bounds: tuple[int, int, int, int] | None = None,
        max_offset_px: int = 6,
    ) -> tuple[int, int]:
        """Click a reversible control with conservative, bounds-aware variation."""
        target = (int(target[0]), int(target[1]))
        if bounds is None:
            registry = getattr(self, "_reversible_click_bounds", {})
            bounds = registry.get(target)
            if bounds is None and registry:
                nearby = [
                    (math.hypot(point[0] - target[0], point[1] - target[1]), candidate_bounds)
                    for point, candidate_bounds in registry.items()
                ]
                distance, candidate_bounds = min(nearby, key=lambda item: item[0])
                if distance <= 10.0:
                    bounds = candidate_bounds

        actual = target
        if bounds is not None:
            actual = HumanInput.safe_click_point(bounds, max_offset_px=max_offset_px)
            self.log(
                "DEBUG",
                f"Safe reversible click '{label}': bounds={tuple(bounds)}, "
                f"detected_target={target}, final_target={actual}",
            )
        else:
            self.log(
                "DEBUG",
                f"Exact reversible click '{label}': no confirmed bounds for target={target}",
            )
        self.human.click(*actual)
        return actual

    def set_outcome(self, status: str, error: str | None = None, **extra) -> bool:
        """Persist a final outcome and return whether it represents publication."""
        self.result_status = status
        self.result_error = error
        self.set_stage(status, error=error, **extra)
        self.result_extra = extra
        if getattr(self, "_pipeline_defer_finalization", False):
            return status in {"published", "completed"}
        self.finalize_outcome()
        return status in {"published", "completed"}

    def finalize_outcome(self, **extra) -> None:
        """Write the final task record once all pipeline modules have finished."""
        if getattr(self, "_outcome_finalized", False):
            return
        self._outcome_finalized = True
        merged_extra = {**self.result_extra, **extra}
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            try:
                merged_extra["telemetry"] = telemetry.finalize(self.result_status)
            except Exception as exc:
                self.log("WARN", f"Could not finalize execution telemetry: {exc}")
        self.result_extra = merged_extra
        try:
            self.evidence.write_result(
                self.result_status,
                self.result_error,
                **merged_extra,
            )
        except Exception as exc:
            self.log("WARN", f"Could not write evidence result: {exc}")

    def rank_candidates_semantically(
        self,
        goal: str,
        screen: np.ndarray,
        region: str | tuple[int, int, int, int] | tuple[float, float, float, float] | None = None,
        is_reversible: bool = True,
        min_ocr_confidence: float = 0.20,
    ) -> tuple[bool, str, tuple[int, int] | None]:
        """
        Extract OCR candidates in the specified region and evaluate semantic fallback.
        Audits proposal via evidence recorder and enforces deterministic safety gates.
        Returns: (allow_click, status_reason, target_coords)
        """
        candidates = collect_action_candidates(
            self.vision, screen, region, min_confidence=min_ocr_confidence,
        )
        if not candidates:
            return False, "no_candidates", None

        obs = self.recognizer.observe(screen)
        state_name = obs.state.value
        blocked_reason = self.semantic_fallback.blocked_observation_reason(obs)
        if blocked_reason:
            self.log(
                "WARN",
                f"Semantic fallback skipped before provider invocation: {blocked_reason}",
            )
            return False, blocked_reason, None

        proposal, candidate_map = self.semantic_fallback.rank_candidates(
            state=state_name,
            goal=goal,
            raw_candidates=candidates,
            expected_region=region,
        )

        if not proposal:
            return False, "no_proposal", None

        # Re-capture and re-read after ranking. The provider never supplies
        # executable coordinates; only a locally re-observed candidate can.
        fresh_screen = self.client.screenshot()
        fresh_candidates = collect_action_candidates(
            self.vision, fresh_screen, region, min_confidence=min_ocr_confidence,
        )
        allow_click, status_reason, target_coords = self.semantic_fallback.validate_proposal(
            proposal=proposal,
            candidate_map=candidate_map,
            current_screen=fresh_screen,
            expected_region=region,
            recognizer=self.recognizer,
            is_reversible=is_reversible,
            fresh_candidates=fresh_candidates,
            expected_states=EXPECTED_ACTION_STATES.get(goal.strip().casefold()),
            require_enabled_action=True,
        )

        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.record_semantic(proposal.to_dict(), goal, state_name)

        # Audit fallback in evidence
        try:
            structured_candidates = [c.to_structured_dict() for c in candidate_map.values()]
            self.evidence.record_semantic_fallback(
                goal=goal,
                state=state_name,
                proposal=proposal.to_dict(),
                candidates=structured_candidates,
                screen=fresh_screen,
            )
        except Exception as exc:
            self.log("WARN", f"Could not record semantic fallback audit: {exc}")

        cand_text = candidate_map[proposal.candidate_id].text if proposal.candidate_id in candidate_map else "unknown"
        if proposal.shadow_mode:
            self.log(
                "INFO",
                f"[SHADOW MODE] Semantic fallback proposed '{cand_text}' ({proposal.candidate_id}, conf={proposal.confidence:.2f}) "
                f"for goal '{goal}'. Deterministic status: {status_reason}. No click executed."
            )
        else:
            self.log(
                "INFO" if allow_click else "WARN",
                f"Semantic fallback proposed '{cand_text}' (conf={proposal.confidence:.2f}) for goal '{goal}'. "
                f"Result: {status_reason} (allowed={allow_click})."
            )

        return allow_click, status_reason, target_coords

    def wait_for_states(
        self,
        expected: set[ScreenState],
        timeout: float,
        poll_interval: float = 1.0,
        stable_observations: int = 1,
    ):
        """Poll screenshots until an expected state is observed repeatedly."""
        deadline = time.time() + timeout
        previous_state = None
        stable_count = 0
        last = None
        while time.time() < deadline:
            screen = self.client.screenshot()
            observation = self.recognizer.observe(screen)
            last = (observation, screen)
            if observation.state in expected:
                if observation.state == previous_state:
                    stable_count += 1
                else:
                    previous_state = observation.state
                    stable_count = 1
                if stable_count >= stable_observations:
                    return observation, screen
            else:
                previous_state = observation.state
                stable_count = 0
                if observation.state == ScreenState.UNKNOWN:
                    if self.handle_leave_site_dialog(screen):
                        time.sleep(poll_interval)
                        continue
            time.sleep(poll_interval)
        return last

    def handle_leave_site_dialog(self, screen=None) -> bool:
        """
        Check if Chrome displayed the 'Leave site? Changes you made may not be saved.'
        modal dialog. If detected, click 'Leave' or send Return to dismiss it and unblock navigation.
        Returns True if a dialog was detected and dismissed, False otherwise.
        """
        try:
            vision = getattr(self, "vision", None)
            if vision is None or not hasattr(vision, "find_leave_site_button"):
                return False
            coords = vision.find_leave_site_button(screen=screen)
            if coords:
                self.log("WARN", f"Detected 'Leave site?' navigation dialog. Clicking Leave at {coords}...")
                self.capture_evidence("dismiss_leave_site_dialog", screen=screen)
                human = getattr(self, "human", None)
                if human and hasattr(human, "click"):
                    human.click(coords[0], coords[1])
                elif getattr(self, "client", None) and hasattr(self.client, "dismiss_dialog_key"):
                    self.client.dismiss_dialog_key()
                time.sleep(1.0)
                return True
        except Exception as exc:
            self.log("DEBUG", f"Error during leave site dialog check: {exc}")
        return False

    def find_post_publish_prompt(self, screen=None):
        """Return a modal-scoped Not now match when the known prompt is visible."""
        if screen is None:
            screen = self.client.screenshot() if getattr(self, "client", None) else None
        if screen is None:
            return None
        match = None
        height, width = screen.shape[:2]
        prompt_region = (
            int(width * 0.15), int(height * 0.08),
            int(width * 0.70), int(height * 0.87),
        )
        for item in self.vision.read_text(screen, region=prompt_region, min_confidence=0.15):
            normalized = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
            compact = normalized.replace(" ", "")
            words = set(normalized.split())
            if compact in {"notnow", "notn0w"} or (
                "not" in words and ("now" in words or "n0w" in words)
            ):
                if match is None or item["confidence"] > match["confidence"]:
                    match = item
        return match

    def check_and_dismiss_post_prompt(self, screen=None) -> bool:
        """
        Detect and dismiss Facebook post-publish prompts (e.g. 'Speak With People Directly',
        'Add a button', WhatsApp prompts, or upsell modals) by clicking 'Not now'.
        """
        try:
            match = self.find_post_publish_prompt(screen)
            if match:
                self.log("INFO", f"Detected post-publish prompt ('Not now'). Clicking at {match['center']}...")
                self.capture_evidence("dismiss_post_prompt", screen, target=list(match["center"]))
                self.click_reversible(
                    match["center"],
                    label="dismiss_post_prompt",
                    bounds=match.get("bounds"),
                    max_offset_px=4,
                )
                time.sleep(2.0)
                return True
        except Exception as exc:
            self.log("DEBUG", f"Error during post prompt check: {exc}")
        return False

    def handle_post_publish_prompt(self, screen=None) -> str:
        """Use the shared prompt handler, with compatibility for lightweight task doubles."""
        handler = getattr(self, "post_publish_prompt", None)
        if handler is not None:
            return handler.handle(screen)
        return "dismissed" if self.check_and_dismiss_post_prompt(screen) else "absent"

    def navigate_to(
        self,
        url: str,
        wait_seconds: float = 2.0,
        check_leave_dialog: bool = True,
    ) -> None:
        """
        Navigate to URL via container client and automatically handle any blocking
        'Leave site?' beforeunload prompt.
        """
        started = time.perf_counter()
        outcome = "completed"
        try:
            if hasattr(self, "client") and self.client is not None:
                self.client.navigate_to(url)
            time.sleep(1.0)
            if check_leave_dialog:
                self.handle_leave_site_dialog()
            if wait_seconds > 1.0:
                time.sleep(wait_seconds - 1.0)
        except Exception:
            outcome = "exception"
            raise
        finally:
            telemetry = getattr(self, "telemetry", None)
            if telemetry is not None:
                telemetry.record_navigation(url, (time.perf_counter() - started) * 1000.0, outcome)

    def open_warming_surface(
        self,
        surface: str,
        timeout: float = 15.0,
        *,
        navigate: bool = True,
    ):
        """Navigate to and verify one passive Facebook browsing surface."""
        url = self.WARMING_SURFACES.get(surface)
        if url is None:
            raise ValueError(f"Unknown warming surface: {surface}")

        self.log("INFO", f"Opening warming surface: {surface}")
        if navigate:
            self.navigate_to(url, wait_seconds=2.0, check_leave_dialog=False)
        else:
            self.log("INFO", f"Reusing already verified warming surface: {surface}")
        deadline = time.time() + timeout
        last_screen = None
        valid_states = {
            ScreenState.FEED_READY,
            ScreenState.COMPOSER_OPEN,
            ScreenState.MEDIA_READY,
            ScreenState.POST_ENABLED,
        }
        while time.time() < deadline:
            last_screen = self.client.screenshot()
            observation = self.recognizer.observe_session_gate(last_screen)
            if observation.state == ScreenState.UNKNOWN:
                observation = self.recognizer.observe(last_screen)
            if observation.state == ScreenState.LOGIN_REQUIRED:
                self.session_check_status = "auth_required"
                return False, last_screen
            if observation.state == ScreenState.ERROR_DIALOG:
                return False, last_screen
            if observation.state in valid_states:
                try:
                    current_url = self.client.get_current_url()
                except Exception:
                    current_url = None
                if self._is_facebook_url(current_url) and self._has_strong_facebook_visual(observation):
                    return True, last_screen
            time.sleep(0.75)
        return False, last_screen

    def select_and_open_warming_surface(
        self,
        requested: str | None = None,
        already_open: str | None = None,
    ):
        """Choose either feed or profile independently, with one safe fallback."""
        requested = requested or random.choice(tuple(self.WARMING_SURFACES))
        if requested not in self.WARMING_SURFACES:
            raise ValueError(f"Unknown warming surface: {requested}")
        fallback = next(surface for surface in self.WARMING_SURFACES if surface != requested)
        for attempt, surface in enumerate((requested, fallback), start=1):
            if attempt == 1 and surface == already_open:
                ready, screen = self.open_warming_surface(surface, navigate=False)
            else:
                ready, screen = self.open_warming_surface(surface)
            if ready:
                used_fallback = attempt == 2
                self.log(
                    "INFO",
                    f"Warming surface ready: {surface}"
                    + (f" (fallback from {requested})" if used_fallback else ""),
                )
                return requested, surface, used_fallback, screen
            if attempt == 1:
                self.log("WARN", f"Warming surface '{requested}' was not ready; trying '{fallback}' once.")
        return requested, None, True, screen

    def refresh_page(self, wait_seconds: float = 2.0) -> None:
        """
        Refresh active browser page via container client and automatically handle any blocking
        'Leave site?' beforeunload prompt.
        """
        if hasattr(self, "client") and self.client is not None:
            self.client.refresh_page()
        time.sleep(1.0)
        self.handle_leave_site_dialog()
        if wait_seconds > 1.0:
            time.sleep(wait_seconds - 1.0)

    def paste_text(
        self,
        text: str,
        *,
        paste_key: str = "ctrl+v",
        selection: str = "clipboard",
    ) -> None:
        """
        Copy text (including full Unicode & emojis) to container X11 clipboard
        using xclip and paste it into the active element with the requested
        shortcut. ``primary`` + Shift+Insert is available as an independent
        fallback when a page ignores the regular clipboard shortcut.
        """
        try:
            import subprocess
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "-u",
                    "chromeuser",
                    "-e",
                    f"DISPLAY={self.client.display}",
                    self.client.container_name,
                    "xclip",
                    "-selection",
                    selection,
                ],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
            )
            time.sleep(0.15)
            self.human.key_press(paste_key)
            time.sleep(0.2)
        except Exception as e:
            self.log("WARN", f"Clipboard paste failed, falling back to typing: {e}")
            self.human.type_text(text)

    @staticmethod
    def _is_facebook_url(url: str | None) -> bool:
        """Accept Facebook itself, but never lookalike hosts containing its name."""
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = (parsed.hostname or "").casefold().rstrip(".")
            return parsed.scheme in {"http", "https"} and (
                hostname == "facebook.com" or hostname.endswith(".facebook.com")
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _has_strong_facebook_visual(observation: StateObservation) -> bool:
        """Require a Facebook-specific UI signal in addition to its URL host."""
        if observation.confidence < 0.75:
            return False

        signals = {signal.casefold() for signal in observation.signals}
        if observation.state == ScreenState.FEED_READY:
            strong_feed_signals = {
                "what's on your mind",
                "what’s on your mind",
                "whats on your mind",
                "what s on your mind",
                "photo/video",
                "photo / video",
                "create story",
                "your story",
                "professional dashboard",
                "photo/video text",
                "logged-in dashboard/feed text",
                "photo/video button visual",
            }
            return bool(signals & strong_feed_signals)

        # These states require Facebook-specific modal/action structure in the
        # recognizer, so their non-empty signals are independent visual proof.
        workflow_states = {
            ScreenState.COMPOSER_OPEN,
            ScreenState.MEDIA_UPLOADING,
            ScreenState.MEDIA_READY,
            ScreenState.POST_ENABLED,
            ScreenState.PUBLISHING,
            ScreenState.POST_CONFIRMED,
        }
        return observation.state in workflow_states and bool(signals - {"facebook logo"})

    def skip_unverified_session(self) -> bool:
        """Finish safely without treating an expired session as an automation failure."""
        session_status = getattr(self, "session_check_status", "unverified")
        if session_status in {"auth_required", "safety_blocked"}:
            status = "skipped_auth_required"
            message = "Facebook is logged out, expired, restricted, or requires an account checkpoint."
        else:
            status = "skipped_session_unverified"
            message = "Facebook did not reach a safely verified logged-in screen within 40 seconds."
        self.log("WARN", f"Skipping profile: {message} No login action will be attempted.")
        return self.set_outcome(
            status,
            error=message,
            auth_preflight=session_status,
        )

    def verify_logged_in(self, target_url: str = "https://www.facebook.com/") -> bool:
        """
        Verify that the container Chrome session is logged into Facebook.
        Loading and transitional screens remain UNKNOWN and are polled rather
        than being mistaken for a logged-out session.
        """
        self.log("INFO", "Verifying Facebook session login state...")
        self.navigate_to(
            target_url,
            wait_seconds=2.0,
            check_leave_dialog=False,
        )

        # Check window title first because it is cheap and works without OCR.
        try:
            res = self.client.exec_cmd(["xdotool", "getactivewindow", "getwindowname"], check=False)
            if res.returncode == 0:
                title = res.stdout.strip().lower()
                if any(k in title for k in ("log in", "login", "sign up", "welcome to facebook")):
                    self.session_check_status = "auth_required"
                    self.log("WARN", f"Session expired or logged out (Window title: {title})")
                    self.capture_evidence("session_preflight_auth_required")
                    return False
        except Exception:
            pass

        try:
            current_url = self.client.get_current_url()
            blocked_paths = ("/login", "/checkpoint", "/recover", "/two_step_verification")
            if current_url and any(path in current_url.casefold() for path in blocked_paths):
                self.session_check_status = "auth_required"
                self.log("WARN", f"Facebook authentication checkpoint detected at {current_url}")
                self.capture_evidence("session_preflight_auth_required")
                return False
        except Exception:
            pass

        # This is a safety preflight, not a login workflow. Profiles are assumed
        # to be authenticated; a short bounded check only prevents blind actions
        # after an expired session, checkpoint, or unexpected page.
        deadline = time.time() + 40.0
        observation = None
        screen = None
        valid_logged_in_states = {
            ScreenState.FEED_READY,
            ScreenState.COMPOSER_OPEN,
            ScreenState.MEDIA_UPLOADING,
            ScreenState.MEDIA_READY,
            ScreenState.POST_ENABLED,
            ScreenState.PUBLISHING,
            ScreenState.POST_CONFIRMED,
        }
        navigation_retried = False
        while time.time() < deadline:
            screen = self.client.screenshot()
            # Navigation can still show the previous page after the address bar
            # changes. Refresh theme from each actual screenshot so browser
            # chrome or a prior light page cannot lock Facebook into light-mode
            # preprocessing.
            self.detect_visual_theme(screen)
            observation = self.recognizer.observe_session_gate(screen)
            if "leave_site_dialog" in observation.signals:
                if self.handle_leave_site_dialog(screen):
                    time.sleep(1.0)
                    continue
            if observation.state == ScreenState.UNKNOWN:
                observation = self.recognizer.observe(screen)
            if observation.state == ScreenState.LOGIN_REQUIRED:
                break
            if observation.state == ScreenState.ERROR_DIALOG:
                break
            if observation.state in valid_logged_in_states:
                try:
                    current_url = self.client.get_current_url()
                except Exception:
                    current_url = None
                has_facebook_host = self._is_facebook_url(current_url)
                has_facebook_visual = self._has_strong_facebook_visual(observation)
                if has_facebook_host and has_facebook_visual:
                    break
                self.log(
                    "INFO",
                    "Ignoring an unconfirmed/loading browser page "
                    f"(url={current_url or 'unknown'}, "
                    f"facebook_visual={has_facebook_visual}, signals={observation.signals}).",
                )
                if not navigation_retried and not has_facebook_host:
                    self.log(
                        "INFO",
                        "The browser remained outside Facebook after the first navigation; retrying the target URL once.",
                    )
                    self.navigate_to(
                        target_url,
                        wait_seconds=2.0,
                        check_leave_dialog=False,
                    )
                    navigation_retried = True
                observation = StateObservation(
                    ScreenState.UNKNOWN,
                    0.0,
                    ["facebook_host_or_visual_not_confirmed"],
                )
            if observation.state == ScreenState.UNKNOWN:
                if self.handle_leave_site_dialog(screen):
                    time.sleep(1.0)
                    continue
            self.log("INFO", "Facebook is still loading or the screen is transitional; checking again...")
            time.sleep(2.0)

        if observation is None or screen is None:
            self.session_check_status = "unverified"
            self.log("ERROR", "Facebook session verification produced no visual observation.")
            return False

        self.capture_evidence(
            "login_check",
            screen,
            state=observation.state.value,
            confidence=observation.confidence,
            signals=observation.signals,
        )
        if observation.state == ScreenState.LOGIN_REQUIRED:
            self.session_check_status = "auth_required"
            self.log("WARN", "Facebook login screen was detected; this profile will be skipped.")
            return False
        if observation.state == ScreenState.ERROR_DIALOG:
            self.session_check_status = "safety_blocked"
            self.log("WARN", "Facebook displayed a restriction, checkpoint, CAPTCHA, or error; this profile will be skipped.")
            return False
        if observation.state not in valid_logged_in_states:
            self.session_check_status = "unverified"
            self.log("WARN", "Facebook session remained visually unverified after the 40-second safety timeout.")
            return False
        self.session_check_status = "authenticated"
        return True

    def find_visible_file_chooser(self) -> tuple[str, str] | None:
        """Return the visible GTK chooser window id and matched title pattern."""
        for dialog_name in self.FILE_CHOOSER_DIALOG_NAMES:
            result = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", dialog_name],
                check=False,
            )
            if result.returncode != 0:
                continue
            window_ids = result.stdout.strip().split()
            if window_ids:
                return window_ids[-1], dialog_name
        return None

    @timed_telemetry_step("media_upload")
    def attach_file_gtk(
        self,
        file_path: str,
        chooser_window: tuple[str, str] | None = None,
    ) -> bool:
        """
        Attach a file using the native Linux GTK file chooser dialog (Zero-CDP).
        Focuses the visible 'Open Files' window, enters the direct path, then
        visually locates and clicks its Open button. It never confirms with a
        blind Return press because focus can move to Cancel in GTK dialogs.
        """
        self.log("STEP", f"Zero-CDP GTK file attachment: {file_path}")

        # Wait up to 15s for GTK dialog to appear. Reel Studio can delay opening
        # the native chooser while its upload UI initializes.
        detected_chooser = chooser_window
        for _ in range(30):
            if detected_chooser is None:
                detected_chooser = self.find_visible_file_chooser()
            if detected_chooser is not None:
                break
            time.sleep(0.5)

        if detected_chooser is not None:
            win_id, matched_dialog_name = detected_chooser
            self.client.exec_cmd(["xdotool", "windowfocus", "--sync", str(win_id)], check=False)
            time.sleep(0.3)
        else:
            self.log("ERROR", "GTK file chooser window ('Open File') was not detected; refusing blind input.")
            visible_windows = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", ".*"],
                check=False,
            )
            self.capture_evidence(
                "file_chooser_not_detected",
                visible_window_ids=visible_windows.stdout.strip().split(),
                searched_titles=list(self.FILE_CHOOSER_DIALOG_NAMES),
            )
            return False

        file_check = self.client.exec_cmd(["test", "-f", file_path], check=False)
        if file_check.returncode != 0:
            self.log("ERROR", f"Media file does not exist inside the container: {file_path}")
            return False

        # In GTK3 file chooser, Ctrl+L reliably activates the direct path text input
        self.human.key_press("ctrl+l")
        time.sleep(0.3)
        self.human.key_press("ctrl+a")
        time.sleep(0.1)
        self.human.key_press("BackSpace")
        time.sleep(0.2)

        # Type the path inside the container as a direct argv argument
        self.client.exec_cmd(["xdotool", "type", "--", file_path], check=False)
        time.sleep(0.5)

        # Locate and click the dialog's Open button to submit the selected file
        geometry = self.client.exec_cmd(
            ["xdotool", "getwindowgeometry", "--shell", str(win_id)],
            check=False,
        )
        values = dict(re.findall(r"^(X|Y|WIDTH|HEIGHT)=(\d+)$", geometry.stdout, re.MULTILINE))
        clicked_open = False
        if len(values) == 4:
            dialog_x = int(values["X"])
            dialog_y = int(values["Y"])
            dialog_w = int(values["WIDTH"])
            dialog_h = int(values["HEIGHT"])
            screen = self.client.screenshot()
            action_region = (
                dialog_x + dialog_w // 2,
                dialog_y + (dialog_h * 2) // 3,
                dialog_w // 2,
                dialog_h // 3,
            )
            open_match = self.vision.find_text(
                ("open", "select"),
                screen=screen,
                region=action_region,
                min_confidence=0.35,
            )
            self.capture_evidence(
                "file_chooser_ready",
                screen,
                file_dialog_window=win_id,
                action_region=list(action_region),
                open_target=list(open_match["center"]) if open_match else None,
            )
            if open_match:
                self.log("INFO", f"Clicking the visually confirmed Open button at {open_match['center']}...")
                self.human.click(*open_match["center"])
                clicked_open = True
            else:
                fallback_open = (dialog_x + dialog_w - 48, dialog_y + dialog_h - 22)
                self.log("INFO", f"Clicking geometric Open button at {fallback_open}...")
                self.human.click(*fallback_open)
                clicked_open = True

        if not clicked_open:
            self.client.exec_cmd(["xdotool", "key", "Return"], check=False)

        for _ in range(12):
            time.sleep(0.5)
            check_dialog = self.client.exec_cmd(
                ["xdotool", "search", "--onlyvisible", "--name", matched_dialog_name or "Open File"],
                check=False,
            )
            if check_dialog.returncode != 0 or not check_dialog.stdout.strip():
                self.log("INFO", "GTK file chooser dismissed successfully.")
                break
        else:
            self.log("ERROR", "GTK dialog remained visible after submitting file.")
            self.capture_evidence("file_chooser_open_failed")
            return False

        # Re-focus Chrome window
        chrome_res = self.client.exec_cmd(["xdotool", "search", "--class", "google-chrome"], check=False)
        if chrome_res.returncode == 0 and chrome_res.stdout.strip():
            chrome_win = chrome_res.stdout.strip().split()[-1]
            self.client.exec_cmd(["xdotool", "windowfocus", str(chrome_win)], check=False)

        return True

    @staticmethod
    def _first_comment_search_regions(screen):
        """Return the evidence-backed local crop and the existing broad fallback."""
        height, width = screen.shape[:2]
        narrow = (
            int(width * 0.53),
            int(height * 0.62),
            int(width * 0.38),
            int(height * 0.29),
        )
        broad = (
            max(0, int(width * 0.28)),
            0,
            min(width - int(width * 0.28), int(width * 0.67)),
            height,
        )
        return narrow, broad

    @staticmethod
    def _submitted_comment_region(screen, comment_box_pos):
        """Crop around the verified input and the newly rendered comment above it."""
        height, width = screen.shape[:2]
        anchor_x, anchor_y = comment_box_pos
        x1 = max(int(width * 0.28), int(anchor_x - width * 0.08))
        y1 = max(0, int(anchor_y - height * 0.20))
        x2 = min(int(width * 0.95), int(anchor_x + width * 0.34))
        y2 = min(height, int(anchor_y + height * 0.10))
        return x1, y1, max(1, x2 - x1), max(1, y2 - y1)

    def _find_first_comment_input(self, screen=None, region=None):
        """Locate the 'Comment as ...' or 'Write a comment...' input field."""
        if hasattr(self.vision, "find_comment_input"):
            regions = [region] if region is not None else None
            res = self.vision.find_comment_input(screen=screen, regions=regions)
            if res is not None and type(res).__name__ not in ("Mock", "MagicMock"):
                return res
        # Fallback / mock support
        candidates = []
        screen = self.client.screenshot() if screen is None and hasattr(self, "client") and self.client else screen
        for item in self.vision.read_text(screen, region=region, min_confidence=0.15):
            if VisionEngine.is_comment_input_text(item.get("text", "")):
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["center"][1])
        return candidates[0]["center"]

    def _find_first_comment_action(self, screen):
        """Return the topmost exact Comment action in the profile post stream."""
        height, width = screen.shape[:2]
        region = (
            max(0, int(width * 0.28)),
            0,
            min(width - int(width * 0.28), 1050),
            height,
        )
        candidates = []
        for item in self.vision.read_text(screen, region=region, min_confidence=0.35):
            normalized = re.sub(r"[^a-z]+", " ", item.get("text", "").casefold()).strip()
            if normalized == "comment":
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.get("center", (0, height + 1))[1])
        return candidates[0]

    def _find_first_comment_targets(self, screen, *, allow_broad_fallback=True):
        """Search the expected comment crop, then the broad post/feed area."""
        height = screen.shape[0]
        narrow, broad = self._first_comment_search_regions(screen)
        regions = [("narrow", narrow)]
        if allow_broad_fallback:
            regions.append(("broad", broad))

        for tier, region in regions:
            inputs = []
            actions = []
            for item in self.vision.read_text(screen, region=region, min_confidence=0.15):
                normalized = re.sub(r"[^a-z0-9]+", " ", item.get("text", "").casefold()).strip()
                if VisionEngine.is_comment_input_text(item.get("text", "")):
                    inputs.append(item)
                elif normalized == "comment":
                    actions.append(item)

            inputs.sort(key=lambda item: item.get("center", (0, height + 1))[1])
            actions.sort(key=lambda item: item.get("center", (0, height + 1))[1])
            input_target = inputs[0].get("center") if inputs else None
            action_target = actions[0] if actions else None
            if input_target or action_target:
                self._last_comment_search_tier = tier
                return input_target, action_target

        self._last_comment_search_tier = "not_found"
        return None, None

    def _open_profile_first_comment_input(self, *, navigate: bool = True):
        """
        Primary comment targeting method: navigate to the profile and select the
        topmost comment field/action belonging to the first visible post.

        Profiles managed by this application do not pin posts, so the first post
        is the intended newly published item once the profile feed has refreshed.
        """
        self.log("STEP", "Primary comment method: opening the profile's first post...")
        if navigate:
            self.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
        else:
            self.log("INFO", "Reusing the verified post-publication profile feed for comment targeting.")

        # Move mouse over main post feed column (x ~ 1150, y ~ 500) so mouse wheel / keys scroll feed
        self.client.exec_cmd(["xdotool", "mousemove", "1150", "500"], check=False)

        # Comment success is more important than OCR cost here. Tall Reel cards
        # can require substantially more vertical travel than photo/text posts,
        # so every scan uses all detection regions and the bounded search goes
        # far enough to expose the first card's action row.
        for scan in range(1, 15):
            screen = self.client.screenshot()
            target, action_btn = self._find_first_comment_targets(
                screen,
                allow_broad_fallback=True,
            )
            search_tier = getattr(self, "_last_comment_search_tier", "unknown")
            self.log_decision(
                "Find 'Comment as ...' field",
                "post card comment input ('Comment as ...' or 'Write a comment...')",
                f"scan={scan}, tier={search_tier}, target={target}",
                "click comment field" if target else "scroll and scan again",
                level="INFO",
            )
            if target:
                return target, screen

            # The Comment action/icon is only a landmark. Clicking it can open a
            # post overlay without focusing the editor, after which pasted text
            # is lost. Move the feed just enough to expose the actual comment
            # input below the action row, then require that input on a later
            # scan before any text-entry click occurs.
            if action_btn:
                action_source = action_btn.get("source", "ocr_comment_label")
                self.capture_evidence(
                    "comment_action_observed",
                    screen,
                    target=list(action_btn["center"]),
                    action_source=action_source,
                    scan=scan,
                )
                self.log(
                    "INFO",
                    f"Observed '{action_source}' at {action_btn['center']}; scrolling to the comment input without clicking the action.",
                )
                self.human.scroll("down", notches=1)
                time.sleep(0.8)
                continue

            if scan == 1:
                time.sleep(0.8)
                continue

            # Move just enough to reveal the remainder of the first post. Large
            # page jumps could make a later post become the topmost visible card.
            self.human.scroll("down", notches=2)
            time.sleep(0.8)

        self.capture_evidence(
            "comment_input_not_found",
            self.client.screenshot(),
            scans=14,
            broad_post_area=True,
            full_screen_fallback=False,
        )
        return None, None

    def _open_permalink_comment_input(self, post_url: str | None):
        """Fallback comment target used only before any comment submission."""
        if not post_url:
            return None, None
        is_valid, clean_url = self.validate_facebook_permalink(post_url)
        if not is_valid or not clean_url:
            self.log("WARN", f"Comment permalink fallback rejected an invalid URL: {post_url}")
            return None, None

        self.log("STEP", f"Fallback comment method: opening verified permalink {clean_url}")
        try:
            self.navigate_to(clean_url, wait_seconds=3.0)
            for scan in range(1, 4):
                screen = self.client.screenshot()
                _narrow, broad = self._first_comment_search_regions(screen)
                target = self._find_first_comment_input(screen, region=broad)
                self.log_decision(
                    "Find permalink comment field",
                    "'Comment as ...' field on the verified permalink",
                    f"scan={scan}, target={target}",
                    "use permalink field" if target else "wait and scan again",
                    level="INFO",
                )
                if target:
                    return target, screen
                time.sleep(1.2)
        except Exception as exc:
            self.log("WARN", f"Could not use the permalink comment fallback: {exc}")
        return None, None

    @staticmethod
    def _comment_match_confidence(comment_text: str, ocr_items: list[dict], max_y: int) -> float:
        """Measure whether submitted comment text is visible above the input row."""
        normalized_target = re.sub(r"[^a-z0-9]+", " ", comment_text.casefold()).strip()
        target_tokens = [token for token in normalized_target.split() if len(token) >= 2]
        if not target_tokens:
            return 0.0

        visible_parts = []
        for item in ocr_items:
            if item.get("center", (0, max_y + 1))[1] > max_y:
                continue
            normalized = re.sub(r"[^a-z0-9]+", " ", item.get("text", "").casefold()).strip()
            if VisionEngine.is_comment_input_text(normalized):
                continue
            visible_parts.append(normalized)

        visible_text = " ".join(visible_parts)
        if not visible_text:
            return 0.0
        if normalized_target in visible_text:
            return 1.0
        visible_tokens = set(visible_text.split())
        matched = sum(1 for token in target_tokens if token in visible_tokens)
        return matched / len(target_tokens)

    def post_first_comment(
        self,
        comment_link: str,
        post_url: str | None = None,
        *,
        reuse_profile_page: bool = False,
        prefer_permalink: bool = False,
        warm_down_after_submit: bool = True,
    ) -> str:
        """
        Locate the post's 'Comment as ...' field, click it, paste comment_link, and submit once with Return.
        Returns: 'submitted_unverified' on submission, or 'failed_input_not_found'.
        """
        self.set_stage("commenting")
        self.log("STEP", "Locating 'Comment as ...' field for first comment...")
        time.sleep(1.5)
        self.last_comment_method = None
        comment_box_pos = None
        comment_screen = None
        if not prefer_permalink:
            if reuse_profile_page:
                comment_box_pos, comment_screen = self._open_profile_first_comment_input(
                    navigate=False
                )
            else:
                comment_box_pos, comment_screen = self._open_profile_first_comment_input()
            if comment_box_pos:
                self.last_comment_method = "profile_first_post"
        if not comment_box_pos and reuse_profile_page and not prefer_permalink:
            self.log(
                "INFO",
                "The reused profile feed did not expose a comment field; refreshing and running one full second pass.",
            )
            comment_box_pos, comment_screen = self._open_profile_first_comment_input(
                navigate=True
            )
            if comment_box_pos:
                self.last_comment_method = "profile_first_post_refresh"
        if not comment_box_pos and post_url:
            self.log("WARN", "Primary first-post comment target was not found; trying the verified permalink before submission.")
            comment_box_pos, comment_screen = self._open_permalink_comment_input(post_url)
            if comment_box_pos:
                self.last_comment_method = "verified_permalink_fallback"
        if not comment_box_pos:
            self.log_decision(
                "Submit first comment",
                "'Comment as ...' field under post",
                "no verified 'Comment as' field found after scanning",
                "keep content published and skip comment",
                level="WARN",
            )
            return "failed_input_not_found"

        if comment_screen is None:
            comment_screen = self.client.screenshot()

        self.capture_evidence(
            "before_first_comment",
            comment_screen,
            target=list(comment_box_pos),
            comment_method=self.last_comment_method,
        )
        self.log_decision(
            "Submit first comment",
            "'Comment as ...' field under post",
            f"method={self.last_comment_method}, target={comment_box_pos}",
            "click 'Comment as ...', paste configured comment, and submit once",
        )
        self.log("STEP", f"Clicking 'Comment as ...' at {comment_box_pos}...")
        self.human.click(*comment_box_pos)
        time.sleep(1.0)
        self.paste_text(comment_link)
        time.sleep(0.5)
        self.human.key_press("Return")
        self.log("INFO", "First comment was submitted once; waiting for Facebook's Posting indicator to clear.")

        after_comment = None
        clear_streak = 0
        visible_streak = 0
        best_comment_confidence = 0.0
        settled_observations = 0
        local_verification_misses = 0
        for _ in range(30):
            time.sleep(2.0)
            after_comment = self.client.screenshot()
            screen_h, screen_w = after_comment.shape[:2]
            # Verify around the known input first. After two local misses, use
            # the established broad card crop so long comments and link-preview
            # titles at the right edge still remain verifiable.
            broad_comment_region = (
                int(screen_w * 0.28),
                int(screen_h * 0.22),
                int(screen_w * 0.67),
                int(screen_h * 0.73),
            )
            focused_comment_region = self._submitted_comment_region(
                after_comment,
                comment_box_pos,
            )
            comment_region = (
                focused_comment_region
                if local_verification_misses < 2
                else broad_comment_region
            )
            comment_items = self.vision.read_text(
                after_comment,
                region=comment_region,
                min_confidence=0.15,
            )
            normalized_comment_items = {
                re.sub(r"[^a-z0-9]+", " ", item.get("text", "").casefold()).strip()
                for item in comment_items
            }
            submission_pending = bool(normalized_comment_items.intersection({
                "posting",
                "posting comment",
                "sending",
                "sending comment",
                "submitting",
                "submitting comment",
            }))
            comment_confidence = self._comment_match_confidence(
                comment_link,
                comment_items,
                # The submitted comment renders directly above the known
                # input. Tall Reel cards can place both below 90% of the
                # viewport, so bind the cutoff to that input instead of a
                # fixed screen percentage.
                max_y=min(screen_h - 1, int(comment_box_pos[1]) - 4),
            )
            best_comment_confidence = max(best_comment_confidence, comment_confidence)
            if submission_pending:
                clear_streak = 0
                settled_observations = 0
                visible_streak = 0
                continue
            clear_streak += 1
            settled_observations += 1
            if comment_confidence >= 0.60:
                visible_streak += 1
            else:
                visible_streak = 0
                local_verification_misses += 1
            # Two consecutive visible observations confirm the comment. When
            # OCR cannot match it, allow four settled observations before
            # returning unverified so a slowly rendered link preview is not
            # missed. Never resubmit automatically.
            if (visible_streak >= 2 and clear_streak >= 2) or settled_observations >= 4:
                break

        if after_comment is None:
            after_comment = self.client.screenshot()
        comment_status = (
            "submitted_verified"
            if visible_streak >= 2
            else "submitted_unverified"
        )
        self.capture_evidence(
            "after_first_comment",
            after_comment,
            comment_status=comment_status,
            comment_match_confidence=round(best_comment_confidence, 3),
            comment_method=self.last_comment_method,
        )

        if clear_streak < 2:
            self.log("WARN", "First comment is still submitting; leaving the post modal open and not retrying.")
            return "submission_pending"

        if comment_status == "submitted_verified":
            self.log(
                "SUCCESS",
                f"First comment text was visibly verified (confidence={best_comment_confidence:.2f}).",
            )
        else:
            self.log(
                "WARN",
                "First comment submission settled, but its text was not visibly verified; no automatic retry will be attempted.",
            )

        if not warm_down_after_submit:
            self.log("INFO", "Comment verification complete; returning immediately for container shutdown.")
            return comment_status

        # The permalink/comment view is normally a centered modal. Click a
        # resolution-relative point on the dimmed backdrop to close it without
        # touching post actions, then linger briefly like a human operator.
        screen_h, screen_w = after_comment.shape[:2]
        backdrop_target = (
            max(20, int(screen_w * 0.20)),
            max(20, int(screen_h * 0.50)),
        )
        self.log("INFO", f"Closing the post modal from the outside backdrop at {backdrop_target}...")
        self.human.click(*backdrop_target)

        warm_seconds = random.uniform(1.0, 5.0)
        self.set_stage("warming", seconds=round(warm_seconds, 2))
        self.log("INFO", f"Post-comment warm-down for {warm_seconds:.1f}s...")
        time.sleep(warm_seconds)
        self.capture_evidence("after_comment_modal_close", self.client.screenshot())
        return comment_status

    def post_first_comment_with_page_reuse(
        self,
        comment_link: str,
        *,
        caption: str | None,
        media_type: str,
    ) -> tuple[str, dict]:
        """Try the current profile feed before paying for navigation/correlation."""
        comment_status = self.post_first_comment(
            comment_link,
            reuse_profile_page=True,
        )
        if comment_status != "failed_input_not_found":
            return comment_status, self.permalink_not_requested()

        self.log(
            "INFO",
            "Current profile feed did not expose the comment input; refreshing once to correlate the published post.",
        )
        permalink_info = self.correlate_and_extract_permalink(
            caption=caption,
            media_type=media_type,
        )
        if not permalink_info.get("post_url"):
            permalink_info.update({
                "permalink_status": "missing",
                "permalink_missing": True,
                "permalink_recovery_attempted": False,
            })
            return comment_status, permalink_info

        permalink_info = self.recover_missing_permalink(
            permalink_info,
            caption=caption,
            media_type=media_type,
        )
        comment_status = self.post_first_comment(
            comment_link,
            post_url=permalink_info["post_url"],
            prefer_permalink=True,
        )
        return comment_status, permalink_info

    @staticmethod
    def validate_facebook_permalink(url: str, post_type: str = "post") -> tuple[bool, str | None]:
        """
        Validate that the URL belongs to an allowed Facebook host and has a
        legitimate permalink path shape (posts, reels, permalink.php, watch, etc.).
        Returns (is_valid, normalized_clean_url).
        """
        if not url or not isinstance(url, str):
            return False, None
        url = url.strip()
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False, None

        if parsed.scheme not in ("http", "https"):
            return False, None

        host = (parsed.netloc or "").lower().split(":")[0]
        allowed_hosts = {
            "facebook.com",
            "www.facebook.com",
            "m.facebook.com",
            "web.facebook.com",
            "mbasic.facebook.com",
        }
        if host not in allowed_hosts and not any(host.endswith("." + h) for h in ("facebook.com",)):
            return False, None

        path = parsed.path or ""
        query = parsed.query or ""

        # Reject naked home/profile/login pages
        if path in ("", "/", "/me", "/home.php", "/login.php", "/checkpoint"):
            return False, None
        if re.match(r"^/profile\.php$", path):
            q_params = urllib.parse.parse_qs(query)
            if not ("story_fbid" in q_params or "fbid" in q_params):
                return False, None

        is_reel_pattern = (
            bool(re.search(r"^/reel/\d+", path))
            or bool(re.search(r"^/[^/]+/videos/\d+", path))
            or (path.startswith("/watch") and "v=" in query)
        )

        is_post_pattern = (
            bool(re.search(r"^/[^/]+/posts/[a-zA-Z0-9_-]+", path))
            or (path == "/permalink.php" and "story_fbid=" in query)
            or (path.startswith("/photo") and "fbid=" in query)
            or (path.startswith("/story.php") and "story_fbid=" in query)
            or bool(re.search(r"^/groups/[^/]+/permalink/\d+", path))
            or is_reel_pattern
        )

        valid = is_reel_pattern if post_type == "reel" else is_post_pattern
        if not valid:
            return False, None

        # Clean tracking query parameters while preserving story IDs
        keep_params = {"story_fbid", "id", "fbid", "v", "set"}
        q_dict = urllib.parse.parse_qs(query)
        clean_q = {k: v for k, v in q_dict.items() if k in keep_params}
        clean_query_str = urllib.parse.urlencode(clean_q, doseq=True) if clean_q else ""

        clean_url = urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            clean_query_str,
            "",
        ))
        return True, clean_url

    @staticmethod
    def _is_recent_timestamp_text(text: str) -> bool:
        """Recognize recent Facebook timestamps with bounded OCR-error tolerance."""
        clean = re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()
        if not clean:
            return False
        if clean in {"just now", "1m", "2m", "3m", "a few seconds ago", "moment ago"}:
            return True
        if re.fullmatch(r"\d+\s*(m|min|mins|minute|minutes|s|sec|secs|second|seconds)", clean):
            return True

        parts = clean.split()
        words = set(parts)
        now_like = bool(words & {"now", "n0w"})
        just_like = bool(words & {"just", "jusl", "jusi"})
        ago_like = bool(words & {"ago", "ag0", "a00", "aoo", "ano"})
        few_like = bool(words & {"few", "tew"})
        seconds_like = bool(words & {"second", "seconds", "sec", "secs"})
        numeric_time_like = any(
            re.fullmatch(r"\d+(m|min|mins|minute|minutes|s|sec|secs|second|seconds)", word)
            for word in words
        )
        expanded_recent_time = any(
            parts[index].isdigit()
            and int(parts[index]) <= 15
            and parts[index + 1] in {"m", "min", "mins", "minute", "minutes"}
            and parts[index + 2] in {"ago", "ag0", "a00", "aoo", "ano"}
            for index in range(max(0, len(parts) - 2))
        )
        moment_like = bool(words & {"moment", "mornent"})
        return (
            (just_like and now_like)
            or (seconds_like and (few_like or ago_like))
            or (numeric_time_like and ago_like)
            or expanded_recent_time
            or (moment_like and ago_like)
        )

    @timed_telemetry_step("permalink_correlation")
    def correlate_and_extract_permalink(
        self,
        caption: str | None = None,
        media_type: str = "post",
        max_scans: int = 4,
        time_budget_seconds: float | None = None,
    ) -> dict:
        """
        Navigate to profile, locate newly created post card via multi-signal correlation
        (caption tokens, recent timestamp, author), click the timestamp, and extract the URL.
        Returns a dict: { "post_url": str | None, "post_url_verified_at": str | None, "post_match_confidence": float }
        """
        empty_res = {
            "post_url": None,
            "post_url_verified_at": None,
            "post_match_confidence": 0.0,
        }
        deadline = (
            time.monotonic() + max(0.0, time_budget_seconds)
            if time_budget_seconds is not None
            else None
        )

        self.log("STEP", "Navigating to profile to correlate published post and extract permalink...")
        try:
            self.navigate_to("https://www.facebook.com/me", wait_seconds=3.0)
        except Exception as exc:
            self.log("WARN", f"Could not navigate to profile for permalink correlation: {exc}")
            return empty_res

        # Tokenize caption for correlation if provided
        search_tokens = set()
        if caption:
            normalized_cap = re.sub(r"[^a-z0-9]+", " ", caption.casefold()).strip()
            all_tokens = [w for w in normalized_cap.split() if len(w) >= 3]
            search_tokens = set(all_tokens[:8])

        for scan in range(1, max_scans + 1):
            if deadline is not None and time.monotonic() >= deadline:
                self.log("INFO", "Permalink correlation stopped at its time budget.")
                break
            screen = self.client.screenshot()
            ocr_items = self.vision.read_text(screen, min_confidence=0.18)

            caption_match_center = None
            timestamp_match_center = None
            timestamp_match_text = None
            timestamp_candidates = []
            matched_tokens_count = 0

            # Scan visible text items
            for item in ocr_items:
                text_clean = re.sub(r"[^a-z0-9]+", " ", item["text"].casefold()).strip()
                item_tokens = set(text_clean.split())

                # 1. Caption matching
                if search_tokens:
                    overlap = search_tokens.intersection(item_tokens)
                    if len(overlap) > matched_tokens_count and len(overlap) >= min(2, len(search_tokens)):
                        matched_tokens_count = len(overlap)
                        caption_match_center = item["center"]

                # 2. Timestamp matching
                if self._is_recent_timestamp_text(item["text"]):
                    timestamp_candidates.append(item)

            # Bind the timestamp to the matched post card. A recent timestamp
            # elsewhere on the feed must not be allowed to identify this post.
            if caption_match_center:
                cx, cy = caption_match_center
                nearby_timestamps = [
                    item for item in timestamp_candidates
                    if 0 <= cy - item["center"][1] <= 120
                    and abs(cx - item["center"][0]) <= 450
                ]
                if nearby_timestamps:
                    nearby_timestamps.sort(
                        key=lambda item: (
                            cy - item["center"][1],
                            -float(item.get("confidence", 0.0)),
                        )
                    )
                    timestamp_match_center = nearby_timestamps[0]["center"]
                    timestamp_match_text = nearby_timestamps[0]["text"]
            elif not search_tokens and timestamp_candidates:
                timestamp_match_center = timestamp_candidates[0]["center"]
                timestamp_match_text = timestamp_candidates[0]["text"]

            # If caption was matched, scan specifically in the header region above the caption for timestamp
            if caption_match_center and not timestamp_match_center:
                header_region = (
                    max(0, caption_match_center[0] - 220),
                    max(0, caption_match_center[1] - 80),
                    400,
                    70,
                )
                header_items = self.vision.read_text(screen, region=header_region, min_confidence=0.18)
                for h_item in header_items:
                    hx, hy = h_item["center"]
                    rx, ry, rw, rh = header_region
                    if (
                        rx <= hx <= rx + rw
                        and ry <= hy <= ry + rh
                        and self._is_recent_timestamp_text(h_item["text"])
                    ):
                        timestamp_match_center = h_item["center"]
                        timestamp_match_text = h_item["text"]
                        break

            # Compute correlation confidence
            confidence = 0.0
            if search_tokens and matched_tokens_count > 0:
                fraction = matched_tokens_count / max(1, len(search_tokens))
                confidence += 0.50 * min(1.0, fraction * 1.5)
            elif not search_tokens:
                confidence += 0.40

            if timestamp_match_center:
                confidence += 0.40

            self.log_decision(
                "Correlate published post",
                f"caption tokens={search_tokens or 'none'}, recent timestamp",
                f"scan={scan}, matched_tokens={matched_tokens_count}, ts_text={timestamp_match_text!r}, "
                f"ts_target={timestamp_match_center}, confidence={confidence:.2f}",
                "click timestamp link" if (confidence >= 0.50 and timestamp_match_center) else "scroll to next section",
                level="INFO",
            )

            # If we have a confident match with a clickable timestamp:
            if confidence >= 0.50 and timestamp_match_center:
                self.capture_evidence("before_permalink_click", screen, target=list(timestamp_match_center), confidence=round(confidence, 2))
                self.human.click(*timestamp_match_center)
                time.sleep(2.5)

                raw_url = self.client.get_current_url()
                is_valid, clean_url = self.validate_facebook_permalink(raw_url or "", post_type=media_type)

                after_click_screen = self.client.screenshot()
                self.capture_evidence(
                    "permalink_extraction",
                    after_click_screen,
                    raw_url=raw_url,
                    clean_url=clean_url,
                    is_valid=is_valid,
                    confidence=round(confidence, 2),
                )

                if is_valid and clean_url:
                    self.log("SUCCESS", f"Captured validated Facebook permalink: {clean_url} (confidence={confidence:.2f})")
                    return {
                        "post_url": clean_url,
                        "post_url_verified_at": datetime.now(timezone.utc).isoformat(),
                        "post_match_confidence": round(confidence, 2),
                    }
                else:
                    self.log("WARN", f"Clicked timestamp, but resulting URL was not a valid permalink: {raw_url}")

            # If not confident or timestamp not clickable, scroll down and scan next cards
            self.human.scroll("down", notches=3)
            if deadline is None:
                time.sleep(1.2)
            else:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                time.sleep(min(1.2, remaining))

        self.log("WARN", "Permalink correlation was ambiguous or not found; leaving post_url null.")
        self.capture_evidence("permalink_correlation_ambiguous")
        return empty_res

    def recover_missing_permalink(
        self,
        permalink_info: dict | None,
        *,
        caption: str | None,
        media_type: str,
        time_budget_seconds: float = 12.0,
    ) -> dict:
        """Run one short post-comment recovery pass only when the URL is missing."""
        result = dict(permalink_info or {})
        result.setdefault("post_url", None)
        result.setdefault("post_url_verified_at", None)
        result.setdefault("post_match_confidence", 0.0)
        if result.get("post_url"):
            result.update({
                "permalink_status": "captured",
                "permalink_missing": False,
                "permalink_recovery_attempted": False,
            })
            return result

        self.log(
            "INFO",
            f"Initial permalink was unavailable; starting one bounded recovery pass "
            f"after comment handling (budget={time_budget_seconds:.0f}s).",
        )
        recovered = self.correlate_and_extract_permalink(
            caption=caption,
            media_type=media_type,
            max_scans=2,
            time_budget_seconds=time_budget_seconds,
        )
        if recovered.get("post_url"):
            result.update(recovered)
            result.update({
                "permalink_status": "recovered",
                "permalink_missing": False,
                "permalink_recovery_attempted": True,
            })
            return result

        result.update({
            "permalink_status": "missing",
            "permalink_missing": True,
            "permalink_recovery_attempted": True,
        })
        self.log("WARN", "Permalink recovery ended without a confident URL; publication remains successful.")
        return result

    @staticmethod
    def permalink_not_requested() -> dict:
        """Return explicit metadata when no downstream action needs a permalink."""
        return {
            "post_url": None,
            "post_url_verified_at": None,
            "post_match_confidence": None,
            "permalink_status": "not_requested",
            "permalink_missing": False,
            "permalink_recovery_attempted": False,
        }

    def run_with_retry(self, max_retries: int = 2) -> bool:
        """Run task with retry logic and exponential backoff."""
        for attempt in range(1, max_retries + 2):
            try:
                self.log("INFO", f"Task execution attempt {attempt}/{max_retries + 1}...")
                success = self.run()
                if success:
                    return True
                self.log("WARN", f"Attempt {attempt} returned failure.")
            except Exception as e:
                self.log("ERROR", f"Attempt {attempt} raised exception: {e}")

            if attempt <= max_retries:
                wait_sec = 10 * attempt
                self.log("INFO", f"Backing off {wait_sec}s before retry...")
                time.sleep(wait_sec)

        self.log("ERROR", f"Task failed after {max_retries + 1} attempts.")
        return False

    def run(self) -> bool:
        """Execute the task. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement run()")
