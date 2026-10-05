from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field
from backend.services.proxy_service import (
    load_proxy_pool,
    save_proxy_pool,
    import_proxies_from_text,
    delete_proxy,
    delete_proxies,
    refresh_proxy_geographies,
    refresh_proxy_geography,
    refresh_proxy_statuses,
    test_proxy_ping,
)

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


class ProxySelection(BaseModel):
    proxy_ids: list[str] = Field(min_length=1, max_length=10000)


@router.post("/delete-selected")
def remove_selected_proxies(request: ProxySelection):
    return delete_proxies(request.proxy_ids)

@router.get("")
def get_proxies():
    return load_proxy_pool()

@router.post("/import")
def import_proxies(payload: dict = Body(...)):
    text = payload.get("text")
    if not text or not isinstance(text, str):
        raise HTTPException(status_code=400, detail="No proxy text provided")
    result = import_proxies_from_text(text)
    added_ids = result.pop("added_ids", [])
    status = refresh_proxy_statuses(added_ids) if added_ids else {
        "online": 0, "offline": 0, "geo_checked": 0, "geo_failed": 0,
    }
    return {
        **result,
        "status_checked": status.get("total", 0),
        "online": status["online"],
        "offline": status["offline"],
        "geo_checked": status["geo_checked"],
        "geo_failed": status["geo_failed"],
    }


@router.post("/refresh-status")
def refresh_all_proxy_statuses():
    proxy_ids = [proxy["id"] for proxy in load_proxy_pool()]
    return refresh_proxy_statuses(proxy_ids)

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


@router.post("/{proxy_id}/geo-check")
def geo_check_proxy(proxy_id: str):
    try:
        return refresh_proxy_geography(proxy_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/geo-check-all")
def geo_check_all_proxies():
    proxy_ids = [proxy["id"] for proxy in load_proxy_pool()]
    result = refresh_proxy_geographies(proxy_ids)
    return {"checked": result["checked"], "failed": result["failed"]}

@router.delete("/{proxy_id}")
def remove_proxy(proxy_id: str):
    try:
        deleted = delete_proxy(proxy_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return {"success": True}
