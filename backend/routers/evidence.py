from fastapi import APIRouter, Body
from backend.config import PROFILES_DIR
from backend.services.evidence_cleanup import clean_all_profiles_evidence

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

@router.post("/clean")
def clean_all_evidence(payload: dict = Body(...)):
    days = int(payload.get("days", 3))
    from backend.services.queue_service import load_posting_queue
    from backend.services.storage_housekeeping import protected_execution_ids
    metrics = clean_all_profiles_evidence(PROFILES_DIR, days_threshold=days,
                                          protected=protected_execution_ids(load_posting_queue()))
    return {"success": True, "days_kept": days, "metrics": metrics}
