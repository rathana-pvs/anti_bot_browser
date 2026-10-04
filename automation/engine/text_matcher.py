"""Reusable scored matching for short OCR text phrases."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass(frozen=True)
class TextMatchResult:
    expected: str
    received: str
    normalized_expected: str
    normalized_received: str
    score: float
    threshold: float
    matched: bool
    required_tokens_present: bool
    components: dict[str, float]


class OcrTextMatcher:
    """Evaluate OCR text without making UI or click-safety decisions."""

    def __init__(self, default_threshold: float = 0.78, min_fuzzy_characters: int = 8):
        if not 0.0 <= default_threshold <= 1.0:
            raise ValueError("default_threshold must be between 0 and 1")
        self.default_threshold = default_threshold
        self.min_fuzzy_characters = max(1, int(min_fuzzy_characters))

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()

    def evaluate(
        self,
        *,
        expected: str,
        received: str,
        threshold: float | None = None,
        required_tokens: tuple[str, ...] = (),
    ) -> TextMatchResult:
        accepted_at = self.default_threshold if threshold is None else float(threshold)
        if not 0.0 <= accepted_at <= 1.0:
            raise ValueError("threshold must be between 0 and 1")

        target = self.normalize(expected)
        candidate = self.normalize(received)
        required = {self.normalize(token) for token in required_tokens if self.normalize(token)}
        candidate_tokens = set(candidate.split())
        required_present = all(
            required_token in candidate_tokens
            or (
                len(required_token) >= 4
                and any(
                    SequenceMatcher(None, required_token, candidate_token).ratio() >= 0.75
                    for candidate_token in candidate_tokens
                )
            )
            for required_token in required
        )

        components = {
            "characters": 0.0,
            "word_order": 0.0,
            "start": 0.0,
            "end": 0.0,
            "length": 0.0,
        }
        if not target or not candidate:
            score = 0.0
        elif target == candidate:
            score = 1.0
            components = {name: 1.0 for name in components}
        elif len(target) < self.min_fuzzy_characters:
            # Fuzzy matching short labels can confuse irreversible actions such
            # as Post, Boost, and Next, so only exact normalized text qualifies.
            score = 0.0
        else:
            window = min(8, len(target), len(candidate))
            components = {
                "characters": SequenceMatcher(None, target, candidate).ratio(),
                "word_order": SequenceMatcher(
                    None,
                    target.split(),
                    candidate.split(),
                ).ratio(),
                "start": SequenceMatcher(
                    None,
                    target[:window],
                    candidate[:window],
                ).ratio(),
                "end": SequenceMatcher(
                    None,
                    target[-window:],
                    candidate[-window:],
                ).ratio(),
                "length": min(len(target), len(candidate)) / max(len(target), len(candidate)),
            }
            score = (
                components["characters"] * 0.45
                + components["word_order"] * 0.20
                + components["start"] * 0.15
                + components["end"] * 0.15
                + components["length"] * 0.05
            )

        score = round(score, 6)
        return TextMatchResult(
            expected=expected,
            received=received,
            normalized_expected=target,
            normalized_received=candidate,
            score=score,
            threshold=accepted_at,
            matched=required_present and score >= accepted_at,
            required_tokens_present=required_present,
            components={name: round(value, 6) for name, value in components.items()},
        )
