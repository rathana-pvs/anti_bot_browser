import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from backend.config import (
    ROOT_DIR,
    PROFILES_DIR,
    QUEUE_FILE,
    QUEUE_CLAIM_LOCK_FILE,
    MANAGER_INSTANCE_ID,
    OCR_RUNTIME,
    CPU_THREADS,
    TOTAL_MEMORY_GB,
)
from backend.services.queue_scheduler import (
    DEFAULT_SCHEDULER_CONFIG,
    is_lease_active,
    list_queue_leases,
    scheduler_snapshot,
    can_acquire_scheduler_slot,
    claim_execution_lease,
    refresh_execution_lease,
    release_execution_lease,
    find_stale_running_executions,
    compute_execution_schedule,
)
from backend.services.queue_safety import (
    classify_interrupted_execution,
    is_execution_deletion_locked,
    batch_contains_unresolved_execution,
    validate_facebook_permalink,
    apply_verified_permalink_backfill,
    apply_automation_permalink_result,
    apply_comment_evidence_backfill,
    can_retry_first_comment,
    apply_comment_retry_result,
    apply_warming_result,
)
from backend.services.profile_pipeline import (
    shuffled_profile_order,
    ordered_due_executions,
    count_buffered_preparations,
)
from backend.services.telemetry_summary import build_queue_telemetry_summary
from backend.services.support_bundle import (
    build_execution_diagnostic,
    build_queue_diagnostic_summary,
    sanitize_diagnostic_value,
    sanitize_diagnostic_string,
)
from backend.services.docker_service import (
    get_scheduler_config,
    get_container_status,
    count_running_profile_containers,
    claim_container_start_admission,
)

_QUEUE_IO_LOCK = threading.RLock()


def _queue_backup_file() -> Path:
    return QUEUE_FILE.with_suffix(".json.bak")


def _read_queue_file(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Queue root in {path.name} is not an object")
    return data


def _atomic_json_write(path: Path, data: dict) -> None:
    """Write JSON through a writer-specific temp file before replacing path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def load_posting_queue() -> dict:
    with _QUEUE_IO_LOCK:
        try:
            if QUEUE_FILE.exists():
                return _read_queue_file(QUEUE_FILE)
        except Exception as err:
            print(f"Error loading posting_queue.json: {err}")
            backup_file = _queue_backup_file()
            try:
                if backup_file.exists():
                    recovered = _read_queue_file(backup_file)
                    print(f"Recovered posting queue from {backup_file.name}")
                    return recovered
            except Exception as backup_err:
                print(f"Error loading posting queue backup: {backup_err}")
    return {"queue_version": "2.0", "daily_batches": []}

def save_posting_queue(data: dict) -> bool:
    with _QUEUE_IO_LOCK:
        try:
            # Keep the last known-good generation. A corrupt primary is never
            # promoted over an existing valid backup.
            if QUEUE_FILE.exists():
                try:
                    previous = _read_queue_file(QUEUE_FILE)
                    _atomic_json_write(_queue_backup_file(), previous)
                except Exception as backup_err:
                    print(f"Skipping posting queue backup because the current file is invalid: {backup_err}")
            _atomic_json_write(QUEUE_FILE, data)
            return True
        except Exception as err:
            print(f"Error atomically saving posting_queue.json: {err}")
            return False

def with_queue_claim_lock(callback):
    scheduler_cfg = get_scheduler_config()
    fd = None
    try:
        try:
            fd = os.open(str(QUEUE_CLAIM_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                mtime = os.path.getmtime(QUEUE_CLAIM_LOCK_FILE)
                if (time.time() - mtime) * 1000 <= scheduler_cfg["lease_ttl_ms"]:
                    raise RuntimeError("queue_claim_in_progress")
                try:
                    os.unlink(str(QUEUE_CLAIM_LOCK_FILE))
                except Exception:
                    pass
                fd = os.open(str(QUEUE_CLAIM_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except Exception as e:
                raise RuntimeError("queue_claim_in_progress") from e

        content = json.dumps({"owner_id": MANAGER_INSTANCE_ID, "pid": os.getpid()}).encode("utf-8")
        os.write(fd, content)
        return callback()
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                if QUEUE_CLAIM_LOCK_FILE.exists():
                    QUEUE_CLAIM_LOCK_FILE.unlink()
            except Exception:
                pass

def find_queue_execution(queue: dict, execution_id: str) -> dict | None:
    for batch in queue.get("daily_batches", []):
        for post in batch.get("posts", []):
            for execution in post.get("executions", []):
                if execution.get("execution_id") == execution_id:
                    return {"execution": execution, "post": post, "batch": batch}
    return None

def persist_queue_execution_stage(execution_id: str, stage: str, timestamp: str | None = None, lease_id: str | None = None) -> bool:
    if not execution_id or not stage:
        return False
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        return False

    execution = match["execution"]
    if lease_id and execution.get("scheduler_lease", {}).get("lease_id") != lease_id:
        return False

    history = execution.get("stage_history")
    if not isinstance(history, list):
        history = []
        execution["stage_history"] = history

    prev = history[-1] if history else None
    if not prev or prev.get("stage") != stage:
        history.append({"stage": stage, "timestamp": timestamp})

    execution["stage"] = stage
    execution["stage_updated_at"] = timestamp
    return save_posting_queue(queue)

def apply_interrupted_execution_recovery(execution: dict, now: str | None = None):
    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    recovery = classify_interrupted_execution(execution)
    execution["last_active_stage"] = recovery["interruptedStage"]
    execution["status"] = recovery["status"]
    execution["stage"] = recovery["stage"]
    execution["ended_at"] = now
    execution["recovered_at"] = now
    execution["error"] = recovery["error"]

    history = execution.get("stage_history")
    if not isinstance(history, list):
        history = []
        execution["stage_history"] = history
    history.append({
        "stage": recovery["stage"],
        "timestamp": now,
        "reason": "manager_restart_recovery",
        "interrupted_stage": recovery["interruptedStage"],
    })

def generate_spun_caption(base_caption: str, index: int = 0, profile_id: str = "") -> str:
    if not base_caption or not base_caption.strip():
        return ""
    hooks = [
        "Here's something you need to know 👇",
        "Take a look at this ⚡",
        "If you haven't seen this yet, watch closely 👀",
        "Quick tip for today 🚀",
        "Game changer: don't sleep on this 💡",
        "Consistency beats talent every single day 🔥",
        "Check this out right now 💥",
        "Save this for your routine 📌",
        "Must watch: here is how it works 🎯",
        "Real growth starts with small steps daily ✨",
    ]
    closers = [
        "\n\nDrop a comment if you agree! 💬",
        "\n\nSave this for later 📌",
        "\n\nTag someone who needs to see this! 👇",
        "\n\nFollow for more daily insights 🚀",
        "\n\nWhat are your thoughts on this? 💭",
        "\n\nHit save to try this out! ⚡",
    ]
    seed = (index + len(profile_id) + len(base_caption)) % len(hooks)
    hook = hooks[seed]
    closer = closers[(seed + 2) % len(closers)]
    return f"{hook}\n\n{base_caption.strip()}{closer}"

def safe_evidence_directory(evidence_dir: str | None) -> Path | None:
    if not evidence_dir or not isinstance(evidence_dir, str):
        return None
    p = Path(evidence_dir)
    if not p.exists() or not p.is_dir():
        return None
    try:
        real_ev = p.resolve()
        real_prof = PROFILES_DIR.resolve()
        if not str(real_ev).startswith(str(real_prof)):
            return None
        return real_ev
    except Exception:
        return None

def add_sanitized_evidence(bundle_dir: Path, execution: dict, options: dict) -> dict:
    ev_dir = safe_evidence_directory(execution.get("evidence_dir"))
    if not ev_dir:
        return {"metadata_files": 0, "screenshot_files": 0}

    metadata_dir = bundle_dir / "evidence_metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_files = 0

    json_files = [f for f in ev_dir.iterdir() if f.is_file() and f.suffix == ".json"][:80]
    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                parsed = json.load(f)
            sanitized = sanitize_diagnostic_value(parsed, options)
            with open(metadata_dir / jf.name, "w", encoding="utf-8") as f:
                json.dump(sanitized, f, indent=2)
            metadata_files += 1
        except Exception:
            pass

    screenshot_files = 0
    if options.get("includeEvidence"):
        screenshot_dir = bundle_dir / "evidence_screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        pat = re.compile(r"(error|fail|requires_review|publication_verification|permalink_extraction|after_first_comment|login_check)", re.IGNORECASE)
        pngs = [
            f for f in ev_dir.iterdir()
            if f.is_file() and f.suffix == ".png" and pat.search(f.name)
        ]
        pngs.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for s in pngs[:6]:
            try:
                shutil.copy2(s, screenshot_dir / s.name)
                screenshot_files += 1
            except Exception:
                pass

    return {"metadata_files": metadata_files, "screenshot_files": screenshot_files}

def create_support_archive(match: dict | None = None, queue: dict | None = None, description: str = "", include_content: bool = False, include_evidence: bool = False) -> tuple[Path, str, Path]:
    if queue is None:
        queue = load_posting_queue()

    temp_root = Path(tempfile.mkdtemp(prefix="automat-fb-support-"))
    report_id = match["execution"]["execution_id"] if match else "global"
    safe_report_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(report_id))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%SZ")
    bundle_name = f"support_{safe_report_id}_{timestamp}"
    bundle_dir = temp_root / bundle_name
    bundle_dir.mkdir(parents=True, exist_ok=True)

    options = {
        "includeContent": include_content,
        "includeEvidence": include_evidence,
        "rootDir": str(ROOT_DIR),
        "description": description,
    }

    report = build_execution_diagnostic(match, options) if match else build_queue_diagnostic_summary(queue, options)
    evidence_counts = add_sanitized_evidence(bundle_dir, match["execution"], options) if match else {"metadata_files": 0, "screenshot_files": 0}

    with open(bundle_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    sys_info = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": "1.0.0",
        "cpu_threads": CPU_THREADS,
        "total_memory_mb": round(TOTAL_MEMORY_GB * 1024),
        "ocr_runtime": OCR_RUNTIME,
    }
    with open(bundle_dir / "system_info.json", "w", encoding="utf-8") as f:
        json.dump(sanitize_diagnostic_value(sys_info, options), f, indent=2)

    readme_content = f"""AUTOMAT FB SUPPORT BUNDLE

Report type: {'single execution' if match else 'global diagnostics'}
Generated: {datetime.now(timezone.utc).isoformat()}
Evidence metadata files: {evidence_counts['metadata_files']}
Evidence screenshots: {evidence_counts['screenshot_files']}

Privacy:
- Browser profiles, cookies, passwords, proxy credentials, and access tokens are never included.
- Caption/comment content included: {'yes' if include_content else 'no'}.
- Screenshots included: {'yes' if include_evidence else 'no'}.
- Review the files before sending them to support.
"""
    with open(bundle_dir / "README.txt", "w", encoding="utf-8") as f:
        f.write(readme_content)

    archive_path = temp_root / f"{bundle_name}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(bundle_dir):
            for file in files:
                abs_f = Path(root) / file
                rel_f = abs_f.relative_to(temp_root)
                zf.write(abs_f, rel_f)

    return archive_path, archive_path.name, temp_root
