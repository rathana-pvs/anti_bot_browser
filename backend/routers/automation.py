from fastapi import APIRouter, HTTPException, Body
from backend.services.automation_service import (
    active_automation_tasks,
    launch_manual_automation,
    stop_automation_task,
    serialize_task_state,
    latest_queue_task_state,
)

router = APIRouter(prefix="/api/automation", tags=["automation"])

@router.post("/run")
async def run_automation(payload: dict = Body(...)):
    profile_id = payload.get("profile_id")
    task = payload.get("task")
    if not profile_id or not task:
        raise HTTPException(status_code=400, detail="profile_id and task are required")

    try:
        res = await launch_manual_automation(
            profile_id=profile_id,
            task=task,
            scrolls=payload.get("scrolls"),
            warming_options=payload.get("warming_options"),
            caption=payload.get("caption"),
            comment_link=payload.get("comment_link"),
            media=payload.get("media"),
            brain_version=payload.get("brain_version"),
        )
        if isinstance(res, dict) and res.get("status_code") == 409:
            raise HTTPException(status_code=409, detail=res)
        return {
            "success": True,
            "message": f'Started "{task}" task for profile "{profile_id}"',
            "state": res,
        }
    except HTTPException:
        raise
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err))
    except RuntimeError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.get("/status/{profile_id}")
def get_automation_status(profile_id: str):
    record = active_automation_tasks.get(profile_id)
    if not record:
        return latest_queue_task_state(profile_id) or {"profile_id": profile_id, "status": "idle", "logs": []}
    return serialize_task_state(record)

@router.post("/stop/{profile_id}")
def stop_automation(profile_id: str):
    try:
        return stop_automation_task(profile_id)
    except RuntimeError as err:
        raise HTTPException(status_code=400, detail=str(err))
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.get("/tasks")
def list_automation_tasks():
    return {pid: serialize_task_state(rec) for pid, rec in active_automation_tasks.items()}
