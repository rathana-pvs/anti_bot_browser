"""Bounded, reproducible interaction behavior for one automation run."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any
from .runtime_paths import profiles_dir


BEHAVIOR_VERSION = 2


@dataclass(frozen=True)
class BehaviorPreset:
    mode: str
    duration_scale: tuple[float, float]
    typing_wpm: tuple[int, int]
    warming_scrolls: tuple[int, int]


PRESETS = {
    "fast": BehaviorPreset("fast", (0.72, 0.90), (58, 72), (1, 2)),
    "medium": BehaviorPreset("medium", (0.95, 1.10), (45, 62), (2, 5)),
    "slow": BehaviorPreset("slow", (1.25, 1.60), (32, 48), (4, 8)),
}

NAVIGATION_HABITS = ("sidebar", "avatar", "typed")


@dataclass(frozen=True)
class Persona:
    """Stable habits of one account, derived from its seed alone.

    Used only when the account's natural session switch is on. Milestone 5
    traits (pointer speed, overshoot, typos) are stored here for later use.
    """

    reading_speed: float          # multiplies reading pauses; >1 reads slower
    scroll_notches: tuple[int, int]
    reread_rate: float
    preparation_scale: float      # multiplies the batch's preparation time
    preparation_skip_rate: float
    linger_rate: float
    linger_seconds: tuple[float, float]
    navigation_weights: tuple[float, float, float]  # sidebar, avatar, typed
    paste_preference: float
    active_hours: tuple[int, int]  # local start hour, length in hours
    pointer_speed: float
    overshoot_rate: float
    typo_rate: float

    @classmethod
    def from_seed(cls, profile_seed: int) -> "Persona":
        rng = random.Random(int.from_bytes(sha256(f"persona|{int(profile_seed)}".encode()).digest()[:8], "big"))
        low = rng.randint(1, 3)
        linger_low = round(rng.uniform(8.0, 25.0), 1)
        sidebar, avatar = rng.uniform(0.3, 0.7), rng.uniform(0.15, 0.5)
        return cls(
            reading_speed=round(rng.uniform(0.75, 1.35), 3),
            scroll_notches=(low, low + rng.randint(1, 3)),
            reread_rate=round(rng.uniform(0.05, 0.30), 3),
            preparation_scale=round(rng.uniform(0.6, 1.0), 3),
            preparation_skip_rate=round(rng.uniform(0.0, 0.25), 3),
            linger_rate=round(rng.uniform(0.25, 0.75), 3),
            linger_seconds=(linger_low, round(min(60.0, linger_low + rng.uniform(10.0, 35.0)), 1)),
            navigation_weights=(round(sidebar, 3), round(avatar, 3), round(rng.uniform(0.05, 0.2), 3)),
            paste_preference=round(rng.uniform(0.2, 0.8), 3),
            active_hours=(rng.randint(6, 11), rng.randint(10, 15)),
            pointer_speed=round(rng.uniform(0.8, 1.25), 3),
            overshoot_rate=round(rng.uniform(0.05, 0.35), 3),
            typo_rate=round(rng.uniform(0.0, 0.03), 4),
        )

    def summary(self) -> dict[str, Any]:
        return {name: list(value) if isinstance(value, tuple) else value for name, value in self.__dict__.items()}


class BehaviorSession:
    """Independent deterministic random streams derived for a single run."""

    CHANNELS = (
        "mouse", "click", "typing", "scroll", "pause", "warming", "entry_strategy", "review", "baseline",
        "session",
    )

    def __init__(self, mode: str, profile_seed: int, run_id: str, *, natural: bool = False):
        self.preset = PRESETS.get(mode, PRESETS["medium"])
        self.mode = self.preset.mode
        self.version = BEHAVIOR_VERSION
        source = f"{int(profile_seed)}|{run_id}|{self.version}".encode("utf-8")
        self._session_digest = sha256(source).hexdigest()
        self._streams = {
            channel: random.Random(
                int.from_bytes(sha256(source + b"|" + channel.encode()).digest()[:8], "big")
            )
            for channel in self.CHANNELS
        }

        baseline = self.stream("baseline")
        self.pace = baseline.uniform(*self.preset.duration_scale)
        self.wpm = baseline.randint(*self.preset.typing_wpm)
        # Natural session: stable per-account habits, plus small per-run drift
        # from the run-seeded "session" stream. Existing streams are unchanged.
        self.natural = bool(natural)
        self.persona = Persona.from_seed(profile_seed)
        self.drift = self.stream("session").uniform(0.9, 1.1)

    @property
    def seed_hash(self) -> str:
        return self._session_digest[:12]

    def stream(self, channel: str) -> random.Random:
        try:
            return self._streams[channel]
        except KeyError as exc:
            raise ValueError(f"Unknown behavior channel: {channel}") from exc

    def scaled_duration(self, channel: str, value: float) -> float:
        return max(0.0, float(value) * self.pace)

    def typing_speed(self) -> int:
        return self.wpm

    def chance(self, rate: float) -> bool:
        """A per-run decision with this probability, from the session stream."""
        return self.stream("session").random() < min(1.0, max(0.0, rate * self.drift))

    def session_seconds(self, low: float, high: float) -> float:
        """A per-run duration in [low, high], drifted and paced for this account."""
        value = self.stream("session").uniform(low, high) * self.drift * self.pace
        return min(high, max(low, value))

    def warming_scroll_count(self) -> int:
        return self.stream("warming").randint(*self.preset.warming_scrolls)

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "version": self.version,
            "session_wpm": self.wpm,
            "session_pace": round(self.pace, 3),
            "seed_hash": self.seed_hash,
            "typing_wpm_range": list(self.preset.typing_wpm),
            "warming_scroll_range": list(self.preset.warming_scrolls),
            "natural_session": self.natural,
            **({"persona": self.persona.summary(), "session_drift": round(self.drift, 3)} if self.natural else {}),
        }


def load_behavior_session(
    profile_id: str,
    run_id: str,
    *,
    profiles_root: Path | None = None,
) -> BehaviorSession:
    root = profiles_root if profiles_root is not None else profiles_dir()
    config_path = root / profile_id / "config.json"
    mode = "medium"
    natural = False
    seed = int.from_bytes(sha256(profile_id.encode("utf-8")).digest()[:8], "big")
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        configured_mode = str(value.get("behavior_mode") or "medium").casefold()
        mode = configured_mode if configured_mode in PRESETS else "medium"
        configured_seed = value.get("behavior_seed")
        if isinstance(configured_seed, int) and not isinstance(configured_seed, bool) and configured_seed > 0:
            seed = configured_seed
        automation = value.get("automation")
        natural = isinstance(automation, dict) and automation.get("natural_session") is True
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        pass
    return BehaviorSession(mode, seed, run_id, natural=natural)

