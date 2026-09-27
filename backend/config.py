import os
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
AUTOMATION_PYTHON = ROOT_DIR / "automation" / "venv" / "bin" / "python"
BRAIN_UPLOAD_DIR = BRAINS_DIR / "staging" / "uploads"
MANAGER_DIST_DIR = ROOT_DIR / "manager-app" / "dist"

# Ensure directories exist
for d in (DATA_DIR, SHARED_MEDIA_DIR, BRAIN_UPLOAD_DIR, PROFILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Instance identifier for queue scheduler leases
MANAGER_INSTANCE_ID = f"mgr_{uuid.uuid4().hex[:12]}"

# Hardware Specs
TOTAL_MEMORY_BYTES = psutil.virtual_memory().total
TOTAL_MEMORY_GB = round(TOTAL_MEMORY_BYTES / (1024 ** 3))
CPU_THREADS = os.cpu_count() or 4

# Real host hardware specifications (TigerLake Iris Xe / i7-11370H / 16GB)
REAL_HOST_SPECS = {
    "webgl_vendor": "Intel Open Source Technology Center",
    "webgl_renderer": "Mesa Intel(R) Iris(R) Xe Graphics (TGL GT2)",
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "color_depth": 24,
    "language": "en-US",
    "hardware_concurrency": 8,
    "device_memory": 16,
}

# OCR Runtime Detection
def detect_ocr_runtime():
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
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
        "nvidia_detected": False,
        "cuda_runtime_available": False,
        "gpu_name": None,
        "fallback_reason": "No compatible NVIDIA GPU detected.",
    }

OCR_RUNTIME = detect_ocr_runtime()
