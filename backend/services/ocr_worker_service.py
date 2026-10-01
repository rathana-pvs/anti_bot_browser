"""Lifecycle manager for the shared OCR subprocess."""

import json
import os
import secrets
import socket
import subprocess
from urllib import error, request
from pathlib import Path

from backend.config import AUTOMATION_PYTHON, CPU_THREADS, DATA_DIR, OCR_RUNTIME, ROOT_DIR
from backend.services.resource_service import recommended_ocr_threads, settings_manager


_process: subprocess.Popen | None = None
_log_handle = None


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
    log_path = DATA_DIR / "ocr_worker.log"
    try:
        _log_handle = open(log_path, "a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["AUTOMATION_OCR_DEVICE"] = OCR_RUNTIME["device"]
        env["AUTOMATION_OCR_THREADS"] = str(threads)
        _process = subprocess.Popen(
            [
                str(AUTOMATION_PYTHON),
                "-u",
                str(worker_script),
                "--port",
                str(port),
                "--token",
                token,
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
