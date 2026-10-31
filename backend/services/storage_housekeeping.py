"""Storage housekeeping: keep the app's data folder from growing day by day.

Runs once a day while the machine is calm (sooner when the disk is low), and
on demand from Settings → Storage. Every step only removes what can be rebuilt
or is no longer needed:

- Evidence is kept by what it proves. Runs behind an unconfirmed post, a
  review, an open network recovery or a current Needs attention reason are
  never removed. Published posts keep their proof screenshots for 30 days and
  their step screenshots for 3; other runs last 7 days. Each account keeps its
  newest runs whatever their age, and screenshots older than a day are
  shrunk in place (smaller PNGs, so the evidence viewer still shows them).
- Chrome's HTTP and code caches are trimmed (oldest files first) only while
  that account's browser is stopped. Logins, cookies and storage are never
  touched.
- Finished queue batches older than 30 days move to a monthly archive file.
- An old Python environment backup is removed once the new one is installed.
- Large logs are rotated.

Below LOW_DISK_BYTES free, housekeeping runs early; below CRITICAL_DISK_BYTES
no new queue browser starts, so a publish never runs out of disk midway.
"""

import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.config import DATA_DIR, PROFILES_DIR, ROOT_DIR
from backend.services.profile_service import atomic_write_json

DAY_S = 86400
GB = 1024 ** 3
MB = 1024 ** 2

PUBLISHED_PROOF_DAYS = 30
PUBLISHED_STEPS_DAYS = 3
OTHER_RUN_DAYS = 7
KEEP_NEWEST_RUNS = 5
SHRINK_AFTER_DAYS = 1
SHRINK_WIDTH = 1280
SHRUNK_MARKER = ".shrunk"

CACHE_DIRS = ("Default/Cache/Cache_Data", "Default/Code Cache/js", "Default/Code Cache/wasm")
CACHE_TRIM_ABOVE = 300 * MB
CACHE_TRIM_TO = 200 * MB

QUEUE_ARCHIVE_DAYS = 30
ARCHIVE_DIR = DATA_DIR / "queue_archive"
FINAL_STATUSES = {"published", "completed", "cancelled", "stopped"}
BACKUP_KEEP_DAYS = 3
LOG_ROTATE_BYTES = 5 * MB

LOW_DISK_BYTES = 10 * GB
CRITICAL_DISK_BYTES = 3 * GB
RUN_EVERY_S = DAY_S
LOW_DISK_RUN_EVERY_S = 3600

STATE_FILE = DATA_DIR / "storage_housekeeping.json"
UNRESOLVED_STATUSES = {"uncertain", "needs_review", "failed_after_publish"}
OPEN_RECOVERY_STATES = {"waiting_network", "confirming_network", "waiting_review", "ready", "retrying"}
ACTIVE_STATUSES = {"pending", "ready", "preparing", "running"}
# Screenshots that prove what a published post looked like at and after the click.
PROOF_MARKERS = ("before_publish", "before_reel_publish", "publication_verification", "latest_", "after_first_comment")

_run_lock = threading.Lock()


# Small helpers ----------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def disk_free(path: Path = ROOT_DIR) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def disk_too_low() -> bool:
    """New queue browsers wait while the disk is nearly full."""
    free = disk_free()
    return 0 < free < CRITICAL_DISK_BYTES


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_time(directory: Path) -> float:
    from backend.services.evidence_cleanup import parse_run_timestamp
    stamp = parse_run_timestamp(directory.name, directory)
    return stamp / 1000 if stamp else directory.stat().st_mtime


# What must be kept -------------------------------------------------------------

def protected_execution_ids(queue: dict, health: dict | None = None) -> set[str]:
    """Items whose evidence may still be needed to decide what happened."""
    ids = set()
    for batch in queue.get("daily_batches") or []:
        for post in batch.get("posts") or []:
            for execution in post.get("executions") or []:
                recovery = (execution.get("network_recovery") or {}).get("state")
                if (execution.get("status") in UNRESOLVED_STATUSES | ACTIVE_STATUSES
                        or recovery in OPEN_RECOVERY_STATES):
                    ids.add(execution.get("execution_id"))
    for value in (health or {}).values():
        for item in value.get("evidence") or []:
            ids.add(item.get("execution_id"))
    ids.discard(None)
    return ids


def _statuses(queue: dict) -> dict[str, str]:
    return {execution.get("execution_id"): execution.get("status")
            for batch in queue.get("daily_batches") or []
            for post in batch.get("posts") or []
            for execution in post.get("executions") or []}


# Evidence ---------------------------------------------------------------------

def _shrink(png: Path) -> int:
    """Rewrite one screenshot as a smaller PNG; returns the bytes saved."""
    from PIL import Image
    before = png.stat().st_size
    temporary = png.with_suffix(".shrinking.png")
    try:
        with Image.open(png) as image:
            image = image.convert("RGB")
            if image.width > SHRINK_WIDTH:
                image = image.resize((SHRINK_WIDTH, round(image.height * SHRINK_WIDTH / image.width)), Image.LANCZOS)
            image.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(temporary, optimize=True)
        if temporary.stat().st_size < before:
            os.replace(temporary, png)
            return before - png.stat().st_size
    except Exception as error:
        print(f"[Storage] Could not shrink {png}: {error}")
    temporary.unlink(missing_ok=True)
    return 0


def clean_evidence(queue: dict, protected: set[str], now: float, dry_run: bool = False) -> dict:
    statuses = _statuses(queue)
    report = {"runs_removed": 0, "step_screenshots_removed": 0, "screenshots_shrunk": 0, "freed_bytes": 0, "kept_protected": 0}
    for profile in sorted(PROFILES_DIR.glob("*/automation_evidence")):
        runs = sorted((path for path in profile.iterdir() if path.is_dir() and not path.is_symlink()),
                      key=_run_time, reverse=True)
        for index, run in enumerate(runs):
            age_days = (now - _run_time(run)) / DAY_S
            execution_id = _read_json(run / "attempt.json").get("execution_id")
            status = statuses.get(execution_id) or _read_json(run / "result.json").get("status") or ""
            if execution_id in protected or status in UNRESOLVED_STATUSES:
                report["kept_protected"] += 1
                continue
            if not (run / "result.json").exists() and age_days < 1:
                continue  # still being written
            newest = index < KEEP_NEWEST_RUNS
            keep_days = PUBLISHED_PROOF_DAYS if status == "published" else OTHER_RUN_DAYS
            if age_days > keep_days and not newest:
                report["freed_bytes"] += _size(run)
                report["runs_removed"] += 1
                if not dry_run:
                    shutil.rmtree(run, ignore_errors=True)
                continue
            pngs = sorted(run.glob("*.png"))
            if status == "published" and age_days > PUBLISHED_STEPS_DAYS and not newest:
                steps = [png for png in pngs if not any(marker in png.name for marker in PROOF_MARKERS)]
                for png in steps:
                    report["freed_bytes"] += png.stat().st_size
                    report["step_screenshots_removed"] += 1
                    if not dry_run:
                        png.unlink(missing_ok=True)
                pngs = [png for png in pngs if png not in steps]
            if age_days > SHRINK_AFTER_DAYS and not (run / SHRUNK_MARKER).exists():
                for png in pngs:
                    if dry_run:
                        report["freed_bytes"] += int(png.stat().st_size * 0.8)
                    else:
                        report["freed_bytes"] += _shrink(png)
                    report["screenshots_shrunk"] += 1
                if not dry_run:
                    (run / SHRUNK_MARKER).touch()
    return report


# Chrome caches ------------------------------------------------------------------

def _browser_stopped(profile_id: str) -> bool:
    from backend.services import container_lifecycle
    from backend.services.docker_service import get_container_status
    try:
        return get_container_status(profile_id) != "running" and profile_id not in container_lifecycle.snapshot()
    except Exception:
        return False


def trim_chrome_caches(dry_run: bool = False) -> dict:
    report = {"profiles_trimmed": 0, "freed_bytes": 0, "skipped_running": 0}
    for chrome in sorted(PROFILES_DIR.glob("*/chrome_data")):
        profile_id = chrome.parent.name
        entries = []
        for relative in CACHE_DIRS:
            folder = chrome / relative
            if folder.is_dir():
                for path in folder.iterdir():
                    # Index files describe the cache; Chrome rebuilds entries that are gone.
                    if path.is_file() and not path.name.startswith("index"):
                        stat = path.stat()
                        entries.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
        total = sum(size for _at, size, _path in entries)
        if total <= CACHE_TRIM_ABOVE:
            continue
        if not _browser_stopped(profile_id):
            report["skipped_running"] += 1
            continue
        freed = 0
        for _at, size, path in sorted(entries):
            if total - freed <= CACHE_TRIM_TO:
                break
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    continue
            freed += size
        report["profiles_trimmed"] += 1
        report["freed_bytes"] += freed
    return report


# Queue archive ------------------------------------------------------------------

def _batch_finished_before(batch: dict, cutoff: datetime) -> bool:
    latest = None
    for post in batch.get("posts") or []:
        for execution in post.get("executions") or []:
            if execution.get("status") not in FINAL_STATUSES:
                return False
            comment = str(execution.get("first_comment_status") or "")
            if execution.get("status") == "published" and ("fail" in comment or "pending" in comment):
                return False
            for key in ("ended_at", "published_at", "stage_updated_at", "scheduled_at"):
                at = _parse(execution.get(key))
                if at:
                    latest = max(latest, at) if latest else at
                    break
    latest = latest or _parse(batch.get("created_at"))
    return bool(latest and latest < cutoff)


def archive_queue(now: datetime, dry_run: bool = False) -> dict:
    from backend.services import account_health
    from backend.services.queue_service import load_posting_queue, save_posting_queue, with_queue_claim_lock
    cutoff = now - timedelta(days=QUEUE_ARCHIVE_DAYS)
    report = {"batches_archived": 0}
    if dry_run:
        old = [batch for batch in load_posting_queue().get("daily_batches") or [] if _batch_finished_before(batch, cutoff)]
        return {"batches_archived": len(old)}

    def update():
        queue = load_posting_queue()
        old = [batch for batch in queue.get("daily_batches") or [] if _batch_finished_before(batch, cutoff)]
        report["batches_archived"] = len(old)
        if not old:
            return
        # Account history keeps what the stages need before the items leave the queue.
        account_health.sync_history(queue, now)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for batch in old:
            month = (_parse(batch.get("created_at")) or now).strftime("%Y-%m")
            path = ARCHIVE_DIR / f"{month}.json"
            archive = _read_json(path) or {"version": 1, "batches": []}
            if not any(item.get("batch_id") == batch.get("batch_id") for item in archive["batches"]):
                archive["batches"].append(batch)
            atomic_write_json(path, archive)
        archived = {id(batch) for batch in old}
        queue["daily_batches"] = [batch for batch in queue["daily_batches"] if id(batch) not in archived]
        if not save_posting_queue(queue):
            raise RuntimeError("Could not save the queue after archiving")

    try:
        with_queue_claim_lock(update)
    except RuntimeError as error:
        report["error"] = str(error)
    return report


# Leftovers ----------------------------------------------------------------------

def remove_old_runtime_backups(now: float, dry_run: bool = False) -> dict:
    """An old Python environment is only kept until the new one has been installed."""
    report = {"backups_removed": 0, "freed_bytes": 0}
    installed = DATA_DIR / "runtime-installed-version"
    backups = DATA_DIR / "python-runtime" / "backups"
    if not installed.exists() or not backups.is_dir():
        return report
    for backup in backups.iterdir():
        if not backup.is_dir() or backup.is_symlink():
            continue
        modified = backup.stat().st_mtime
        if modified < installed.stat().st_mtime and now - modified > BACKUP_KEEP_DAYS * DAY_S:
            report["freed_bytes"] += _size(backup)
            report["backups_removed"] += 1
            if not dry_run:
                shutil.rmtree(backup, ignore_errors=True)
    return report


def rotate_logs(dry_run: bool = False) -> dict:
    report = {"logs_rotated": 0, "freed_bytes": 0}
    for log in DATA_DIR.glob("*.log"):
        size = log.stat().st_size
        if size <= LOG_ROTATE_BYTES:
            continue
        previous = log.with_suffix(".log.1")
        report["freed_bytes"] += previous.stat().st_size if previous.exists() else 0
        report["logs_rotated"] += 1
        if not dry_run:
            os.replace(log, previous)
            log.touch()
    return report


# Usage and the run itself ---------------------------------------------------------

def usage() -> dict:
    """Space by category. Walks the folders, so it is measured with each run."""
    profiles = [path for path in PROFILES_DIR.iterdir() if path.is_dir() and path.name.startswith("profile_")] \
        if PROFILES_DIR.is_dir() else []
    caches = sum(_size(profile / "chrome_data" / relative) for profile in profiles for relative in CACHE_DIRS)
    chrome = sum(_size(profile / "chrome_data") for profile in profiles)
    return {
        "evidence": sum(_size(profile / "automation_evidence") for profile in profiles),
        "chrome_cache": caches,
        "browser_data": max(0, chrome - caches),
        "runtime": _size(ROOT_DIR / "automation" / "venv") + _size(DATA_DIR / "python-runtime"),
        "runtime_backups": _size(DATA_DIR / "python-runtime" / "backups"),
        "queue": _size(DATA_DIR / "posting_queue.json") + _size(DATA_DIR / "posting_queue.json.bak") + _size(ARCHIVE_DIR),
        "media": _size(PROFILES_DIR / "shared_media") + _size(DATA_DIR / "reel_creator"),
    }


def read_state() -> dict:
    return _read_json(STATE_FILE)


def run(dry_run: bool = False, reason: str = "manual") -> dict:
    """One housekeeping pass. Only one runs at a time."""
    if not _run_lock.acquire(blocking=False):
        return {"busy": True, **read_state()}
    try:
        from backend.services import account_health
        from backend.services.queue_service import load_posting_queue
        now_dt = datetime.now(timezone.utc)
        now = now_dt.timestamp()
        free_before = disk_free()
        queue = load_posting_queue()
        # A preview writes nothing, not even the usual history sync.
        history = account_health._load_history() if dry_run else account_health.sync_history(queue, now_dt)
        health = {pid: account_health.compute(config, account_health.entries_for(history, pid), now_dt)
                  for pid, config in account_health._profile_configs().items()}
        protected = protected_execution_ids(queue, health)
        steps = {
            "evidence": clean_evidence(queue, protected, now, dry_run),
            "chrome_cache": trim_chrome_caches(dry_run),
            "queue": archive_queue(now_dt, dry_run),
            "runtime_backups": remove_old_runtime_backups(now, dry_run),
            "logs": rotate_logs(dry_run),
        }
        result = {
            "ran_at": now_dt.isoformat(), "reason": reason, "dry_run": dry_run,
            "freed_bytes": sum(step.get("freed_bytes", 0) for step in steps.values()),
            "free_before": free_before, "free_after": disk_free(), "steps": steps, "usage": usage(),
        }
        if not dry_run:
            atomic_write_json(STATE_FILE, result)
        return result
    finally:
        _run_lock.release()


def due(now: float | None = None) -> str | None:
    """Why a scheduled run should happen now, or None."""
    now = now or time.time()
    last = _parse(read_state().get("ran_at"))
    elapsed = now - last.timestamp() if last else float("inf")
    free = disk_free()
    if 0 < free < LOW_DISK_BYTES and elapsed >= LOW_DISK_RUN_EVERY_S:
        return "low_disk"
    return "daily" if elapsed >= RUN_EVERY_S else None


async def tick():
    """Scheduled every 30 minutes; runs when due and the machine is calm."""
    import asyncio
    reason = due()
    if not reason:
        return
    from backend.services import adaptive_runtime
    if reason == "daily" and adaptive_runtime.adaptive_enabled() and adaptive_runtime.current_decision().state != "calm":
        return
    try:
        await asyncio.to_thread(run, False, reason)
    except Exception as error:
        print(f"[Storage] Housekeeping failed: {error}")
