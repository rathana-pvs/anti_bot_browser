from fastapi import APIRouter

from backend.services import storage_housekeeping as housekeeping

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("")
def get_storage():
    """Free disk space now, plus usage and results from the last housekeeping run."""
    return {"free_bytes": housekeeping.disk_free(), "low_disk_bytes": housekeeping.LOW_DISK_BYTES,
            "critical_disk_bytes": housekeeping.CRITICAL_DISK_BYTES, "last_run": housekeeping.read_state() or None,
            "progress": housekeeping.progress()}


@router.post("/clean")
def clean_storage(dry_run: bool = False):
    """Clean now (or a preview with dry_run) in the background; follow it with GET /api/storage."""
    return {"started": housekeeping.start(dry_run, "manual"), "progress": housekeeping.progress()}
