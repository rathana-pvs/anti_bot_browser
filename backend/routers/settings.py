from fastapi import APIRouter, Body, HTTPException
from backend.services.docker_service import resource_mode_snapshot
from backend.services.resource_service import settings_manager
from backend.services.ocr_worker_service import shared_ocr_worker_snapshot

router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("/resource-mode")
def get_resource_mode():
    snapshot = resource_mode_snapshot()
    snapshot["runtime"]["ocr_worker"] = shared_ocr_worker_snapshot()
    return snapshot

@router.put("/resource-mode")
def update_resource_mode(payload: dict = Body(...)):
    try:
        settings_manager.update_execution_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from backend.services import adaptive_runtime
    adaptive_runtime.tick()
    snapshot = resource_mode_snapshot()
    snapshot["runtime"]["ocr_worker"] = shared_ocr_worker_snapshot()
    return snapshot


@router.put("/ocr-engine")
def update_ocr_engine(payload: dict = Body(...)):
    try:
        settings_manager.update_ocr_engine(payload.get("engine"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Selection is persisted, while active processes keep their startup engine.
    return get_resource_mode()


@router.get("/posting-plan")
def get_posting_plan():
    return settings_manager.posting_plan


@router.put("/posting-plan")
def update_posting_plan(payload: dict = Body(...)):
    try:
        return settings_manager.update_posting_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/auto-warming")
def get_auto_warming():
    from backend.services import auto_warming
    return auto_warming.summary()


@router.put("/auto-warming")
def update_auto_warming(payload: dict = Body(...)):
    from backend.services import auto_warming
    try:
        settings_manager.update_auto_warming(payload.get("enabled"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return auto_warming.summary()
