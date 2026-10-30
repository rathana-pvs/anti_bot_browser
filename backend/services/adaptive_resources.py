"""Adaptive resource control: how many jobs run, how fast containers start,
and when running jobs hold at safe steps.

It only admits or holds work. It never stops a running job; the publish
click, verification and first comment are never held (the automation enforces
that), and idle prepared containers are already stopped by the 120-second
preparation handoff.
"""

import math
import time
from dataclasses import asdict, dataclass

DEFAULT_CONTAINER_MB = 1500.0
CALM_SECONDS_BEFORE_GROWING = 120.0
ABSOLUTE_MAX_PARALLEL = 16
START_SPACING_MS = {"calm": 8000, "busy": 20000, "pressure": 30000}
ELASTIC_SCALE = {"calm": 1.0, "busy": 0.75, "pressure": 0.5}


@dataclass(frozen=True)
class Decision:
    state: str                    # calm, busy or pressure
    limit: int                    # parallel jobs
    max_publishers: int
    max_preparers: int
    max_containers: int
    ceiling: int
    start_spacing_ms: int
    room_for_one: bool            # one more container fits the targets
    admit: bool                   # room for one, and start spacing has passed
    hold: bool                    # running jobs wait at their next safe step
    elastic_scale: float          # scales flexible phases (preparation, staying)
    reason: str
    predicted_memory_percent: float
    container_memory_mb: float

    def summary(self) -> dict:
        return asdict(self)


def split_limit(limit: int) -> tuple[int, int, int]:
    """Publishers, preparers and containers for a parallel-job limit.

    Matches the old fixed modes: 2 -> 1/2/2, 4 -> 2/3/4, 6 -> 3/5/6.
    """
    limit = max(1, int(limit))
    return max(1, limit // 2), max(1, min(limit, limit - 1 if limit > 2 else limit)), limit


class AdaptiveController:
    def __init__(self, *, max_parallel: int | None = None, reserve_percent: int = 10,
                 start_limit: int = 2, clock=time.monotonic):
        self.max_parallel = max_parallel
        self.reserve_percent = reserve_percent
        self.limit = max(1, start_limit)
        self.clock = clock
        self.calm_since = None
        self.container_mb = DEFAULT_CONTAINER_MB
        self.last: Decision | None = None

    def configure(self, *, max_parallel: int | None, reserve_percent: int) -> None:
        self.max_parallel = max_parallel
        self.reserve_percent = reserve_percent

    # Targets leave the user's reserve free on top of the usual headroom.
    def _targets(self) -> dict:
        reserve = max(0, min(40, int(self.reserve_percent)))
        return {"memory": 75 - reserve, "cpu": 70 - reserve}

    def _learn_container_memory(self, containers: dict) -> None:
        values = [item["memory_mb"] for item in containers.values() if item.get("memory_mb")]
        if values:
            observed = sum(values) / len(values)
            # Slow moving average; containers grow while Chrome loads pages.
            self.container_mb = 0.8 * self.container_mb + 0.2 * max(self.container_mb * 0.5, observed)

    def _ceiling(self, sample: dict) -> int:
        target = self._targets()["memory"] / 100
        by_memory = max(1, math.floor(sample["memory_total_mb"] * target / max(1.0, self.container_mb)))
        user = self.max_parallel or ABSOLUTE_MAX_PARALLEL
        return max(1, min(by_memory, user, ABSOLUTE_MAX_PARALLEL))

    def _state(self, sample: dict) -> tuple[str, str, bool]:
        targets = self._targets()
        psi = sample.get("psi") or {}

        def pressure(resource, kind="some"):
            return ((psi.get(resource) or {}).get(kind)) or 0.0

        memory, cpu = sample["memory_percent"], sample["cpu_percent"]
        gpu_memory = sample.get("gpu_memory_percent") or 0.0
        severe = memory >= targets["memory"] + 15 or pressure("memory") >= 20 or pressure("memory", "full") >= 5
        if severe:
            return "pressure", f"memory is under pressure ({memory:.0f}% used)", True
        if memory >= targets["memory"] + 10 or pressure("memory") >= 10:
            return "pressure", f"memory {memory:.0f}% is above its {targets['memory']}% target", False
        if cpu >= targets["cpu"] + 20 or pressure("cpu") >= 40 or pressure("io") >= 30:
            return "pressure", f"the machine is overloaded (CPU {cpu:.0f}%)", False
        if gpu_memory >= 92:
            return "pressure", f"GPU memory {gpu_memory:.0f}% is nearly full", False
        if memory >= targets["memory"]:
            return "busy", f"memory {memory:.0f}% is at its {targets['memory']}% target", False
        if cpu >= targets["cpu"] or pressure("cpu") >= 10 or pressure("io") >= 15 or pressure("memory") >= 3:
            return "busy", f"CPU {cpu:.0f}% or waiting time is above target", False
        return "calm", "resources available", False

    def decide(self, sample: dict, *, running_jobs: int, since_last_start_ms: float = math.inf) -> Decision:
        now = self.clock()
        self._learn_container_memory(sample.get("containers") or {})
        ceiling = self._ceiling(sample)
        state, reason, hold = self._state(sample)
        targets = self._targets()
        predicted = (sample["memory_total_mb"] - sample["memory_available_mb"] + self.container_mb) \
            / max(1, sample["memory_total_mb"]) * 100

        if state == "pressure":
            # Shrink fast: never below one job, never above what is running.
            self.limit = max(1, min(self.limit, math.floor(max(1, running_jobs) * 0.75)))
            self.calm_since = None
        elif state == "busy":
            self.calm_since = None
        else:
            self.calm_since = self.calm_since if self.calm_since is not None else now
            grow = (now - self.calm_since >= CALM_SECONDS_BEFORE_GROWING
                    and running_jobs >= self.limit and predicted < targets["memory"])
            if grow and self.limit < ceiling:
                self.limit += 1
                self.calm_since = now
                reason = f"calm for {CALM_SECONDS_BEFORE_GROWING:.0f}s; allowing {self.limit} parallel jobs"
        self.limit = min(self.limit, ceiling)

        spacing = START_SPACING_MS[state]
        room_for_one = state != "pressure" and predicted < targets["memory"] + 5
        admit = room_for_one and since_last_start_ms >= spacing
        if state != "pressure" and predicted >= targets["memory"] + 5:
            reason = f"one more container would reach {predicted:.0f}% memory"
        elif state != "pressure" and since_last_start_ms < spacing:
            reason = "spacing container starts"
        publishers, preparers, containers = split_limit(self.limit)
        self.last = Decision(
            state=state, limit=self.limit, max_publishers=publishers, max_preparers=preparers,
            max_containers=containers, ceiling=ceiling, start_spacing_ms=spacing, room_for_one=room_for_one,
            admit=admit, hold=hold,
            elastic_scale=ELASTIC_SCALE[state], reason=reason,
            predicted_memory_percent=round(predicted, 1), container_memory_mb=round(self.container_mb),
        )
        return self.last
