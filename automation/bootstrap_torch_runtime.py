#!/usr/bin/env python3
"""Install the PyTorch runtime best suited to this host."""

from __future__ import annotations

import shutil
import subprocess
import sys
import json
from pathlib import Path
from engine.runtime_paths import runtime_root


TORCH_VERSION = "2.14.0"
VISION_VERSION = "0.29.0"
CUDA_TAG = "cu130"
CPU_TAG = "cpu"
STATUS_FILE = runtime_root() / "data" / "torch_runtime.json"


def probe_runtime() -> dict:
    """Inspect Torch in a fresh interpreter so a replaced wheel is never read from sys.modules."""
    probe = r"""
import json
try:
    import torch
    import torchvision
    available = bool(torch.cuda.is_available())
    print(json.dumps({
        "torch_version": torch.__version__,
        "vision_version": torchvision.__version__,
        "cuda_available": available,
        "gpu_name": torch.cuda.get_device_name(0) if available else None,
    }))
except Exception as exc:
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"error": result.stderr.strip() or f"probe exited with {result.returncode}"}


def nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    wsl_binary = Path("/usr/lib/wsl/lib/nvidia-smi")
    return str(wsl_binary) if wsl_binary.is_file() else None


def runtime_is_ready(tag: str) -> bool:
    runtime = probe_runtime()
    versions_match = (
        runtime.get("torch_version") == f"{TORCH_VERSION}+{tag}"
        and runtime.get("vision_version") == f"{VISION_VERSION}+{tag}"
    )
    return versions_match and (tag == CPU_TAG or runtime.get("cuda_available") is True)


def install(tag: str) -> None:
    index_url = f"https://download.pytorch.org/whl/{tag}"
    packages = [
        f"torch=={TORCH_VERSION}+{tag}",
        f"torchvision=={VISION_VERSION}+{tag}",
    ]
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--index-url", index_url, *packages],
        check=True,
    )


def write_status(device: str, cuda_attempted: bool, fallback_reason: str | None = None) -> None:
    runtime = probe_runtime()
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        json.dumps(
            {
                "device": device,
                "cuda_attempted": cuda_attempted,
                "cuda_runtime_available": device == "cuda",
                "gpu_name": runtime.get("gpu_name"),
                "torch_version": runtime.get("torch_version"),
                "fallback_reason": fallback_reason,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    tag = CUDA_TAG if nvidia_smi() else CPU_TAG
    label = "NVIDIA CUDA 13.0" if tag == CUDA_TAG else "CPU"
    if runtime_is_ready(tag):
        print(f"PyTorch {label} runtime is already ready.")
        write_status("cuda" if tag == CUDA_TAG else "cpu", tag == CUDA_TAG)
        return 0

    print(f"Installing PyTorch {label} runtime...")
    try:
        install(tag)
    except subprocess.CalledProcessError as exc:
        if tag == CPU_TAG:
            print(f"ERROR: PyTorch {label} installation failed: {exc}", file=sys.stderr)
            return 1
        print(f"WARNING: CUDA runtime installation failed: {exc}", file=sys.stderr)
    else:
        if runtime_is_ready(tag):
            print(f"PyTorch {label} runtime is ready.")
            write_status("cuda" if tag == CUDA_TAG else "cpu", tag == CUDA_TAG)
            return 0
        if tag == CPU_TAG:
            print(f"ERROR: PyTorch {label} runtime did not initialize.", file=sys.stderr)
            return 1
        print("WARNING: CUDA runtime did not initialize; falling back to CPU OCR.", file=sys.stderr)

    print("Installing PyTorch CPU fallback runtime...")
    try:
        install(CPU_TAG)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: PyTorch CPU fallback installation failed: {exc}", file=sys.stderr)
        return 1
    if not runtime_is_ready(CPU_TAG):
        print("ERROR: PyTorch CPU fallback runtime did not initialize.", file=sys.stderr)
        return 1
    print("PyTorch CPU fallback runtime is ready.")
    write_status(
        "cpu",
        tag == CUDA_TAG,
        "CUDA installation or initialization failed; OCR will use CPU." if tag == CUDA_TAG else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
