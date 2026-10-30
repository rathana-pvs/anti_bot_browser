"""Base Task Class for Browser Automation."""

import json
import math
import os
import random
import time
import uuid
import numpy as np
from datetime import datetime
from engine.container_client import ContainerClient
from engine.runtime_paths import profiles_dir
from engine.evidence import EvidenceRecorder
from engine.human_input import HumanInput
from engine.behavior_profile import load_behavior_session
from modules.composer_entry import ComposerEntry
from modules.composer_review import ComposerReview
from engine.post_publish_prompt import PostPublishPromptHandler
from engine.screen_state import FacebookStateRecognizer, ScreenState, StateObservation
from engine.vision import VisionEngine
from engine.semantic_fallback import (
    EXPECTED_ACTION_STATES,
    SemanticFallbackEngine,
    collect_action_candidates,
)
from engine.telemetry import TelemetryRecorder
from engine.outcome import SUCCESS_STATUSES, PublicationStatus, resolve_status
from engine.resource_signal import read_signal
from skills.comment import (
    FirstComment,
    comment_match_confidence,
    first_comment_search_regions,
    submitted_comment_region,
)
from skills.dialogs import KnownDialogs
from skills.media import MediaAttachment
from skills.navigation import Navigation
from skills.natural_navigation import NaturalNavigation
from skills.permalink import (
    Permalink,
    is_recent_timestamp_text,
    permalink_not_requested,
    validate_facebook_permalink,
)
from skills.session import SessionCheck, has_strong_facebook_visual, is_facebook_url
from skills.text_entry import TextEntry


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
            os.environ.get("AUTOMATION_BEHAVIOR_RUN_ID") or f"{self.__class__.__name__}:{uuid.uuid4().hex}",
        )
        self.human = HumanInput(self.client, self.behavior_session)
        self.composer_entry = ComposerEntry(self)
        self.composer_review = ComposerReview(self.behavior_session)
        self.input_cancelled = False
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
        self.result_status = PublicationStatus.RUNNING.value
        self.result_error: str | None = None
        self.result_extra: dict = {}
        self.session_check_status = "not_checked"
        self.current_stage: str = "pending"
        self.stage_history: list[dict] = []
        self._reversible_click_bounds: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        self._load_environment_context()

    def _load_environment_context(self) -> None:
        """Load stable profile context; runtime screen observations fill the remaining fields."""
        config_path = profiles_dir() / self.profile_id / "config.json"
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

    def current_url(self) -> str | None:
        """The browser's URL; natural sessions read the address bar first."""
        natural = NaturalNavigation(self)
        return natural.current_url() if natural.enabled() else self.client.get_current_url()

    def rng(self, channel: str):
        """The account's seeded random stream, or ``random`` for task doubles."""
        session = getattr(self, "behavior_session", None)
        return session.stream(channel) if session is not None else random

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
        from engine.cloud_publish_guard import record_cloud_publish_intent
        record_cloud_publish_intent()
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
            actual = HumanInput.safe_click_point(bounds, max_offset_px=max_offset_px,
                                                       rng=self.behavior_session.stream("mouse") if hasattr(self, "behavior_session") else None)
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

    def publish_attempted(self) -> bool:
        """True once the publish click may have been sent."""
        return bool(
            getattr(self, "_publish_gate_consumed", False)
            or getattr(self, "current_stage", "") in ("publish_clicked", "verifying")
            or any(entry.get("stage") == "publish_clicked" for entry in getattr(self, "stage_history", []))
        )

    def _write_status(self, status, error: str | None) -> PublicationStatus:
        """The only place that assigns ``result_status``.

        Validates the status and applies the post-click rule, so no caller can
        report a plain failure after the publish click may have been sent.
        """
        resolved = resolve_status(status, publish_clicked=self.publish_attempted())
        self.result_status = resolved.value
        self.result_error = error
        return resolved

    def set_outcome(self, status: str, error: str | None = None, **extra) -> bool:
        """Persist a final outcome and return whether it represents publication."""
        resolved = self._write_status(status, error)
        self.set_stage(resolved.value, error=error, **extra)
        self.result_extra = extra
        if getattr(self, "_pipeline_defer_finalization", False):
            return resolved in SUCCESS_STATUSES
        self.finalize_outcome()
        return resolved in SUCCESS_STATUSES

    def update_outcome(self, status: str, error: str | None = None, **extra) -> None:
        """Change the status from a later pipeline module, keeping earlier details."""
        self._write_status(status, error)
        self.result_extra = {**getattr(self, "result_extra", {}), **extra}

    def workflow_steps(self, default_steps: tuple[str, ...]) -> tuple[str, ...] | None:
        """Steps in the order the pinned Brain workflow lists them.

        A workflow lists steps as states chained by ``on_success`` from
        ``initial_state``; that chain must match the engine's steps exactly.
        A workflow with any state that is not a step (an older installed
        Brain, whose states may share a name such as ``publish``) uses the
        built-in order. Returns None when the listed steps do not match.
        """
        workflow = getattr(self, "brain_workflow", None)
        if not workflow:
            return default_steps
        states = workflow.get("states", {})
        chain, name = [], workflow.get("initial_state")
        while name in states and name not in chain:
            chain.append(name)
            name = states[name].get("on_success")
        if not chain or not set(chain) <= set(default_steps):
            self.log("INFO", "The pinned workflow does not list steps; using the built-in step order.")
            return default_steps
        if tuple(chain) != tuple(default_steps):
            self.log("ERROR", f"The pinned workflow lists steps {chain}, expected {list(default_steps)}.")
            return None
        return tuple(chain)

    def run_steps(self, steps) -> bool:
        """Run workflow steps in order; each step is a ``_step_<name>`` method.

        A step returns None to continue, or the task result (from set_outcome,
        _fail and similar) to stop. Each step's outcome and duration are kept
        for the final record.
        """
        self.step_results = []
        for name in steps:
            held = self._hold_before_step(name)
            started = time.perf_counter()
            try:
                result = getattr(self, f"_step_{name}")()
            except Exception:
                self._record_step(name, started, "exception", held)
                raise
            if result is None:
                self._record_step(name, started, "completed", held)
                continue
            self._record_step(name, started, self.result_status, held)
            return result
        raise RuntimeError("Workflow steps ended without an outcome")

    # Longest wait before each step while the machine is under memory
    # pressure. Steps not listed never wait; nothing waits after the publish
    # click. Subclasses list only steps where pausing is safe.
    HOLD_LIMITS: dict = {}
    MAX_TOTAL_HOLD_SECONDS = 180.0

    def resource_signal(self) -> dict:
        return read_signal()

    def _hold_before_step(self, name: str) -> float:
        limit = self.HOLD_LIMITS.get(name)
        if not limit or self.publish_attempted():
            return 0.0
        held_so_far = sum(item.get("held_seconds", 0.0) for item in getattr(self, "step_results", []))
        limit = min(limit, self.MAX_TOTAL_HOLD_SECONDS - held_so_far)
        started = time.monotonic()
        signal = self.resource_signal()
        if not signal["hold"] or limit <= 0:
            return 0.0
        self.log("INFO", f"Pausing before {name}: {signal['reason'] or 'the machine is under pressure'}.")
        while signal["hold"] and time.monotonic() - started < limit:
            # Uneven waits, so held accounts do not all resume together.
            time.sleep(min(limit - (time.monotonic() - started), self.rng("pause").uniform(3.0, 6.0)))
            signal = self.resource_signal()
        held = round(time.monotonic() - started, 1)
        self.log("INFO", f"Resuming {name} after {held:.0f}s.")
        return held

    def _record_step(self, name: str, started: float, outcome: str, held: float = 0.0) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
        entry = {"step": name, "outcome": outcome, "duration_ms": duration_ms}
        if held:
            entry["held_seconds"] = held
        self.step_results.append(entry)
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.record_step("workflow_step", duration_ms, outcome=outcome, step=name)

    def finalize_outcome(self, **extra) -> None:
        """Write the final task record once all pipeline modules have finished."""
        if getattr(self, "_outcome_finalized", False):
            return
        self._outcome_finalized = True
        merged_extra = {**self.result_extra, **extra}
        if getattr(self, "step_results", None):
            merged_extra.setdefault("workflow_steps", list(self.step_results))
        if getattr(self, "_natural_navigation_counts", None):
            merged_extra.setdefault("navigation_counts", dict(self._natural_navigation_counts))
        if getattr(self, "reel_upload_extension", None):
            merged_extra.setdefault("upload_extension", dict(self.reel_upload_extension))
        if getattr(self, "facebook_identity", None):
            merged_extra.setdefault("facebook_identity", self.facebook_identity)
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
        return KnownDialogs(self).leave_site(screen)

    def dismiss_meta_account_notice(self, screen=None) -> str:
        return KnownDialogs(self).dismiss_meta_account_notice(screen)

    def find_post_publish_prompt(self, screen=None):
        return KnownDialogs(self).find_post_publish_prompt(screen)

    def check_and_dismiss_post_prompt(self, screen=None) -> bool:
        return KnownDialogs(self).dismiss_post_prompt(screen)

    def handle_post_publish_prompt(self, screen=None) -> str:
        return KnownDialogs(self).handle_post_publish_prompt(screen)

    def navigate_to(
        self,
        url: str,
        wait_seconds: float = 2.0,
        check_leave_dialog: bool = True,
    ) -> None:
        Navigation(self).go_to(url, wait_seconds, check_leave_dialog)

    def open_warming_surface(
        self,
        surface: str,
        timeout: float = 15.0,
        *,
        navigate: bool = True,
    ):
        return Navigation(self).open_surface(surface, timeout, navigate=navigate)

    def select_and_open_warming_surface(
        self,
        requested: str | None = None,
        already_open: str | None = None,
    ):
        return Navigation(self).select_and_open_surface(requested, already_open)

    def refresh_page(self, wait_seconds: float = 2.0) -> None:
        Navigation(self).refresh(wait_seconds)

    def paste_text(
        self,
        text: str,
        *,
        paste_key: str = "ctrl+v",
        selection: str = "clipboard",
        expected_owner=None,
    ) -> None:
        TextEntry(self).paste(
            text, paste_key=paste_key, selection=selection, expected_owner=expected_owner,
        )

    def review_composer(self, caption: str, media_revision: str = "", image_count: int = 0):
        TextEntry(self).review(caption, media_revision, image_count)

    @staticmethod
    def _is_facebook_url(url: str | None) -> bool:
        return is_facebook_url(url)

    @staticmethod
    def _has_strong_facebook_visual(observation: StateObservation) -> bool:
        return has_strong_facebook_visual(observation)

    def skip_unverified_session(self) -> bool:
        return SessionCheck(self).skip_unverified()

    def verify_logged_in(self, target_url: str = "https://www.facebook.com/") -> bool:
        return SessionCheck(self).verify(target_url)

    def find_visible_file_chooser(self) -> tuple[str, str] | None:
        return MediaAttachment(self).find_chooser()

    def attach_file_gtk(
        self,
        file_path: str,
        chooser_window: tuple[str, str] | None = None,
    ) -> bool:
        return MediaAttachment(self).attach(file_path, chooser_window)

    @staticmethod
    def _first_comment_search_regions(screen):
        return first_comment_search_regions(screen)

    @staticmethod
    def _submitted_comment_region(screen, comment_box_pos):
        return submitted_comment_region(screen, comment_box_pos)

    def _find_first_comment_input(self, screen=None, region=None):
        return FirstComment(self).find_input(screen, region)

    def _find_first_comment_action(self, screen):
        return FirstComment(self).find_action(screen)

    def _find_first_comment_targets(self, screen, *, allow_broad_fallback=True):
        return FirstComment(self).find_targets(screen, allow_broad_fallback=allow_broad_fallback)

    def _open_profile_first_comment_input(self, *, navigate: bool = True):
        return FirstComment(self).open_profile_input(navigate=navigate)

    def _open_permalink_comment_input(self, post_url: str | None):
        return FirstComment(self).open_permalink_input(post_url)

    @staticmethod
    def _comment_match_confidence(comment_text: str, ocr_items: list[dict], max_y: int) -> float:
        return comment_match_confidence(comment_text, ocr_items, max_y)

    def post_first_comment(
        self,
        comment_link: str,
        post_url: str | None = None,
        *,
        reuse_profile_page: bool = False,
        prefer_permalink: bool = False,
        warm_down_after_submit: bool = True,
    ) -> str:
        return FirstComment(self).post(
            comment_link,
            post_url,
            reuse_profile_page=reuse_profile_page,
            prefer_permalink=prefer_permalink,
            warm_down_after_submit=warm_down_after_submit,
        )

    def post_first_comment_with_page_reuse(
        self,
        comment_link: str,
        *,
        caption: str | None,
        media_type: str,
    ) -> tuple[str, dict]:
        return FirstComment(self).post_with_page_reuse(
            comment_link, caption=caption, media_type=media_type,
        )

    @staticmethod
    def validate_facebook_permalink(url: str, post_type: str = "post") -> tuple[bool, str | None]:
        return validate_facebook_permalink(url, post_type)

    @staticmethod
    def _is_recent_timestamp_text(text: str) -> bool:
        return is_recent_timestamp_text(text)

    def correlate_and_extract_permalink(
        self,
        caption: str | None = None,
        media_type: str = "post",
        max_scans: int = 4,
        time_budget_seconds: float | None = None,
    ) -> dict:
        return Permalink(self).correlate(caption, media_type, max_scans, time_budget_seconds)

    def recover_missing_permalink(
        self,
        permalink_info: dict | None,
        *,
        caption: str | None,
        media_type: str,
        time_budget_seconds: float = 12.0,
    ) -> dict:
        return Permalink(self).recover_missing(
            permalink_info,
            caption=caption,
            media_type=media_type,
            time_budget_seconds=time_budget_seconds,
        )

    @staticmethod
    def permalink_not_requested() -> dict:
        return permalink_not_requested()

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
