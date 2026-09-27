import json
import math
from datetime import datetime, timezone
from pathlib import Path
from backend.config import (
    MANAGER_SETTINGS_FILE,
    TOTAL_MEMORY_GB,
    CPU_THREADS,
    OCR_RUNTIME,
)

RESOURCE_MODE_LIMITS = {
    "low": {
        "max_publishers": 1,
        "max_preparers": 1,
        "max_total_automation_tasks": 2,
        "max_active_profile_containers": 2,
    },
    "medium": {
        "max_publishers": 2,
        "max_preparers": 2,
        "max_total_automation_tasks": 4,
        "max_active_profile_containers": 4,
    },
    "high": {
        "max_publishers": 3,
        "max_preparers": 3,
        "max_total_automation_tasks": 6,
        "max_active_profile_containers": 6,
    },
}

MODE_RANK = {"low": 0, "medium": 1, "high": 2}
RANK_MODE = ["low", "medium", "high"]

def recommended_resource_mode(total_memory_gb: int, cpu_threads: int) -> str:
    memory_rank = 0 if total_memory_gb <= 24 else (1 if total_memory_gb <= 47 else 2)
    cpu_rank = 0 if cpu_threads < 12 else (1 if cpu_threads < 16 else 2)
    return RANK_MODE[min(memory_rank, cpu_rank)]

def recommended_ocr_threads(cpu_threads: int, max_active_tasks: int) -> int:
    threads = max(1, int(cpu_threads) if cpu_threads else 1)
    tasks = max(1, int(max_active_tasks) if max_active_tasks else 1)
    return max(2, min(4, math.floor(threads / tasks)))

def resolve_resource_mode(selected_mode: str, total_memory_gb: int, cpu_threads: int) -> dict:
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
        self.file_path = file_path
        self._settings = self._load()

    def _load(self) -> dict:
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"resource_mode": "auto", "updated_at": datetime.now(timezone.utc).isoformat()}

    def _save(self):
        tmp = f"{self.file_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2)
        Path(tmp).replace(self.file_path)

    @property
    def resource_mode(self) -> str:
        return self._settings.get("resource_mode", "auto")

    def update_resource_mode(self, mode: str) -> dict:
        mode = mode if mode in ("auto", "low", "medium", "high") else "auto"
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
