import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from fastapi.responses import FileResponse

from backend.services.queue_service import (
    load_posting_queue,
    find_queue_execution,
    create_support_archive,
)

router = APIRouter(prefix="/api/support", tags=["support"])

def cleanup_temp_dir(temp_dir: Path):
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

@router.post("/execution/{execution_id}")
def export_execution_support(
    execution_id: str,
    background_tasks: BackgroundTasks,
    payload: dict = Body(None),
):
    payload = payload or {}
    queue = load_posting_queue()
    match = find_queue_execution(queue, execution_id)
    if not match:
        raise HTTPException(status_code=404, detail="Execution not found")

    try:
        archive_path, filename, temp_root = create_support_archive(
            match=match,
            queue=queue,
            description=str(payload.get("description") or ""),
            include_content=payload.get("include_content") is True,
            include_evidence=payload.get("include_evidence") is True,
        )
        background_tasks.add_task(cleanup_temp_dir, temp_root)
        return FileResponse(
            path=str(archive_path),
            filename=filename,
            media_type="application/zip",
        )
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.post("/export")
def export_global_support(
    background_tasks: BackgroundTasks,
    payload: dict = Body(None),
):
    payload = payload or {}
    queue = load_posting_queue()

    try:
        archive_path, filename, temp_root = create_support_archive(
            queue=queue,
            description=str(payload.get("description") or ""),
        )
        background_tasks.add_task(cleanup_temp_dir, temp_root)
        return FileResponse(
            path=str(archive_path),
            filename=filename,
            media_type="application/zip",
        )
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))
