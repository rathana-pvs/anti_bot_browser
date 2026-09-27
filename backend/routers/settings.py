from fastapi import APIRouter, Body
from backend.services.docker_service import resource_mode_snapshot
from backend.services.resource_service import settings_manager

router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("/resource-mode")
def get_resource_mode():
    return resource_mode_snapshot()

@router.put("/resource-mode")
def update_resource_mode(payload: dict = Body(...)):
    mode = payload.get("mode", "auto")
    settings_manager.update_resource_mode(mode)
    return resource_mode_snapshot()
