import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

def parse_run_timestamp(folder_name: str, folder_path: Path | None = None) -> float | None:
    match = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", str(folder_name))
    if match:
        y, m, d, hh, mm, ss = map(int, match.groups())
        try:
            dt = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)
            return dt.timestamp() * 1000
        except ValueError:
            pass

    if folder_path and folder_path.exists():
        try:
            return folder_path.stat().st_mtime * 1000
        except Exception:
            return None
    return None

def calculate_directory_size_bytes(dir_path: Path) -> int:
    if not dir_path.exists():
        return 0
    total = 0
    for root, _, files in os.walk(dir_path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except Exception:
                pass
    return total

def format_bytes(bytes_num: int | float) -> str:
    if not isinstance(bytes_num, (int, float)) or bytes_num <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    val = float(bytes_num)
    unit_idx = 0
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.1f} {units[unit_idx]}"

def clean_profile_evidence(profile_dir: Path, days_threshold: int = 3, now_ms: float | None = None) -> dict:
    evidence_dir = profile_dir / "automation_evidence"
    if not evidence_dir.exists():
        return {
            "deleted_runs": 0,
            "freed_bytes": 0,
            "freed_mb": 0.0,
            "freed_formatted": "0 B",
            "kept_runs": 0,
        }

    if now_ms is None:
        now_ms = time.time() * 1000
    cutoff_ms = now_ms - (days_threshold * 24 * 60 * 60 * 1000)

    deleted_runs = 0
    freed_bytes = 0
    kept_runs = 0

    try:
        for entry in evidence_dir.iterdir():
            if not entry.is_dir():
                continue
            ts = parse_run_timestamp(entry.name, entry)
            if ts is None:
                kept_runs += 1
                continue

            if ts < cutoff_ms:
                size = calculate_directory_size_bytes(entry)
                try:
                    shutil.rmtree(entry)
                    deleted_runs += 1
                    freed_bytes += size
                except Exception as err:
                    print(f"Failed to remove evidence directory {entry}: {err}")
                    kept_runs += 1
            else:
                kept_runs += 1
    except Exception as err:
        print(f"Failed to read evidence directory {evidence_dir}: {err}")

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    return {
        "deleted_runs": deleted_runs,
        "freed_bytes": freed_bytes,
        "freed_mb": freed_mb,
        "freed_formatted": format_bytes(freed_bytes),
        "kept_runs": kept_runs,
    }

def clean_all_profiles_evidence(profiles_dir: Path, days_threshold: int = 3, now_ms: float | None = None) -> dict:
    if not profiles_dir.exists():
        return {
            "total_deleted_runs": 0,
            "total_freed_bytes": 0,
            "total_freed_mb": 0.0,
            "total_freed_formatted": "0 B",
            "total_kept_runs": 0,
            "profiles_cleaned": 0,
        }

    total_deleted = 0
    total_freed = 0
    total_kept = 0
    profiles_cleaned = 0

    try:
        for entry in profiles_dir.iterdir():
            if not entry.is_dir() or entry.name == "shared_media":
                continue
            res = clean_profile_evidence(entry, days_threshold, now_ms)
            total_deleted += res["deleted_runs"]
            total_freed += res["freed_bytes"]
            total_kept += res["kept_runs"]
            if res["deleted_runs"] > 0:
                profiles_cleaned += 1
    except Exception as err:
        print(f"Failed to clean all profiles evidence: {err}")

    return {
        "total_deleted_runs": total_deleted,
        "total_freed_bytes": total_freed,
        "total_freed_mb": round(total_freed / (1024 * 1024), 2),
        "total_freed_formatted": format_bytes(total_freed),
        "total_kept_runs": total_kept,
        "profiles_cleaned": profiles_cleaned,
    }
