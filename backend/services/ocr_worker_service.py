"""Lifecycle manager for the shared OCR subprocess."""

import atexit
import json
import os
import secrets
import socket
import subprocess
from urllib import error, request
from pathlib import Path

import psutil

from backend.config import AUTOMATION_PYTHON, CPU_THREADS, DATA_DIR, OCR_RUNTIME, ROOT_DIR
from backend.services.resource_service import recommended_ocr_threads, settings_manager


_process: subprocess.Popen | None = None
_log_handle = None


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_legacy_workers(worker_script: Path) -> int:
    """Remove workers from older releases that had no owner watchdog."""
    legacy = []
    expected_script = str(worker_script.resolve())
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = process.info.get("cmdline") or []
            if expected_script not in command or "--parent-pid" in command:
                continue
            process.terminate()
            legacy.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    _, survivors = psutil.wait_procs(legacy, timeout=2)
    for process in survivors:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            pass
    return len(legacy)


def start_shared_ocr_worker() -> dict:
    global _process, _log_handle
    if _process is not None and _process.poll() is None:
        return {"started": True, "pid": _process.pid, "url": os.environ.get("AUTOMATION_OCR_WORKER_URL")}

    port = _available_loopback_port()
    token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}"
    limits = settings_manager.get_snapshot()["limits"]
    threads = recommended_ocr_threads(CPU_THREADS, limits["max_total_automation_tasks"])
    worker_script = ROOT_DIR / "automation" / "ocr_worker.py"
    stopped_legacy_workers = _stop_legacy_workers(worker_script)
    if stopped_legacy_workers:
        print(f"Stopped {stopped_legacy_workers} legacy OCR worker(s).")
    log_path = DATA_DIR / "ocr_worker.log"
    try:
        _log_handle = open(log_path, "a", encoding="utf-8")
        env = os.environ.copy()
        env["AUTOMAT_FB_ROOT"] = str(ROOT_DIR)
        env["PYTHONUNBUFFERED"] = "1"
        env["AUTOMATION_OCR_DEVICE"] = OCR_RUNTIME["device"]
        env["AUTOMATION_OCR_THREADS"] = str(threads)
        env["AUTOMATION_OCR_WORKER_TOKEN"] = token
        _process = subprocess.Popen(
            [
                str(AUTOMATION_PYTHON),
                "-u",
                str(worker_script),
                "--port",
                str(port),
                "--parent-pid",
                str(os.getpid()),
                "--device",
                OCR_RUNTIME["device"],
                "--threads",
                str(threads),
            ],
            cwd=str(ROOT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=_log_handle,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        if _log_handle is not None:
            _log_handle.close()
            _log_handle = None
        _process = None
        return {"started": False, "pid": None, "url": None, "error": str(exc)}
    os.environ["AUTOMATION_OCR_WORKER_URL"] = url
    os.environ["AUTOMATION_OCR_WORKER_TOKEN"] = token
    return {"started": True, "pid": _process.pid, "url": url}


def stop_shared_ocr_worker() -> None:
    global _process, _log_handle
    process = _process
    _process = None
    os.environ.pop("AUTOMATION_OCR_WORKER_URL", None)
    os.environ.pop("AUTOMATION_OCR_WORKER_TOKEN", None)
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if _log_handle is not None:
        _log_handle.close()
        _log_handle = None


atexit.register(stop_shared_ocr_worker)


def shared_ocr_worker_snapshot() -> dict:
    if _process is None or _process.poll() is not None:
        return {"status": "stopped", "device": None, "cache_entries": 0, "error": None}
    url = os.environ.get("AUTOMATION_OCR_WORKER_URL", "")
    token = os.environ.get("AUTOMATION_OCR_WORKER_TOKEN", "")
    req = request.Request(
        f"{url.rstrip('/')}/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with request.urlopen(req, timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = "ready" if payload.get("ready") else ("error" if payload.get("error") else "initializing")
        return {
            "status": status,
            "device": payload.get("device"),
            "cache_entries": int(payload.get("cache_entries") or 0),
            "error": payload.get("error"),
        }
    except (error.URLError, TimeoutError, OSError, ValueError):
        return {"status": "starting", "device": None, "cache_entries": 0, "error": None}
