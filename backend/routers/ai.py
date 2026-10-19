from fastapi import APIRouter, HTTPException, Body
from backend.services.queue_service import generate_spun_caption

router = APIRouter(prefix="/api/ai", tags=["ai"])

@router.post("/spin-caption")
def spin_caption(payload: dict = Body(...)):
    base_caption = payload.get("base_caption")
    count = payload.get("count", 3)
    profile_ids = payload.get("profile_ids") or []

    if not base_caption:
        raise HTTPException(status_code=400, detail="base_caption is required")

    variations = []
    num = max(1, int(count) if count else 3)
    for i in range(num):
        pid = profile_ids[i] if i < len(profile_ids) else f"profile_{i + 1}"
        variations.append({
            "profile_id": pid,
            "spun_caption": generate_spun_caption(base_caption, i, pid),
        })

    return {"success": True, "variations": variations}


from backend.services import caption_hook


@router.post('/caption-hooks')
def generate_caption_hooks(payload: caption_hook.GenerateRequest):
    return caption_hook.generate(payload)


@router.post('/caption-hooks/approve')
def approve_caption_hook(payload: caption_hook.ApproveRequest):
    try:
        return caption_hook.approve(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
