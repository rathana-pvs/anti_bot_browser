"""Runs the adaptive controller against live samples and publishes its decision.

Every few seconds this samples the host, decides, and writes the signal file
that running automation reads before its safe steps. The decision only
changes behavior when the resource mode is Automatic; in a fixed mode it is
still computed and shown as what Automatic would do, and the signal stays
neutral.
"""

import json
import math
import time

from backend.config import DATA_DIR
from backend.services import docker_service, host_pressure
from backend.services.adaptive_resources import AdaptiveController, Decision
from backend.services.resource_service import settings_manager

SIGNAL_FILE = DATA_DIR / "resources" / "signal.json"
NEUTRAL_SIGNAL = {"hold": False, "elastic_scale": 1.0, "publish_slots_full": False, "reason": "fixed_mode"}

controller = AdaptiveController()
_decision: Decision | None = None
_sample: dict | None = None


def adaptive_enabled() -> bool:
    return settings_manager.resource_mode == "auto"


def _running_leases() -> list:
    from backend.services.automation_service import active_in_memory_scheduler_leases
    return active_in_memory_scheduler_leases()


def tick() -> Decision:
    global _decision, _sample
    controller.configure(max_parallel=settings_manager.max_parallel,
                         reserve_percent=settings_manager.reserve_percent)
    _sample = host_pressure.sample(
        cpu_percent=docker_service.sustained_cpu_percent(),
        gpu_percent=docker_service.get_current_gpu_percent(),
        gpu_memory_percent=docker_service.current_gpu_memory_percent,
        container_stats=docker_service.get_all_container_stats(),
    )
    leases = _running_leases()
    since_last_start = time.time() * 1000 - docker_service.last_queue_container_start_at
    _decision = controller.decide(_sample, running_jobs=len(leases), since_last_start_ms=since_last_start)
    publishers = sum(1 for lease in leases if lease.get("kind") == "publisher")
    signal = NEUTRAL_SIGNAL
    if adaptive_enabled():
        signal = {
            "hold": _decision.hold,
            "elastic_scale": _decision.elastic_scale,
            "publish_slots_full": publishers >= _decision.max_publishers,
            "reason": _decision.reason,
        }
    _write_signal({**signal, "updated_at": time.time()})
    return _decision


def _write_signal(value: dict) -> None:
    try:
        SIGNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SIGNAL_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(SIGNAL_FILE)
    except OSError:
        pass


def current_decision() -> Decision:
    return _decision if _decision is not None else tick()


def current_start_spacing_ms() -> int:
    if adaptive_enabled():
        return current_decision().start_spacing_ms
    return docker_service.CONTAINER_START_SPACING_MS


def admission() -> dict:
    """Live start admission in Automatic mode (spacing is rechecked now)."""
    decision = current_decision()
    since = time.time() * 1000 - docker_service.last_queue_container_start_at
    if decision.state == "pressure":
        return {"allowed": False, "reason": "resource_pressure", "memoryPaused": True}
    if not decision.room_for_one:
        return {"allowed": False, "reason": "adaptive_memory_forecast", "memoryPaused": False}
    if since < decision.start_spacing_ms:
        return {"allowed": False, "reason": "container_start_spacing", "memoryPaused": False}
    return {"allowed": True, "reason": "resources_available", "memoryPaused": False}


def snapshot() -> dict:
    decision = current_decision()
    return {"enabled": adaptive_enabled(), "decision": decision.summary(), "sample": _sample,
            "max_parallel": settings_manager.max_parallel, "reserve_percent": settings_manager.reserve_percent}
