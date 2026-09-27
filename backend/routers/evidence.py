from fastapi import APIRouter, Body
from backend.config import PROFILES_DIR
from backend.services.evidence_cleanup import clean_all_profiles_evidence

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

@router.post("/clean")
def clean_all_evidence(payload: dict = Body(...)):
    days = int(payload.get("days", 3))
    metrics = clean_all_profiles_evidence(PROFILES_DIR, days_threshold=days)
    return {"success": True, "days_kept": days, "metrics": metrics}
