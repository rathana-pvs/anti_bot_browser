#!/usr/bin/env python3
"""Install the PyTorch runtime best suited to this host."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


TORCH_VERSION = "2.14.0"
VISION_VERSION = "0.29.0"
CUDA_TAG = "cu130"
CPU_TAG = "cpu"


def nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    wsl_binary = Path("/usr/lib/wsl/lib/nvidia-smi")
    return str(wsl_binary) if wsl_binary.is_file() else None


def runtime_is_ready(tag: str) -> bool:
    try:
        import torch
        import torchvision

        versions_match = (
            torch.__version__ == f"{TORCH_VERSION}+{tag}"
            and torchvision.__version__ == f"{VISION_VERSION}+{tag}"
        )
        if tag == CPU_TAG:
            return versions_match
        return versions_match and torch.cuda.is_available()
    except (ImportError, RuntimeError):
        return False


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


def main() -> int:
    tag = CUDA_TAG if nvidia_smi() else CPU_TAG
    label = "NVIDIA CUDA 13.0" if tag == CUDA_TAG else "CPU"
    if runtime_is_ready(tag):
        print(f"PyTorch {label} runtime is already ready.")
        return 0

    print(f"Installing PyTorch {label} runtime...")
    install(tag)
    if not runtime_is_ready(tag):
        print(f"ERROR: PyTorch {label} runtime did not initialize.", file=sys.stderr)
        return 1
    print(f"PyTorch {label} runtime is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
