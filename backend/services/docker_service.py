import base64
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import psutil
from backend.config import (
    ROOT_DIR,
    PROFILES_DIR,
    SCRIPTS_DIR,
    OCR_RUNTIME,
    CPU_THREADS,
    TOTAL_MEMORY_GB,
)
from backend.services.resource_service import (
    settings_manager,
    recommended_ocr_threads,
    resolve_resource_mode,
    resource_admission_decision,
)

container_stats_cache: dict[str, dict] = {}
profile_disk_usage_cache: dict[str, str] = {}

recent_cpu_samples: list[float] = []
current_cpu_percent: float = 0.0
current_gpu_percent: float = 0.0
gpu_model: str = "Integrated / Software"

# Detect GPU model on init
try:
    lspci = subprocess.check_output(
        "lspci | grep -iE 'vga|3d|display'",
        shell=True,
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=2,
    ).strip()
    if lspci:
        raw = lspci.split(":")[-1].strip()
        raw = re.sub(r"Corporation\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"[\[\]]", "", raw)
        raw = re.sub(r"\(rev \d+\)", "", raw, flags=re.IGNORECASE).strip()
        if raw:
            gpu_model = raw
except Exception:
    pass

_prev_gpu_rc6 = None
_prev_gpu_time = None

def sample_cpu():
    global current_cpu_percent, recent_cpu_samples
    try:
        current_cpu_percent = psutil.cpu_percent(interval=None)
        recent_cpu_samples.append(current_cpu_percent)
        if len(recent_cpu_samples) > 15:
            recent_cpu_samples.pop(0)
    except Exception:
        pass

def sustained_cpu_percent() -> float:
    if not recent_cpu_samples:
        return current_cpu_percent
    return sum(recent_cpu_samples) / len(recent_cpu_samples)

def sample_gpu():
    global current_gpu_percent, gpu_model, _prev_gpu_rc6, _prev_gpu_time
    # 1. Check NVIDIA
    try:
        nv = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=0.3,
        ).strip()
        if nv:
            parts = [s.strip() for s in nv.split(",")]
            gpu_model = parts[0] or "NVIDIA GPU"
            current_gpu_percent = float(parts[1]) if len(parts) > 1 else 0.0
            return
    except Exception:
        pass

    # 2. Check Intel DRM sysfs RC6 residency
    try:
        rc6_paths = [
            Path("/sys/class/drm/card1/gt/gt0/rc6_residency_ms"),
            Path("/sys/class/drm/card0/gt/gt0/rc6_residency_ms"),
        ]
        rc6_path = next((p for p in rc6_paths if p.exists()), None)
        if rc6_path:
            now = time.time() * 1000
            rc6 = int(rc6_path.read_text(encoding="utf-8").strip())
            if _prev_gpu_time and _prev_gpu_rc6 is not None:
                elapsed = now - _prev_gpu_time
                rc6_diff = rc6 - _prev_gpu_rc6
                active = max(0, elapsed - rc6_diff)
                if elapsed > 0:
                    current_gpu_percent = min(100.0, max(0.0, round((active / elapsed) * 100)))
            _prev_gpu_rc6 = rc6
            _prev_gpu_time = now
    except Exception:
        pass

def sample_container_stats():
    global container_stats_cache
    try:
        out = subprocess.check_output(
            ["docker", "stats", "--no-stream", '--format={{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        if out:
            stats_map = {}
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    name = parts[0].strip()
                    cpu_str = parts[1].strip() or "0%"
                    mem_str = parts[2].strip() or "0B"
                    # Profile containers have 4 cores allocated. Normalize raw docker stats to 0-100%
                    if name.startswith("isolated_") and cpu_str.endswith("%"):
                        try:
                            raw = float(cpu_str.replace("%", ""))
                            normalized = min(100.0, round((raw / 4) * 10) / 10)
                            cpu_str = f"{normalized}%"
                        except ValueError:
                            pass
                    stats_map[name] = {"cpu": cpu_str, "mem": mem_str}
            container_stats_cache = stats_map
    except Exception:
        pass

def sample_profile_disk_usage():
    global profile_disk_usage_cache
    try:
        if not PROFILES_DIR.exists():
            return
        for entry in PROFILES_DIR.iterdir():
            if entry.is_dir() and (entry / "config.json").exists():
                profile_id = entry.name
                usage = get_profile_disk_usage(profile_id)
                if usage:
                    profile_disk_usage_cache[profile_id] = usage
    except Exception:
        pass

def get_profile_disk_usage(profile_id: str) -> str | None:
    if profile_id in profile_disk_usage_cache:
        return profile_disk_usage_cache[profile_id]
    profile_path = PROFILES_DIR / profile_id
    if not profile_path.exists():
        return None
    try:
        out = subprocess.check_output(
            ["du", "-sk", str(profile_path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        match = re.match(r"^(\d+)", out)
        if match:
            kb = int(match.group(1))
            if kb >= 1024 * 1024:
                formatted = f"{kb / (1024 * 1024):.1f} GiB"
            elif kb >= 1024:
                formatted = f"{kb / 1024:.1f} MiB"
            else:
                formatted = f"{kb} KiB"
            profile_disk_usage_cache[profile_id] = formatted
            return formatted
    except Exception:
        pass
    return None

def get_container_status(profile_id: str) -> str:
    container_name = f"isolated_{profile_id}"
    try:
        output = subprocess.check_output(
            ["docker", "ps", "-a", "--filter", f"name=^/{container_name}$", '--format={{.Status}}'],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        if not output:
            return "stopped"
        if "Paused" in output:
            return "paused"
        if "Up" in output:
            return "running"
        return "stopped"
    except Exception:
        return "stopped"

def count_running_profile_containers() -> int:
    try:
        output = subprocess.check_output(
            ["docker", "ps", "--filter", "name=^/isolated_", '--format={{.Names}}'],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        return len([line for line in output.splitlines() if line.strip()]) if output else 0
    except Exception:
        return 0

memory_admission_paused = False
last_queue_container_start_at = 0.0
CONTAINER_START_SPACING_MS = 10000

def get_scheduler_config() -> dict:
    mode_snapshot = settings_manager.get_snapshot()
    limits = mode_snapshot["limits"]
    return {
        "max_publishers": limits["max_publishers"],
        "max_preparers": limits["max_preparers"],
        "max_total_automation_tasks": limits["max_total_automation_tasks"],
        "max_active_profile_containers": limits["max_active_profile_containers"],
        "lease_ttl_ms": 60000,
        "heartbeat_interval_ms": 15000,
    }

def resource_mode_snapshot() -> dict:
    global memory_admission_paused
    vmem = psutil.virtual_memory()
    total_mem = vmem.total
    used_mem = vmem.used
    mem_used_pct = (used_mem / total_mem) * 100 if total_mem > 0 else 0.0
    cpu_pct = sustained_cpu_percent()

    admission = resource_admission_decision(
        memory_used_percent=mem_used_pct,
        cpu_percent=cpu_pct,
        paused_for_memory=memory_admission_paused,
        milliseconds_since_last_start=(time.time() * 1000) - last_queue_container_start_at,
        start_spacing_ms=CONTAINER_START_SPACING_MS,
    )
    memory_admission_paused = admission.get("memoryPaused", False)

    scheduler_cfg = get_scheduler_config()
    cpu_model_str = psutil.cpu_freq() # fallback string if model not easily in psutil
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    cpu_model_str = line.split(":", 1)[1].strip()
                    break
    except Exception:
        cpu_model_str = "x86_64 CPU"

    return {
        "selected_mode": settings_manager.resource_mode,
        "effective_mode": resolve_resource_mode(settings_manager.resource_mode, TOTAL_MEMORY_GB, CPU_THREADS)["effective"],
        "recommended_mode": resolve_resource_mode(settings_manager.resource_mode, TOTAL_MEMORY_GB, CPU_THREADS)["recommended"],
        "limits": {
            **scheduler_cfg,
            "ocr_threads_per_worker": recommended_ocr_threads(CPU_THREADS, scheduler_cfg["max_total_automation_tasks"]),
        },
        "hardware": {
            "total_memory_gb": round(TOTAL_MEMORY_GB, 1),
            "cpu_threads": CPU_THREADS,
            "cpu_model": cpu_model_str,
            "ocr": OCR_RUNTIME,
        },
        "supported_modes": {
            "low": resolve_resource_mode("low", TOTAL_MEMORY_GB, CPU_THREADS)["supported"],
            "medium": resolve_resource_mode("medium", TOTAL_MEMORY_GB, CPU_THREADS)["supported"],
            "high": resolve_resource_mode("high", TOTAL_MEMORY_GB, CPU_THREADS)["supported"],
        },
        "runtime": {
            "memory_used_percent": round(mem_used_pct, 1),
            "sustained_cpu_percent": round(cpu_pct, 1),
            "active_profile_containers": count_running_profile_containers(),
            "admission_allowed": admission["allowed"],
            "admission_reason": admission["reason"],
            "memory_paused": memory_admission_paused,
        },
    }

def claim_container_start_admission() -> dict:
    global last_queue_container_start_at
    snapshot = resource_mode_snapshot()
    if snapshot["runtime"]["admission_allowed"]:
        last_queue_container_start_at = time.time() * 1000
        return {"allowed": True, "reason": "admission_granted"}
    return {"allowed": False, "reason": snapshot["runtime"]["admission_reason"]}

def automation_worker_env() -> dict:
    scheduler_cfg = get_scheduler_config()
    threads = recommended_ocr_threads(CPU_THREADS, scheduler_cfg["max_total_automation_tasks"])
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["AUTOMATION_OCR_THREADS"] = str(threads)
    env["AUTOMATION_OCR_DEVICE"] = OCR_RUNTIME["device"]
    return env

async def run_profile_action(profile_id: str, action: str) -> dict:
    if action not in ("start", "stop", "pause", "unpause"):
        raise ValueError("Invalid action")
    script_path = SCRIPTS_DIR / "run_profile.sh"
    cmd = ["bash", str(script_path), profile_id, action]
    proc = await asyncio_subprocess_run(cmd, cwd=str(ROOT_DIR))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Failed to run {action} on {profile_id}")
    return {"success": True, "message": proc.stdout.strip()}

async def paste_text_to_container(profile_id: str, text: str, mode: str = "both") -> dict:
    container_name = f"isolated_{profile_id}"
    status = get_container_status(profile_id)
    if status != "running":
        raise RuntimeError(f"Container {container_name} is not running")

    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    py_script = f"""
import base64, subprocess, sys

raw_b64 = "{b64}"
text = base64.b64decode(raw_b64).decode("utf-8")
mode = "{mode}"

try:
    p1 = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
    p1.communicate(text.encode("utf-8"))
    p2 = subprocess.Popen(["xclip", "-selection", "primary"], stdin=subprocess.PIPE)
    p2.communicate(text.encode("utf-8"))
except Exception:
    pass

if mode == "type":
    try:
        win_res = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "google-chrome"], stdout=subprocess.PIPE, text=True)
        win_ids = [w.strip() for w in win_res.stdout.split() if w.strip()]
        if win_ids:
            subprocess.run(["xdotool", "windowfocus", win_ids[-1]], check=False)
            subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "8", text], check=False)
    except Exception:
        pass
elif mode in ("paste", "both"):
    try:
        win_res = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "google-chrome"], stdout=subprocess.PIPE, text=True)
        win_ids = [w.strip() for w in win_res.stdout.split() if w.strip()]
        if win_ids:
            subprocess.run(["xdotool", "windowfocus", win_ids[-1]], check=False)
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=False)
    except Exception:
        pass
"""
    cmd = [
        "docker", "exec", "-i", "-u", "chromeuser", "-e", "DISPLAY=:99",
        container_name, "python3", "-c", py_script
    ]
    proc = await asyncio_subprocess_run(cmd, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to paste to container")
    return {"success": True, "message": f"Text transferred to {profile_id}"}

def remove_container(profile_id: str):
    container_name = f"isolated_{profile_id}"
    try:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
    except Exception:
        pass

class SubprocessResult:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

async def asyncio_subprocess_run(cmd: list[str], cwd: str | None = None, timeout: float = 30.0) -> SubprocessResult:
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return SubprocessResult(
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise TimeoutError(f"Command {' '.join(cmd)} timed out after {timeout}s")
