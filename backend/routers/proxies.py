from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Body
from backend.services.proxy_service import (
    load_proxy_pool,
    save_proxy_pool,
    import_proxies_from_text,
    delete_proxy,
    test_proxy_ping,
)

router = APIRouter(prefix="/api/proxies", tags=["proxies"])

@router.get("")
def get_proxies():
    return load_proxy_pool()

@router.post("/import")
def import_proxies(payload: dict = Body(...)):
    text = payload.get("text")
    if not text or not isinstance(text, str):
        raise HTTPException(status_code=400, detail="No proxy text provided")
    return import_proxies_from_text(text)

@router.post("/{proxy_id}/test")
def test_proxy(proxy_id: str):
    pool = load_proxy_pool()
    proxy = next((p for p in pool if p.get("id") == proxy_id), None)
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")

    test_res = test_proxy_ping(proxy["host"], proxy["port"])
    proxy["latency_ms"] = test_res.get("latency_ms")
    proxy["last_checked"] = datetime.now(timezone.utc).isoformat()
    save_proxy_pool(pool)

    return {**proxy, "test": test_res}

@router.delete("/{proxy_id}")
def remove_proxy(proxy_id: str):
    delete_proxy(proxy_id)
    return {"success": True}
