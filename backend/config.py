import os
import shutil
import subprocess
import sys
import uuid
import psutil
from pathlib import Path

# Paths
BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
PROFILES_DIR = ROOT_DIR / "profiles"
SCRIPTS_DIR = ROOT_DIR / "scripts"
DATA_DIR = ROOT_DIR / "data"
SHARED_MEDIA_DIR = ROOT_DIR / "profiles" / "shared_media"
QUEUE_FILE = DATA_DIR / "posting_queue.json"
QUEUE_CLAIM_LOCK_FILE = DATA_DIR / "posting_queue.claim.lock"
MANAGER_SETTINGS_FILE = DATA_DIR / "manager_settings.json"
BRAINS_DIR = ROOT_DIR / "automation" / "brains"
BRAIN_CLI = ROOT_DIR / "automation" / "brain_cli.py"
if sys.platform == "win32":
    win_py_build = ROOT_DIR / "build" / "venv_win" / "Scripts" / "python.exe"
    win_py_auto = ROOT_DIR / "automation" / "venv" / "Scripts" / "python.exe"
    if win_py_build.exists():
        AUTOMATION_PYTHON = win_py_build
    elif win_py_auto.exists():
        AUTOMATION_PYTHON = win_py_auto
    else:
        AUTOMATION_PYTHON = Path(sys.executable)
else:
    linux_py_auto = ROOT_DIR / "automation" / "venv" / "bin" / "python"
    AUTOMATION_PYTHON = linux_py_auto if linux_py_auto.exists() else Path(sys.executable)
BRAIN_UPLOAD_DIR = BRAINS_DIR / "staging" / "uploads"
MANAGER_DIST_DIR = ROOT_DIR / "manager-app" / "dist"

# Ensure directories exist
for d in (DATA_DIR, SHARED_MEDIA_DIR, BRAIN_UPLOAD_DIR, PROFILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Instance identifier for queue scheduler leases
MANAGER_INSTANCE_ID = f"mgr_{uuid.uuid4().hex[:12]}"

# Hardware Specs
TOTAL_MEMORY_BYTES = psutil.virtual_memory().total
TOTAL_MEMORY_GB = round(TOTAL_MEMORY_BYTES / (1024 ** 3), 1)
CPU_THREADS = os.cpu_count() or 4

def get_host_timezone() -> str:
    try:
        tz_path = Path("/etc/timezone")
        if tz_path.exists():
            val = tz_path.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            target = os.readlink(str(localtime))
            if "zoneinfo/" in target:
                return target.split("zoneinfo/")[-1].strip()
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.check_output(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return "America/Guatemala" if Path("/etc/timezone").exists() else "UTC"

# OCR Runtime Detection
def detect_nvidia_gpu() -> tuple[bool, str | None]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        wsl_executable = Path("/usr/lib/wsl/lib/nvidia-smi")
        executable = str(wsl_executable) if wsl_executable.is_file() else None
    if not executable:
        return False, None
    try:
        output = subprocess.check_output(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        gpu_name = next((line.strip() for line in output.splitlines() if line.strip()), None)
        return True, gpu_name
    except Exception:
        return False, None


def detect_ocr_runtime():
    nvidia_detected, detected_gpu_name = detect_nvidia_gpu()
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0) or detected_gpu_name
            return {
                "device": "cuda",
                "label": "NVIDIA GPU",
                "nvidia_detected": True,
                "cuda_runtime_available": True,
                "gpu_name": gpu_name,
                "fallback_reason": None,
            }
    except Exception:
        pass
    return {
        "device": "cpu",
        "label": "CPU",
        "nvidia_detected": nvidia_detected,
        "cuda_runtime_available": False,
        "gpu_name": detected_gpu_name,
        "fallback_reason": (
            "NVIDIA GPU detected, but the installed CUDA runtime is unavailable."
            if nvidia_detected
            else "No compatible NVIDIA GPU detected."
        ),
    }

OCR_RUNTIME = detect_ocr_runtime()
