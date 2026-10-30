"""Linux host measurements for adaptive resource control.

Reads pressure stall information (PSI), memory, swap and the samples the
background jobs already keep (sustained CPU, GPU, per-container stats). PSI is
the share of time work waited for CPU, memory or IO; it is missing on kernels
older than 4.20, in which case the controller uses percentages only.
"""

import re
from pathlib import Path

import psutil

PSI_ROOT = Path("/proc/pressure")
_SIZE = re.compile(r"^\s*([\d.]+)\s*([kKMGT]?i?B)\s*$")
_UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "KiB": 1024, "MB": 1e6, "MiB": 1024 ** 2,
          "GB": 1e9, "GiB": 1024 ** 3, "TB": 1e12, "TiB": 1024 ** 4}


def read_psi(resource: str, root: Path = PSI_ROOT) -> dict | None:
    """``{"some": avg10, "full": avg10}`` for cpu, memory or io, or None."""
    try:
        text = (root / resource).read_text(encoding="utf-8")
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        match = re.search(r"avg10=([\d.]+)", rest)
        if kind in ("some", "full") and match:
            values[kind] = float(match.group(1))
    return values or None


def parse_size_mb(text: str) -> float | None:
    """Docker's "1.2GiB" style sizes, in MiB."""
    match = _SIZE.match(text or "")
    if not match or match.group(2) not in _UNITS:
        return None
    return float(match.group(1)) * _UNITS[match.group(2)] / 1024 ** 2


def container_usage(stats: dict) -> dict:
    """Per-container CPU % and memory MiB from the docker stats cache."""
    usage = {}
    for name, value in stats.items():
        if not name.startswith("isolated_"):
            continue
        try:
            cpu = float(str(value.get("cpu", "0")).rstrip("%") or 0)
        except ValueError:
            cpu = 0.0
        memory = parse_size_mb(str(value.get("mem", "")).split("/")[0])
        usage[name] = {"cpu_percent": cpu, "memory_mb": memory}
    return usage


def sample(*, cpu_percent: float, gpu_percent: float | None, gpu_memory_percent: float | None,
           container_stats: dict, psi_root: Path = PSI_ROOT) -> dict:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    psi = {name: read_psi(name, psi_root) for name in ("cpu", "memory", "io")}
    return {
        "cpu_percent": round(float(cpu_percent), 1),
        "memory_percent": round(float(memory.percent), 1),
        "memory_total_mb": round(memory.total / 1024 ** 2),
        "memory_available_mb": round(memory.available / 1024 ** 2),
        "swap_percent": round(float(swap.percent), 1),
        "psi": psi if any(psi.values()) else None,
        "gpu_percent": gpu_percent,
        "gpu_memory_percent": gpu_memory_percent,
        "containers": container_usage(container_stats),
    }
