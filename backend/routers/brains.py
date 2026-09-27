import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Body
from backend.config import BRAIN_UPLOAD_DIR
from backend.services.automation_service import run_brain_cli

router = APIRouter(prefix="/api/brains", tags=["brains"])

@router.get("")
def list_brains():
    try:
        return run_brain_cli(["list"])
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.post("/{brain_id}/validate")
def validate_brain(brain_id: str, payload: dict = Body(...)):
    try:
        args = ["validate", brain_id]
        if payload.get("version"):
            args.extend(["--version", str(payload["version"])])
        return run_brain_cli(args)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))

@router.post("/{brain_id}/activate")
def activate_brain(brain_id: str, payload: dict = Body(...)):
    version = str(payload.get("version") or "")
    if not version:
        raise HTTPException(status_code=400, detail="version is required")
    try:
        return run_brain_cli(["activate", brain_id, version])
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))

@router.post("/{brain_id}/rollback")
def rollback_brain(brain_id: str):
    try:
        listing = run_brain_cli(["list"])
        family = next((b for b in listing.get("brains", []) if b.get("id") == brain_id), None)
        if not family:
            raise HTTPException(status_code=404, detail="Brain is not installed")
        if not family.get("previous_version"):
            raise HTTPException(status_code=400, detail="No previous version is available")
        return run_brain_cli(["activate", brain_id, family["previous_version"]])
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))

@router.post("/upload")
async def upload_brain(
    package: UploadFile = File(...),
    activate: str = Form("false"),
):
    BRAIN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = BRAIN_UPLOAD_DIR / package.filename
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(package.file, buffer)

        installed = run_brain_cli(["install", "--archive", str(temp_path)])
        activation = None
        if activate.lower() == "true":
            installed_info = installed.get("installed", {})
            activation = run_brain_cli(["activate", installed_info["id"], installed_info["directory"]])

        return {**installed, "activation": activation}
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err))
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
