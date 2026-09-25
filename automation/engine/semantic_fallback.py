"""Guarded Semantic Fallback Engine.

Implements Phase 3 from OPTIMIZATION_AND_SAFETY_PLAN.md:
  - Tier A: Structured semantic text ranking (no raw screenshot coordinate clicks).
  - Pluggable provider architecture: Heuristic (offline zero-dependency), Gemini, OpenAI.
  - Strict schema enforcement and hallucination rejection (valid candidate ID verification).
  - Deterministic pre-click guard (region boundaries, state recognition, fresh observation).
  - Shadow-mode rollout (default: True, records proposals without executing clicks).
  - Irreversible action gating: Final Publish action produces 'needs_review' rather than
    uncertain destructive click.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .screen_state import ScreenState, FacebookStateRecognizer
from .vision import VisionEngine


BLOCKED_SCREEN_PHRASES = (
    "captcha",
    "security check",
    "verify your account",
    "confirm your identity",
    "account restricted",
    "account suspended",
    "community standards",
)

EXPECTED_ACTION_STATES: dict[str, set[ScreenState]] = {
    "identify the next action": {ScreenState.MEDIA_READY},
    "identify the publish action": {ScreenState.POST_ENABLED},
}


def collect_action_candidates(
    vision: VisionEngine,
    screen: np.ndarray,
    region,
    min_confidence: float = 0.20,
) -> list[dict]:
    """Collect regional OCR plus native-resolution text from enabled CTAs."""
    candidates = list(vision.read_text(
        screen,
        region=region,
        min_confidence=min_confidence,
    ))
    try:
        buttons = vision.find_blue_action_buttons(screen=screen, region=region)
    except (AttributeError, TypeError):
        buttons = []
    for button in buttons if isinstance(buttons, list) else []:
        try:
            x1, y1, x2, y2 = button["bounds"]
            tight = vision.read_text(
                screen,
                region=(x1, y1, x2 - x1, y2 - y1),
                min_confidence=0.10,
            )
        except (KeyError, TypeError, ValueError):
            continue
        for item in tight:
            enriched = {**item, "region": "enabled_action"}
            normalized = SemanticFallbackEngine._normalize_text(str(item.get("text", "")))
            existing = next((
                candidate for candidate in candidates
                if SemanticFallbackEngine._normalize_text(str(candidate.get("text", ""))) == normalized
                and math.hypot(
                    candidate.get("center", (0, 0))[0] - item.get("center", (0, 0))[0],
                    candidate.get("center", (0, 0))[1] - item.get("center", (0, 0))[1],
                ) <= 40
            ), None)
            if existing is not None:
                existing.update(enriched)
            else:
                candidates.append(enriched)
    return candidates


@dataclass
class CandidateItem:
    id: str
    text: str
    region: str | tuple[int, int, int, int]
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]
    confidence: float = 1.0

    def to_structured_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "region": self.region if isinstance(self.region, str) else list(self.region),
        }


@dataclass
class SemanticProposal:
    candidate_id: str | None
    confidence: float
    reason: str
    latency_ms: float
    provider: str
    model: str
    shadow_mode: bool = True
    validated: bool = False
    validation_reason: str = ""
    fresh_candidate_confirmed: bool = False
    observed_state: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "latency_ms": round(self.latency_ms, 2),
            "provider": self.provider,
            "model": self.model,
            "shadow_mode": self.shadow_mode,
            "validated": self.validated,
            "validation_reason": self.validation_reason,
            "fresh_candidate_confirmed": self.fresh_candidate_confirmed,
            "observed_state": self.observed_state,
            "timestamp": self.timestamp,
        }


# Canonical synonym definitions for HeuristicProvider
ACTION_SYNONYM_MAP: dict[str, list[str]] = {
    "identify the next action": [
        "next", "continue", "proceed", "review", "forward", "edit reel", "next step",
    ],
    "identify the publish action": [
        "post", "publish", "share", "share now", "share to feed", "done", "confirm", "submit",
    ],
    "open the post composer": [
        "what's on your mind", "what’s on your mind", "whats on your mind", "write something",
        "create a post", "create post", "create public post",
    ],
    "open media picker": [
        "photo/video", "photo / video", "photo", "video", "photos/videos", "add photos", "photos",
    ],
    "add video": [
        "add video", "upload video", "select video", "add videos", "upload", "drag and drop",
    ],
    "describe reel": [
        "describe your reel", "describe your reel...", "write a caption", "description", "describe",
    ],
}


class BaseSemanticProvider:
    """Base interface for structured semantic text ranking providers."""
    name: str = "base"

    def rank(
        self,
        state: str,
        goal: str,
        candidates: list[dict],
    ) -> tuple[str | None, float, str, str]:
        """Return (candidate_id, confidence, reason, model_name)."""
        raise NotImplementedError


class HeuristicProvider(BaseSemanticProvider):
    """
    Built-in, zero-dependency offline semantic ranking provider.
    Evaluates token overlap and domain synonym dictionaries.
    Fast (~1-5ms), deterministic, 100% offline, zero API costs.
    """
    name: str = "heuristic"

    def rank(
        self,
        state: str,
        goal: str,
        candidates: list[dict],
    ) -> tuple[str | None, float, str, str]:
        if not candidates:
            return None, 0.0, "No candidates provided", "heuristic-v1"

        goal_key = goal.strip().lower()
        synonyms = ACTION_SYNONYM_MAP.get(goal_key, [])
        # If no exact goal mapping, use words from goal
        if not synonyms:
            synonyms = [w for w in re.findall(r"\b[a-z]{3,}\b", goal_key) if w not in ("identify", "action", "the")]

        best_id = None
        best_score = 0.0
        best_reason = ""

        for c in candidates:
            text = c.get("text", "").strip().lower()
            if not text:
                continue

            cleaned_text = re.sub(r"[^a-z0-9 ]+", " ", text).strip()
            words = set(cleaned_text.split())

            candidate_score = 0.0
            candidate_reason = ""
            # Exact and partial text matching remain deterministic. A label
            # independently observed inside an enabled CTA wins close ties over
            # similarly worded settings such as "Boost post" or "Share to story".
            for syn in synonyms:
                syn_cleaned = re.sub(r"[^a-z0-9 ]+", " ", syn).strip()
                if cleaned_text == syn_cleaned:
                    score = 0.98
                    reason = f"Exact match with synonym '{syn}'"
                elif syn_cleaned in cleaned_text:
                    score = 0.90
                    reason = f"Contains synonym substring '{syn}'"
                else:
                    syn_words = set(syn_cleaned.split())
                    if syn_words and syn_words.issubset(words):
                        score = 0.88
                        reason = f"Contains all words of synonym '{syn}'"
                    elif syn_words & words:
                        overlap_ratio = len(syn_words & words) / float(len(syn_words))
                        score = 0.60 + (0.25 * overlap_ratio)
                        reason = f"Partial word overlap with synonym '{syn}'"
                    else:
                        score = 0.0
                        reason = ""
                if score > candidate_score:
                    candidate_score, candidate_reason = score, reason

            if candidate_score and c.get("region") == "enabled_action":
                candidate_score = min(1.0, candidate_score + 0.02)
                candidate_reason = f"{candidate_reason}; confirmed inside enabled action"
            if candidate_score > best_score:
                best_score, best_id, best_reason = candidate_score, c["id"], candidate_reason

        if best_id and best_score >= 0.70:
            return best_id, best_score, best_reason, "heuristic-v1"

        return None, 0.0, "No confident candidate matched goal synonyms", "heuristic-v1"


class GeminiProvider(BaseSemanticProvider):
    """Google Gemini structured JSON ranking provider via REST API."""
    name: str = "gemini"

    def __init__(self, api_key: str | None = None, model: str = "gemini-1.5-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model

    def rank(
        self,
        state: str,
        goal: str,
        candidates: list[dict],
    ) -> tuple[str | None, float, str, str]:
        if not self.api_key:
            return None, 0.0, "GEMINI_API_KEY is not configured", self.model

        try:
            import requests

            prompt = (
                "You are an automated UI semantic assistant. "
                "Analyze the visible candidate UI labels and identify which candidate matches the target goal.\n"
                f"Screen State: {state}\n"
                f"Target Goal: {goal}\n"
                f"Candidates: {json.dumps(candidates)}\n\n"
                "Respond ONLY with a JSON object in this exact schema:\n"
                "{\n"
                '  "candidate_id": "c1", // MUST be one of the candidate IDs provided, or null if none match\n'
                '  "confidence": 0.95, // Float between 0.0 and 1.0\n'
                '  "reason": "Short explanation"\n'
                "}"
            )

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1,
                },
            }

            resp = requests.post(url, json=payload, timeout=5.0)
            if resp.status_code != 200:
                return None, 0.0, f"Gemini API returned HTTP {resp.status_code}: {resp.text[:100]}", self.model

            data = resp.json()
            text_resp = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text_resp)
            cid = parsed.get("candidate_id")
            conf = float(parsed.get("confidence", 0.0))
            reason = str(parsed.get("reason", "No reason provided"))
            return cid, conf, reason, self.model
        except Exception as exc:
            return None, 0.0, f"Gemini ranking failed: {exc}", self.model


class OpenAIProvider(BaseSemanticProvider):
    """OpenAI / OpenRouter structured JSON ranking provider via REST API."""
    name: str = "openai"

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini", api_url: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.api_url = api_url or os.environ.get("FALLBACK_API_URL", "https://api.openai.com/v1/chat/completions")

    def rank(
        self,
        state: str,
        goal: str,
        candidates: list[dict],
    ) -> tuple[str | None, float, str, str]:
        if not self.api_key:
            return None, 0.0, "OPENAI_API_KEY is not configured", self.model

        try:
            import requests

            prompt = (
                "You are an automated UI semantic assistant. "
                "Analyze the visible candidate UI labels and identify which candidate matches the target goal.\n"
                f"Screen State: {state}\n"
                f"Target Goal: {goal}\n"
                f"Candidates: {json.dumps(candidates)}\n\n"
                "Respond ONLY with a JSON object in this exact schema:\n"
                "{\n"
                '  "candidate_id": "c1", // MUST be one of the candidate IDs provided, or null if none match\n'
                '  "confidence": 0.95, // Float between 0.0 and 1.0\n'
                '  "reason": "Short explanation"\n'
                "}"
            )

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            }

            resp = requests.post(self.api_url, headers=headers, json=payload, timeout=5.0)
            if resp.status_code != 200:
                return None, 0.0, f"OpenAI API returned HTTP {resp.status_code}: {resp.text[:100]}", self.model

            data = resp.json()
            text_resp = data["choices"][0]["message"]["content"]
            parsed = json.loads(text_resp)
            cid = parsed.get("candidate_id")
            conf = float(parsed.get("confidence", 0.0))
            reason = str(parsed.get("reason", "No reason provided"))
            return cid, conf, reason, self.model
        except Exception as exc:
            return None, 0.0, f"OpenAI ranking failed: {exc}", self.model


class SemanticFallbackEngine:
    """
    Guarded Semantic Fallback Coordinator.

    Manages:
      - Provider selection (Heuristic default, Gemini, OpenAI).
      - Strict input/output schema validation.
      - Hallucination rejection (rejecting unknown candidate IDs).
      - Deterministic pre-click verification.
      - Shadow-mode enforcement.
      - Final-action safety gating ('needs_review' on irreversible publish actions).
    """

    def __init__(
        self,
        provider_name: str | None = None,
        shadow_mode: bool | None = None,
        min_confidence: float = 0.80,
    ):
        if provider_name is None:
            provider_name = os.environ.get("SEMANTIC_FALLBACK_PROVIDER", "heuristic").lower().strip()

        if shadow_mode is None:
            env_shadow = os.environ.get("SEMANTIC_FALLBACK_SHADOW_MODE", "true").lower().strip()
            shadow_mode = env_shadow not in ("false", "0", "no")

        self.shadow_mode = shadow_mode
        self.min_confidence = float(os.environ.get("SEMANTIC_FALLBACK_MIN_CONFIDENCE", min_confidence))

        # Instantiate provider
        if provider_name == "gemini":
            model = os.environ.get("SEMANTIC_FALLBACK_MODEL", "gemini-1.5-flash")
            self.provider: BaseSemanticProvider = GeminiProvider(model=model)
        elif provider_name == "openai":
            model = os.environ.get("SEMANTIC_FALLBACK_MODEL", "gpt-4o-mini")
            self.provider = OpenAIProvider(model=model)
        else:
            self.provider = HeuristicProvider()

    def rank_candidates(
        self,
        state: str,
        goal: str,
        raw_candidates: list[dict],
        expected_region: str | tuple[int, int, int, int] | tuple[float, float, float, float] | None = None,
    ) -> tuple[SemanticProposal | None, dict[str, CandidateItem]]:
        """
        Rank a list of OCR items against the target goal.
        Returns (proposal, candidate_map).
        """
        candidate_map: dict[str, CandidateItem] = {}
        structured_list: list[dict] = []

        for idx, item in enumerate(raw_candidates):
            cid = f"c{idx + 1}"
            bounds = item.get("bounds", (0, 0, 0, 0))
            center = item.get("center", ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2))
            cand = CandidateItem(
                id=cid,
                text=item.get("text", "").strip(),
                region=item.get("region") or expected_region or "unknown",
                bounds=bounds,
                center=center,
                confidence=float(item.get("confidence", 1.0)),
            )
            candidate_map[cid] = cand
            structured_list.append(cand.to_structured_dict())

        if not structured_list:
            return None, candidate_map

        start_time = time.time()
        cid, conf, reason, model_name = self.provider.rank(state, goal, structured_list)
        latency_ms = (time.time() - start_time) * 1000.0

        # Enforce the provider boundary before considering its answer. Provider
        # output may identify an input ID, but may never invent coordinates or
        # return malformed confidence values.
        schema_error = None
        if cid is not None and not isinstance(cid, str):
            schema_error = "candidate_id_must_be_string_or_null"
        try:
            conf = float(conf)
            if not math.isfinite(conf) or not 0.0 <= conf <= 1.0:
                schema_error = "confidence_must_be_between_0_and_1"
        except (TypeError, ValueError):
            conf = 0.0
            schema_error = "confidence_must_be_numeric"
        if not isinstance(reason, str):
            schema_error = "reason_must_be_string"
        if schema_error:
            proposal = SemanticProposal(
                candidate_id=None,
                confidence=0.0,
                reason=f"Rejected invalid provider response: {schema_error}",
                latency_ms=latency_ms,
                provider=self.provider.name,
                model=str(model_name),
                shadow_mode=self.shadow_mode,
                validated=False,
                validation_reason=f"provider_schema_invalid:{schema_error}",
            )
            return proposal, candidate_map

        # Strict hallucination guard: candidate_id must be in the input candidates!
        if cid is not None and cid not in candidate_map:
            proposal = SemanticProposal(
                candidate_id=None,
                confidence=0.0,
                reason=f"Rejected hallucinated candidate ID '{cid}' not in input set",
                latency_ms=latency_ms,
                provider=self.provider.name,
                model=str(model_name),
                shadow_mode=self.shadow_mode,
                validated=False,
                validation_reason="hallucinated_candidate_id",
            )
            return proposal, candidate_map

        proposal = SemanticProposal(
            candidate_id=cid,
            confidence=conf,
            reason=reason,
            latency_ms=latency_ms,
            provider=self.provider.name,
            model=str(model_name),
            shadow_mode=self.shadow_mode,
            validated=False,
            validation_reason="",
        )
        return proposal, candidate_map

    @staticmethod
    def blocked_observation_reason(observation) -> str | None:
        """Return a checkpoint reason before any external provider is invoked."""
        if observation.state == ScreenState.ERROR_DIALOG:
            return "screen_in_error_dialog"
        if observation.state == ScreenState.LOGIN_REQUIRED:
            return "screen_in_login_required"
        visible_text = " ".join(observation.text).casefold()
        blocker = next((phrase for phrase in BLOCKED_SCREEN_PHRASES if phrase in visible_text), None)
        return f"blocked_screen:{blocker}" if blocker else None

    def validate_proposal(
        self,
        proposal: SemanticProposal,
        candidate_map: dict[str, CandidateItem],
        current_screen: np.ndarray,
        expected_region: str | tuple[int, int, int, int] | tuple[float, float, float, float] | None = None,
        recognizer: FacebookStateRecognizer | None = None,
        is_reversible: bool = True,
        fresh_candidates: list[dict] | None = None,
        expected_states: set[ScreenState] | None = None,
        require_enabled_action: bool = True,
    ) -> tuple[bool, str, tuple[int, int] | None]:
        """
        Deterministic Pre-Click Guard (Section 6.2).

        Verifies:
          1. Candidate ID was valid and proposed with confidence >= min_confidence.
          2. The same OCR label still exists near the proposal in a fresh screenshot.
          3. Fresh bounds lie inside the permitted region.
          4. Screen state is expected, safe, and exposes an enabled action.
          5. Irreversible actions (is_reversible=False) produce 'needs_review' rather than clicking.
          6. Shadow mode blocks clicks while validating proposals.

        Returns: (allow_click, status_reason, target_coords)
        """
        if not proposal.candidate_id or proposal.candidate_id not in candidate_map:
            proposal.validated = False
            proposal.validation_reason = "no_valid_candidate"
            return False, "no_valid_candidate", None

        candidate = candidate_map[proposal.candidate_id]

        if proposal.confidence < self.min_confidence:
            proposal.validated = False
            proposal.validation_reason = f"low_confidence_{proposal.confidence:.2f}_below_{self.min_confidence:.2f}"
            return False, proposal.validation_reason, candidate.center

        # A semantic answer is only a proposal. Re-identify its exact OCR label
        # on a newly captured screenshot before trusting coordinates.
        if fresh_candidates is None:
            proposal.validated = False
            proposal.validation_reason = "fresh_observation_required"
            return False, proposal.validation_reason, None

        normalized_target = self._normalize_text(candidate.text)
        height, width = current_screen.shape[:2]
        max_dx = max(40, int(width * 0.05))
        max_dy = max(30, int(height * 0.05))
        fresh_match = None
        fresh_distance = None
        for item in fresh_candidates:
            if self._normalize_text(str(item.get("text", ""))) != normalized_target:
                continue
            bounds = tuple(item.get("bounds", (0, 0, 0, 0)))
            center = tuple(item.get("center", ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)))
            distance = abs(center[0] - candidate.center[0]) + abs(center[1] - candidate.center[1])
            if abs(center[0] - candidate.center[0]) <= max_dx and abs(center[1] - candidate.center[1]) <= max_dy:
                if fresh_distance is None or distance < fresh_distance:
                    fresh_match = CandidateItem(
                        id=candidate.id,
                        text=str(item.get("text", "")).strip(),
                        region=candidate.region,
                        bounds=bounds,
                        center=center,
                        confidence=float(item.get("confidence", 0.0)),
                    )
                    fresh_distance = distance
        if fresh_match is None:
            proposal.validated = False
            proposal.validation_reason = "candidate_missing_from_fresh_observation"
            return False, proposal.validation_reason, None
        candidate = fresh_match
        proposal.fresh_candidate_confirmed = True

        # Check fresh region bounds if expected_region is provided
        if expected_region is not None and current_screen is not None:
            px_region = VisionEngine.get_pixel_region(current_screen.shape, expected_region)
            rx, ry, rw, rh = px_region
            cx, cy = candidate.center
            # Element must be inside or immediately adjacent to expected region (with 15% tolerance)
            exp_region = VisionEngine.expand_region(current_screen.shape, px_region, ratio=0.15)
            erx, ery, erw, erh = exp_region
            if not (erx <= cx <= erx + erw and ery <= cy <= ery + erh):
                proposal.validated = False
                proposal.validation_reason = f"out_of_region_bounds_{candidate.center}_not_in_{px_region}"
                return False, proposal.validation_reason, candidate.center

        # Check screen state safety
        if recognizer is not None and current_screen is not None:
            observation = recognizer.observe(current_screen)
            proposal.observed_state = observation.state.value
            blocked_reason = self.blocked_observation_reason(observation)
            if blocked_reason:
                proposal.validated = False
                proposal.validation_reason = blocked_reason
                return False, proposal.validation_reason, candidate.center

            enabled_confirmed = "enabled blue action" in observation.signals
            if not enabled_confirmed:
                # For a localized or newly-worded CTA, the state recognizer may
                # know that the composer is open without knowing the action
                # label. Confirm enabled geometry around the freshly re-found
                # OCR candidate instead of trusting semantic coordinates.
                try:
                    buttons = recognizer.vision.find_blue_action_buttons(
                        screen=current_screen,
                        region=expected_region,
                    )
                    enabled_confirmed = bool(
                        isinstance(buttons, list)
                        and any(
                            math.hypot(
                                candidate.center[0] - button["center"][0],
                                candidate.center[1] - button["center"][1],
                            ) <= 180
                            for button in buttons
                        )
                    )
                except (AttributeError, KeyError, TypeError):
                    enabled_confirmed = False

            observed_state = observation.state
            if (
                expected_states
                and observed_state == ScreenState.COMPOSER_OPEN
                and enabled_confirmed
                and len(expected_states) == 1
            ):
                # Composer markers + a freshly grounded enabled CTA establish
                # the goal-specific ready state without requiring its English
                # label to be understood by the deterministic recognizer.
                observed_state = next(iter(expected_states))
                proposal.observed_state = f"{observation.state.value}->{observed_state.value}"

            if expected_states and observed_state not in expected_states:
                expected = ",".join(sorted(state.value for state in expected_states))
                proposal.validated = False
                proposal.validation_reason = (
                    f"unexpected_screen_state:{observed_state.value}:expected:{expected}"
                )
                return False, proposal.validation_reason, candidate.center
            if require_enabled_action and not enabled_confirmed:
                proposal.validated = False
                proposal.validation_reason = "enabled_action_not_confirmed"
                return False, proposal.validation_reason, candidate.center

        # Irreversible Action Safety Gate:
        # If the action is irreversible (like final Publish) and required semantic fallback,
        # never fire an unchecked click in production. Produce 'needs_review'.
        if not is_reversible:
            proposal.validated = False
            proposal.validation_reason = "irreversible_action_requires_review"
            return False, "needs_review", candidate.center

        # Shadow-mode check:
        if self.shadow_mode:
            proposal.validated = True
            proposal.validation_reason = "shadow_mode_active_click_blocked"
            return False, "shadow_mode_active", candidate.center

        proposal.validated = True
        proposal.validation_reason = "validated_clickable"
        return True, "validated", candidate.center

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
