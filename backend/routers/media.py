import os
import shutil
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from backend.config import SHARED_MEDIA_DIR

router = APIRouter(prefix="/api/media", tags=["media"])

VIDEO_EXTS = {".mp4", ".mov", ".webm"}

@router.post("/upload")
async def upload_media(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    SHARED_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = []

    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        is_video = ext in VIDEO_EXTS
        timestamp_prefix = int(time.time() * 1000)
        clean_filename = f"{timestamp_prefix}_{file.filename}"
        dest_path = SHARED_MEDIA_DIR / clean_filename

        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        size = dest_path.stat().st_size
        uploaded.append({
            "filename": clean_filename,
            "original_name": file.filename,
            "size_bytes": size,
            "type": "reel" if is_video else "photo",
            "url": f"/shared_media/{clean_filename}",
            "path": str(dest_path),
        })

    return {"success": True, "count": len(uploaded), "files": uploaded}

@router.get("/list")
def list_media():
    if not SHARED_MEDIA_DIR.exists():
        return []

    result = []
    for item in SHARED_MEDIA_DIR.iterdir():
        if item.is_file() and not item.name.startswith("."):
            ext = item.suffix.lower()
            is_video = ext in VIDEO_EXTS
            stat = item.stat()
            result.append({
                "filename": item.name,
                "size_bytes": stat.st_size,
                "created_at": stat.st_ctime,
                "type": "reel" if is_video else "photo",
            })

    result.sort(key=lambda x: x["created_at"], reverse=True)
    return result
