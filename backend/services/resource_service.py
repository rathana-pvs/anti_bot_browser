import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from backend.config import (
    MANAGER_SETTINGS_FILE,
    TOTAL_MEMORY_GB,
    CPU_THREADS,
    OCR_RUNTIME,
    OCR_ENGINE,
    OCR_ENGINE_OVERRIDE,
)

from automation.engine.ocr_reader import DEFAULT_ENGINE, ENGINES, ENGINE_LABELS, validate_engine

RESOURCE_MODE_LIMITS = {
    "low": {
        "max_publishers": 1,
        "max_preparers": 2,
        "max_total_automation_tasks": 2,
        "max_active_profile_containers": 2,
    },
    "medium": {
        "max_publishers": 2,
        "max_preparers": 3,
        "max_total_automation_tasks": 4,
        "max_active_profile_containers": 4,
    },
    "high": {
        "max_publishers": 3,
        "max_preparers": 5,
        "max_total_automation_tasks": 6,
        "max_active_profile_containers": 6,
    },
}

MODE_RANK = {"low": 0, "medium": 1, "high": 2}
RANK_MODE = ["low", "medium", "high"]

def recommended_resource_mode(total_memory_gb: float, cpu_threads: int) -> str:
    memory_rank = 0 if total_memory_gb < 24 else (1 if total_memory_gb <= 40 else 2)
    cpu_rank = 0 if cpu_threads < 12 else (1 if cpu_threads < 16 else 2)
    return RANK_MODE[min(memory_rank, cpu_rank)]

def recommended_ocr_threads(cpu_threads: int, max_active_tasks: int) -> int:
    threads = max(1, int(cpu_threads) if cpu_threads else 1)
    tasks = max(1, int(max_active_tasks) if max_active_tasks else 1)
    return max(2, min(4, math.floor(threads / tasks)))

def resolve_resource_mode(selected_mode: str, total_memory_gb: float, cpu_threads: int) -> dict:
    recommended = recommended_resource_mode(total_memory_gb, cpu_threads)
    selected = selected_mode if selected_mode in ("auto", "low", "medium", "high") else "auto"
    effective = recommended if selected == "auto" else selected
    return {
        "selected": selected,
        "effective": effective,
        "recommended": recommended,
        "supported": MODE_RANK[effective] <= MODE_RANK[recommended],
        "limits": dict(RESOURCE_MODE_LIMITS[effective]),
    }

def resource_admission_decision(
    memory_used_percent: float,
    cpu_percent: float,
    paused_for_memory: bool = False,
    milliseconds_since_last_start: float = float("inf"),
    start_spacing_ms: float = 10000,
) -> dict:
    memory_paused = memory_used_percent >= 70 if paused_for_memory else memory_used_percent >= 80
    if memory_paused:
        return {"allowed": False, "reason": "memory_pressure", "memoryPaused": True}
    if cpu_percent >= 85:
        return {"allowed": False, "reason": "cpu_pressure", "memoryPaused": False}
    if milliseconds_since_last_start < start_spacing_ms:
        return {"allowed": False, "reason": "container_start_spacing", "memoryPaused": False}
    return {"allowed": True, "reason": "resources_available", "memoryPaused": False}

class ResourceSettingsManager:
    def __init__(self, file_path: Path):
        self._lock = threading.RLock()
        self.file_path = file_path
        self._settings = self._load()

    def _load(self) -> dict:
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    value = json.load(f)
                    if isinstance(value, dict):
                        return value
            except Exception:
                pass
        return {"resource_mode": "auto", "updated_at": datetime.now(timezone.utc).isoformat()}

    def _save(self):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{self.file_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2)
        Path(tmp).replace(self.file_path)

    @property
    def resource_mode(self) -> str:
        return self._settings.get("resource_mode", "auto")

    @property
    def max_parallel(self) -> int | None:
        value = self._settings.get("max_parallel")
        return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 16 else None

    @property
    def reserve_percent(self) -> int:
        value = self._settings.get("reserve_percent", 10)
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 40 else 10

    def update_adaptive_limits(self, *, max_parallel=None, reserve_percent=None, clear_max=False) -> None:
        with self._lock:
            if clear_max:
                self._settings.pop("max_parallel", None)
            elif max_parallel is not None:
                if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or not 1 <= max_parallel <= 16:
                    raise ValueError("max_parallel must be between 1 and 16")
                self._settings["max_parallel"] = max_parallel
            if reserve_percent is not None:
                if isinstance(reserve_percent, bool) or not isinstance(reserve_percent, int) or not 0 <= reserve_percent <= 40:
                    raise ValueError("reserve_percent must be between 0 and 40")
                self._settings["reserve_percent"] = reserve_percent
            self._settings["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    @property
    def auto_warming(self) -> bool:
        return self._settings.get("auto_warming") is True

    def update_auto_warming(self, enabled) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        with self._lock:
            self._settings["auto_warming"] = enabled
            self._settings["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    @property
    def ocr_engine(self) -> str:
        value = self._settings.get("ocr_engine", DEFAULT_ENGINE)
        return value if value in ENGINES else DEFAULT_ENGINE

    def ocr_selection(self) -> dict:
        return {"selected": OCR_ENGINE if OCR_ENGINE_OVERRIDE else self.ocr_engine, "active": OCR_ENGINE,
                "locked": OCR_ENGINE_OVERRIDE,
                "restart_required": not OCR_ENGINE_OVERRIDE and self.ocr_engine != OCR_ENGINE,
                "options": [{"value": e, "label": ENGINE_LABELS[e]} for e in ENGINES]}

    def update_ocr_engine(self, engine: str) -> dict:
        validate_engine(engine)
        if OCR_ENGINE_OVERRIDE:
            raise ValueError('OCR engine is set by launch configuration; remove the override to use saved selection')
        with self._lock:
            self._settings["ocr_engine"] = engine
            self._settings["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
        return self.ocr_selection()

    def update_resource_mode(self, mode: str) -> dict:
        mode = mode if mode in ("auto", "low", "medium", "high") else "auto"
        with self._lock:
            self._settings["resource_mode"] = mode
            self._settings["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
        return self.get_snapshot()

    def get_snapshot(self) -> dict:
        res = resolve_resource_mode(self.resource_mode, TOTAL_MEMORY_GB, CPU_THREADS)
        ocr_threads = recommended_ocr_threads(CPU_THREADS, res["limits"]["max_total_automation_tasks"])
        return {
            "hardware": {
                "cpu_threads": CPU_THREADS,
                "total_memory_gb": TOTAL_MEMORY_GB,
            },
            "selected_mode": res["selected"],
            "effective_mode": res["effective"],
            "recommended_mode": res["recommended"],
            "supported": res["supported"],
            "limits": res["limits"],
            "ocr": {
                "threads": ocr_threads,
                "runtime": OCR_RUNTIME,
            },
            "updated_at": self._settings.get("updated_at"),
        }

settings_manager = ResourceSettingsManager(MANAGER_SETTINGS_FILE)
