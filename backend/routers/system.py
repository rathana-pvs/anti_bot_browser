import subprocess
import psutil
from fastapi import APIRouter
from backend.config import PROFILES_DIR, CPU_THREADS
from backend.services.proxy_service import load_proxy_pool
from backend.services.docker_service import (
    count_running_profile_containers,
    current_cpu_percent,
    current_gpu_percent,
    gpu_model,
)

router = APIRouter(prefix="/api/system", tags=["system"])

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

    return {
        "docker_running": docker_running,
        "active_profiles": active_containers,
        "total_profiles": total_profiles,
        "used_memory_mb": used_mem_mb,
        "total_memory_mb": total_mem_mb,
        "available_proxies": available_proxies,
        "cpu_percent": current_cpu_percent,
        "cpu_cores": CPU_THREADS,
        "cpu_model": cpu_model_str,
        "gpu_percent": current_gpu_percent,
        "gpu_model": gpu_model,
    }
