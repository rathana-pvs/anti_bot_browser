import json
import shutil
import subprocess
from threading import Lock


_WINDOWS_STATS_LOCK = Lock()
_windows_host_stats: dict = {"available": False}

_WINDOWS_STATS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$cpu = [math]::Round(((Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples |
    Measure-Object -Property CookedValue -Average).Average, 1)
Add-Type -AssemblyName Microsoft.VisualBasic
$computer = [Microsoft.VisualBasic.Devices.ComputerInfo]::new()
$totalMemory = [double]$computer.TotalPhysicalMemory
$availableMemory = [double]$computer.AvailablePhysicalMemory
$gpuSamples = (Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples
$gpu = if ($gpuSamples) {
    [math]::Round(($gpuSamples | Measure-Object -Property CookedValue -Maximum).Maximum, 1)
} else { $null }
$cpuModel = (Get-ItemProperty 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0').ProcessorNameString
$gpuModel = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Video\*\0000' -ErrorAction SilentlyContinue |
    Where-Object { $_.DriverDesc } |
    Select-Object -ExpandProperty DriverDesc -Unique |
    Select-Object -First 1
[pscustomobject]@{
    available = $true
    cpu_percent = $cpu
    cpu_threads = [Environment]::ProcessorCount
    cpu_model = $cpuModel
    used_memory_mb = [math]::Round(($totalMemory - $availableMemory) / 1MB)
    total_memory_mb = [math]::Round($totalMemory / 1MB)
    gpu_percent = $gpu
    gpu_model = $gpuModel
} | ConvertTo-Json -Compress
"""


def sample_windows_host():
    """Sample the real Windows host when the manager is running under WSL."""
    global _windows_host_stats
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return

    try:
        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _WINDOWS_STATS_SCRIPT],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        payload = json.loads(completed.stdout.strip())
        if payload.get("available"):
            with _WINDOWS_STATS_LOCK:
                _windows_host_stats = payload
    except Exception:
        # Keep the last successful sample through transient counter failures.
        pass


def get_windows_host_stats() -> dict:
    with _WINDOWS_STATS_LOCK:
        return dict(_windows_host_stats)
