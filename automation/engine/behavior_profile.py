"""Bounded, reproducible interaction behavior for one automation run."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any


BEHAVIOR_VERSION = 1


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


class BehaviorSession:
    """Independent deterministic random streams derived for a single run."""

    CHANNELS = ("mouse", "click", "typing", "scroll", "pause", "warming")

    def __init__(self, mode: str, profile_seed: int, run_id: str):
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

    @property
    def seed_hash(self) -> str:
        return self._session_digest[:12]

    def stream(self, channel: str) -> random.Random:
        try:
            return self._streams[channel]
        except KeyError as exc:
            raise ValueError(f"Unknown behavior channel: {channel}") from exc

    def scaled_duration(self, channel: str, value: float) -> float:
        low, high = self.preset.duration_scale
        return max(0.0, float(value) * self.stream(channel).uniform(low, high))

    def typing_speed(self) -> int:
        return self.stream("typing").randint(*self.preset.typing_wpm)

    def warming_scroll_count(self) -> int:
        return self.stream("warming").randint(*self.preset.warming_scrolls)

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "version": self.version,
            "seed_hash": self.seed_hash,
            "typing_wpm_range": list(self.preset.typing_wpm),
            "warming_scroll_range": list(self.preset.warming_scrolls),
        }


def load_behavior_session(
    profile_id: str,
    run_id: str,
    *,
    profiles_root: Path | None = None,
) -> BehaviorSession:
    root = profiles_root or Path(__file__).resolve().parents[2] / "profiles"
    config_path = root / profile_id / "config.json"
    mode = "medium"
    seed = int.from_bytes(sha256(profile_id.encode("utf-8")).digest()[:8], "big")
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        configured_mode = str(value.get("behavior_mode") or "medium").casefold()
        mode = configured_mode if configured_mode in PRESETS else "medium"
        configured_seed = value.get("behavior_seed")
        if isinstance(configured_seed, int) and not isinstance(configured_seed, bool) and configured_seed > 0:
            seed = configured_seed
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return BehaviorSession(mode, seed, run_id)

