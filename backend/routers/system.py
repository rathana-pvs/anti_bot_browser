import subprocess
import os
import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.config import DATA_DIR, PROFILES_DIR, CPU_THREADS, ROOT_DIR
from backend.scheduler.background import scheduler
from backend.services import container_lifecycle
from backend.services.queue_service import load_posting_queue
from backend.services.reliability_service import build_reliability_scorecard, flatten_queue_executions
from backend.services.ocr_worker_service import shared_ocr_worker_snapshot
from backend.worker.service import status as worker_status
from backend.services.proxy_service import load_proxy_pool
from backend.services.docker_service import (
    count_running_profile_containers,
    get_current_cpu_percent,
    get_current_gpu_percent,
    get_gpu_model,
)

router = APIRouter(prefix="/api/system", tags=["system"])


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True, timeout=2,
        )
        return True
    except Exception:
        return False


def health_snapshot(docker_available: bool | None = None) -> dict:
    checks: dict[str, dict] = {}
    checks["lifecycle"] = {
        "ok": not container_lifecycle.SHUTTING_DOWN,
        "detail": "running" if not container_lifecycle.SHUTTING_DOWN else "shutting_down",
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        checks["data_directory"] = {"ok": os.access(DATA_DIR, os.W_OK), "detail": "writable"}
    except OSError as error:
        checks["data_directory"] = {"ok": False, "detail": type(error).__name__}
    try:
        queue = load_posting_queue()
        checks["queue"] = {
            "ok": isinstance(queue.get("daily_batches", []), list),
            "detail": f"{len(queue.get('daily_batches', []))} batches",
        }
    except Exception as error:
        checks["queue"] = {"ok": False, "detail": type(error).__name__}
    checks["scheduler"] = {"ok": bool(scheduler.running), "detail": "running" if scheduler.running else "stopped"}
    if docker_available is None:
        docker_available = _docker_available()
    checks["docker"] = {"ok": docker_available, "detail": "available" if docker_available else "unavailable"}
    checks["ocr"] = shared_ocr_worker_snapshot()
    checks["worker"] = worker_status()
    required = ("lifecycle", "data_directory", "queue", "scheduler")
    ready = all(checks[name].get("ok") is True for name in required)
    degraded = not checks["docker"]["ok"] or checks["ocr"].get("status") in ("error", "stopped")
    return {
        "status": "ready" if ready and not degraded else "degraded" if ready else "not_ready",
        "ready": ready,
        "checks": checks,
    }


@router.get("/health")
def get_health():
    snapshot = health_snapshot()
    return {"status": snapshot["status"], "build_version": read_build_version()}


@router.get("/ready")
def get_readiness():
    return health_snapshot()


@router.get("/reliability")
def get_reliability():
    queue = load_posting_queue()
    return build_reliability_scorecard(flatten_queue_executions(queue))


class UpdateMaintenance(BaseModel):
    enabled: bool


@router.post('/update-maintenance')
async def update_maintenance(request: UpdateMaintenance):
    from backend.services import container_lifecycle, automation_service
    if os.environ.get('WORKER_SERVICE_MODE') == '1':
        raise HTTPException(409, 'Stop the independently managed worker before updating')
    try:
        enabled = container_lifecycle.set_update_maintenance(request.enabled, automation_service.active_automation_tasks)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None
    return {'enabled': enabled}


def read_build_version() -> str:
    try:
        version = (ROOT_DIR / "VERSION").read_text(encoding="utf-8").strip()
        return version or "unknown"
    except Exception:
        return "unknown"


@router.get("/runtime-version")
def get_runtime_version():
    return {"version": read_build_version()}


@router.get("/stats")
def get_system_stats():
    docker_running = _docker_available()

    active_containers = count_running_profile_containers() if docker_running else 0

    total_profiles = 0
    if PROFILES_DIR.exists():
        for d in PROFILES_DIR.iterdir():
            if d.is_dir() and (d / "config.json").exists():
                total_profiles += 1

    vmem = psutil.virtual_memory()
    total_mem_mb = round(vmem.total / (1024 * 1024))
    free_mem_mb = round(vmem.available / (1024 * 1024))
    used_mem_mb = total_mem_mb - free_mem_mb

    pool = load_proxy_pool()
    available_proxies = len([p for p in pool if not p.get("assigned")])

    cpu_model_str = "x86_64 CPU"
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    cpu_model_str = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass

    runtime = {
        "cpu_percent": get_current_cpu_percent(),
        "cpu_threads": CPU_THREADS,
        "cpu_model": cpu_model_str,
        "used_memory_mb": used_mem_mb,
        "total_memory_mb": total_mem_mb,
        "gpu_percent": get_current_gpu_percent(),
        "gpu_model": get_gpu_model(),
    }

    return {
        "build_version": read_build_version(),
        "docker_running": docker_running,
        "active_profiles": active_containers,
        "total_profiles": total_profiles,
        "used_memory_mb": used_mem_mb,
        "total_memory_mb": total_mem_mb,
        "available_proxies": available_proxies,
        "cpu_percent": runtime["cpu_percent"],
        "cpu_cores": CPU_THREADS,
        "cpu_model": cpu_model_str,
        "gpu_percent": runtime["gpu_percent"],
        "gpu_model": runtime["gpu_model"],
        "runtime_environment": "linux",
        "runtime": runtime,
        "health": health_snapshot(docker_running),
    }
