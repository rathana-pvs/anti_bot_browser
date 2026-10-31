import asyncio

from fastapi import APIRouter

from backend.services import storage_housekeeping as housekeeping

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("")
def get_storage():
    """Free disk space now, plus usage and results from the last housekeeping run."""
    return {"free_bytes": housekeeping.disk_free(), "low_disk_bytes": housekeeping.LOW_DISK_BYTES,
            "critical_disk_bytes": housekeeping.CRITICAL_DISK_BYTES, "last_run": housekeeping.read_state() or None}


@router.post("/clean")
async def clean_storage(dry_run: bool = False):
    """Clean now: one housekeeping pass, or a preview of it with dry_run."""
    return await asyncio.to_thread(housekeeping.run, dry_run, "manual")
