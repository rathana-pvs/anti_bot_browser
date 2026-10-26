from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field, ConfigDict
from backend.services import proxy_groups
from backend.models.profile import NetworkIntent
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
    check_proxy_speed,
    update_proxy_metadata,
)

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


class ProxySelection(BaseModel):
    proxy_ids: list[str] = Field(min_length=1, max_length=10000)


class ProxyGroupSelection(ProxySelection):
    group: str = Field(max_length=80)


class GroupName(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


@router.get('/groups')
def get_proxy_groups():
    return proxy_groups.list_groups()


@router.post('/groups', status_code=201)
def create_proxy_group(request: GroupName):
    try:
        return {'name': proxy_groups.create_group(request.name)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put('/groups/{name:path}')
def rename_proxy_group(name: str, request: GroupName):
    try:
        return proxy_groups.rename_group(name, request.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete('/groups/{name:path}')
def delete_proxy_group(name: str):
    try:
        return proxy_groups.delete_group(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/set-group")
def set_proxy_group(request: ProxyGroupSelection):
    raise HTTPException(status_code=409, detail=proxy_groups.AUTOMATIC_FOLDER_MESSAGE)


@router.post("/{proxy_id}/speed-test")
def measure_saved_proxy_speed(proxy_id: str):
    return test_network_speed(NetworkIntent(mode="pool", proxy_id=proxy_id))


@router.post("/speed-test")
def test_network_speed(intent: NetworkIntent):
    """Measure a selected proxy without reserving it or creating a profile."""
    if intent.mode == "direct":
        raise HTTPException(status_code=400, detail="Select a proxy to test its speed")
    if intent.mode == "pool":
        proxy = next((p for p in load_proxy_pool() if p.get("id") == intent.proxy_id), None)
        if not proxy:
            raise HTTPException(status_code=404, detail="Selected proxy does not exist")
        network = {"mode": "pool", "proxy_host": proxy["host"], "proxy_port": proxy["port"],
                   "proxy_type": proxy.get("type") or "socks5",
                   "proxy_user": proxy.get("username"), "proxy_pass": proxy.get("password")}
    else:
        network = {"mode": "custom", "proxy_host": intent.host, "proxy_port": intent.port,
                   "proxy_type": "socks5", "proxy_user": intent.username, "proxy_pass": intent.password}
    try:
        latency = test_proxy_ping(network["proxy_host"], network["proxy_port"])
        download = check_proxy_speed(network, "download")
        upload = check_proxy_speed(network, "upload")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = {"latency": latency, "download": download, "upload": upload,
              "last_checked": datetime.now(timezone.utc).isoformat()}
    if intent.mode == "pool":
        saved = update_proxy_metadata([intent.proxy_id], speed_test=result)
        if not saved["updated_ids"]:
            raise HTTPException(status_code=404, detail="Proxy removed during speed test")
    return result


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
    group = payload.get("group", "")
    if not isinstance(group, str) or len(group) > 80:
        raise HTTPException(status_code=422, detail="Folder name must be at most 80 characters")
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
    checked_at = datetime.now(timezone.utc).isoformat()
    saved = update_proxy_metadata([proxy_id], latency_ms=test_res.get("latency_ms"), last_checked=checked_at)
    if not saved['updated_ids']:
        raise HTTPException(status_code=404, detail="Proxy removed during test")
    current = next(p for p in load_proxy_pool() if p['id'] == proxy_id)
    return {**current, "test": test_res}


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
