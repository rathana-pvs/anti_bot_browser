import subprocess
import os
import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.config import PROFILES_DIR, CPU_THREADS, ROOT_DIR
from backend.services.proxy_service import load_proxy_pool
from backend.services.docker_service import (
    count_running_profile_containers,
    get_current_cpu_percent,
    get_current_gpu_percent,
    get_gpu_model,
)
from backend.services.host_metrics_service import get_windows_host_stats

router = APIRouter(prefix="/api/system", tags=["system"])


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


def runtime_environment() -> str:
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    try:
        if "microsoft" in open("/proc/version", encoding="utf-8").read().lower():
            return "wsl"
    except Exception:
        pass
    return "linux"

@router.get("/stats")
def get_system_stats():
    docker_running = False
    try:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=2)
        docker_running = True
    except Exception:
        pass

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

    wsl_runtime = {
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
        # Legacy fields remain WSL values for compatibility with older clients.
        "cpu_percent": wsl_runtime["cpu_percent"],
        "cpu_cores": CPU_THREADS,
        "cpu_model": cpu_model_str,
        "gpu_percent": wsl_runtime["gpu_percent"],
        "gpu_model": wsl_runtime["gpu_model"],
        "windows_host": get_windows_host_stats(),
        "runtime_environment": runtime_environment(),
        "runtime": wsl_runtime,
        "wsl_runtime": wsl_runtime,
    }
