import base64
import json
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
from backend.services.profile_service import atomic_write_json, environment_differences, utc_now

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

def get_current_cpu_percent() -> float:
    global current_cpu_percent
    if current_cpu_percent == 0.0:
        val = psutil.cpu_percent(interval=None)
        if val > 0.0:
            current_cpu_percent = val
    return current_cpu_percent

def get_current_gpu_percent() -> float:
    return current_gpu_percent

def get_gpu_model() -> str:
    return gpu_model

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
            timeout=10,
        ).strip()
        stats_map = {}
        if out:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    name = parts[0].strip()
                    cpu_str = parts[1].strip() or "0.0%"
                    mem_str = parts[2].strip() or "0B"
                    if cpu_str.endswith("%"):
                        try:
                            raw = float(cpu_str.replace("%", "").strip())
                            cpu_str = f"{raw:.1f}%"
                        except ValueError:
                            pass
                    stats_map[name] = {"cpu": cpu_str, "mem": mem_str}
        container_stats_cache.clear()
        container_stats_cache.update(stats_map)
    except Exception:
        pass

def get_container_stats(container_name: str) -> dict:
    return container_stats_cache.get(container_name, {})

def get_all_container_stats() -> dict:
    return dict(container_stats_cache)

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


def sanitize_profile_action_error(profile_id: str, message: str) -> str:
    """Keep actionable launch diagnostics while removing stored proxy secrets."""
    sanitized = re.sub(
        r"(?i)(socks5h?://)[^@\s]+@",
        r"\1[REDACTED]@",
        str(message or ""),
    )
    config_path = PROFILES_DIR / profile_id / "config.json"
    try:
        profile = json.loads(config_path.read_text(encoding="utf-8"))
        network = profile.get("network") or {}
        for key in ("proxy_user", "proxy_pass"):
            secret = str(network.get(key) or "")
            if secret:
                sanitized = sanitized.replace(secret, "[REDACTED]")
    except Exception:
        pass
    sanitized = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", sanitized)
    sanitized = "\n".join(line.rstrip() for line in sanitized.splitlines()).strip()
    return sanitized[-12000:] or "The profile action failed without diagnostic output."

async def run_profile_action(profile_id: str, action: str) -> dict:
    if action not in ("start", "stop", "pause", "unpause"):
        raise ValueError("Invalid action")
    original_status = get_container_status(profile_id)
    script_path = SCRIPTS_DIR / "run_profile.sh"
    cmd = ["bash", str(script_path), profile_id, action]
    proc = await asyncio_subprocess_run(cmd, cwd=str(ROOT_DIR))
    if proc.returncode != 0:
        raw_error = proc.stderr.strip() or f"Failed to run {action} on {profile_id}"
        raise RuntimeError(sanitize_profile_action_error(profile_id, raw_error))
    if action == "start" and original_status != "running":
        record_effective_profile_environment(profile_id)
    return {"success": True, "message": proc.stdout.strip()}


def _container_text(container_name: str, command: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "exec", container_name, "sh", "-lc", command],
            capture_output=True, text=True, timeout=5, check=False,
        )
        value = result.stdout.strip()
        return value or None
    except Exception:
        return None


def _parse_json_object(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _renderer_is_software(renderer: str | None) -> bool:
    return bool(renderer and re.search(
        r"llvmpipe|softpipe|swiftshader|software rasterizer",
        renderer,
        flags=re.IGNORECASE,
    ))


def _browser_observation_values(browser: dict | None) -> dict:
    """Normalize values measured by the localhost page running in Chrome."""
    browser = browser or {}
    screen = browser.get("screen") if isinstance(browser.get("screen"), dict) else {}
    webgl = browser.get("webgl") if isinstance(browser.get("webgl"), dict) else {}
    renderer = webgl.get("unmasked_renderer") or webgl.get("renderer")
    vendor = webgl.get("unmasked_vendor") or webgl.get("vendor")
    screen_resolution = None
    if screen.get("width") and screen.get("height"):
        screen_resolution = f"{screen['width']}x{screen['height']}"
    return {
        "screen_resolution": screen_resolution,
        "timezone": browser.get("timezone"),
        "language": browser.get("language"),
        "languages": browser.get("languages"),
        "user_agent": browser.get("user_agent"),
        "platform": browser.get("platform"),
        "hardware_concurrency": browser.get("hardware_concurrency"),
        "device_memory": browser.get("device_memory"),
        "webdriver": browser.get("webdriver"),
        "webgl_vendor": vendor,
        "webgl_renderer": renderer,
        "screen": screen or None,
        "user_agent_data": browser.get("user_agent_data"),
    }


def observe_container_environment(profile_id: str) -> dict:
    """Collect container values and the browser's localhost self-observation."""
    container_name = f"isolated_{profile_id}"
    geometry = None
    for _ in range(20):
        geometry = _container_text(
            container_name,
            "xdotool getdisplaygeometry 2>/dev/null | tr ' ' 'x'",
        )
        if geometry:
            break
        time.sleep(0.5)
    timezone_name = _container_text(
        container_name,
        "cat /etc/timezone 2>/dev/null || readlink /etc/localtime | sed 's#^.*/zoneinfo/##'",
    )
    system_locale = _container_text(container_name, "printf '%s' \"${LANG:-}\"")
    browser_version = _container_text(container_name, "google-chrome --version 2>/dev/null")
    rendering_status = _parse_json_object(_container_text(
        container_name,
        "cat /run/rendering-status.json 2>/dev/null",
    ))
    browser_probe = None
    for _ in range(20):
        browser_probe = _parse_json_object(_container_text(
            container_name,
            "cat /run/browser-observation.json 2>/dev/null",
        ))
        if browser_probe:
            break
        time.sleep(0.25)
    browser_values = _browser_observation_values(browser_probe)
    display_renderer = (
        rendering_status.get("renderer") if rendering_status else None
    ) or _container_text(
        container_name,
        "glxinfo -B 2>/dev/null | sed -n 's/^OpenGL renderer string: //p' | head -1",
    )
    browser_renderer = browser_values.get("webgl_renderer")
    if rendering_status is not None:
        rendering_status = {
            **rendering_status,
            "display_renderer": display_renderer,
            "browser_webgl_vendor": browser_values.get("webgl_vendor"),
            "browser_webgl_renderer": browser_renderer,
            "accelerated": (
                not _renderer_is_software(browser_renderer)
                if browser_renderer
                else bool(rendering_status.get("accelerated"))
            ),
        }
        if browser_renderer and _renderer_is_software(browser_renderer):
            rendering_status["effective_mode"] = "software"
            rendering_status["fallback_reason"] = "Chrome WebGL reports software rendering"
        elif browser_renderer and rendering_status.get("requested_mode") == "host_gpu":
            rendering_status["effective_mode"] = "host_gpu"
            rendering_status["fallback_reason"] = ""
    return {
        "source": "browser_probe" if browser_probe else "container_runtime",
        "observed_at": utc_now(),
        "screen_resolution": browser_values.get("screen_resolution") or geometry,
        "timezone": browser_values.get("timezone") or timezone_name,
        "language": browser_values.get("language"),
        "languages": browser_values.get("languages"),
        "system_locale": system_locale,
        "browser_version": browser_version,
        "webgl_vendor": browser_values.get("webgl_vendor"),
        "webgl_renderer": browser_renderer or display_renderer,
        "rendering": rendering_status,
        "user_agent": browser_values.get("user_agent"),
        "platform": browser_values.get("platform"),
        "hardware_concurrency": browser_values.get("hardware_concurrency"),
        "device_memory": browser_values.get("device_memory"),
        "webdriver": browser_values.get("webdriver"),
        "screen": browser_values.get("screen"),
        "user_agent_data": browser_values.get("user_agent_data"),
        "note": (
            "Browser-visible values were measured by a localhost page without CDP."
            if browser_probe else
            "The browser probe did not complete; values are limited to the container runtime."
        ),
    }


def record_effective_profile_environment(profile_id: str) -> None:
    config_path = PROFILES_DIR / profile_id / "config.json"
    try:
        profile = json.loads(config_path.read_text(encoding="utf-8"))
        requested = profile.get("requested_environment") or {}
        network = profile.get("network") or {}
        container_id = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.Id}}", f"isolated_{profile_id}"],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip()
        limits_raw = subprocess.check_output(
            [
                "docker", "inspect", "-f",
                "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}",
                f"isolated_{profile_id}",
            ],
            text=True, stderr=subprocess.DEVNULL, timeout=3,
        ).strip().split()
        nano_cpus = int(limits_raw[0]) if limits_raw else 0
        memory_bytes = int(limits_raw[1]) if len(limits_raw) > 1 else 0
        profile["effective_environment"] = {
            **requested,
            "network_mode": network.get("mode", "direct"),
            "proxy_endpoint": (
                f"{network.get('proxy_host')}:{network.get('proxy_port')}"
                if network.get("proxy_host") else None
            ),
            "configuration_revision": profile.get("configuration_revision"),
            "container_id": container_id,
            "applied_at": utc_now(),
            "network_preflight": _container_text(
                f"isolated_{profile_id}",
                "cat /run/network-preflight.status 2>/dev/null",
            ),
        }
        profile["effective_resources"] = {
            "cpu_limit": nano_cpus / 1_000_000_000 if nano_cpus else None,
            "memory_mb": round(memory_bytes / (1024 * 1024)) if memory_bytes else None,
            "configuration_revision": profile.get("configuration_revision"),
            "applied_at": utc_now(),
        }
        observed = observe_container_environment(profile_id)
        observed["comparison"] = environment_differences(requested, observed)
        profile["observed_environment"] = observed
        profile["restart_required"] = False
        profile.setdefault("container", {})["id"] = container_id
        atomic_write_json(config_path, profile)
    except Exception as exc:
        print(f"Could not record effective environment for {profile_id}: {exc}")

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
        "docker", "exec", "-i", "-u", "chromeuser",
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
