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
    if "mode" in payload:
        settings_manager.update_resource_mode(payload.get("mode", "auto"))
    if "max_parallel" in payload or "reserve_percent" in payload:
        try:
            settings_manager.update_adaptive_limits(
                max_parallel=payload.get("max_parallel"),
                reserve_percent=payload.get("reserve_percent"),
                clear_max="max_parallel" in payload and payload["max_parallel"] is None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
