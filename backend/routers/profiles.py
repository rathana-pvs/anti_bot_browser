import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Body

from backend.config import PROFILES_DIR, REAL_HOST_SPECS
from backend.services.docker_service import (
    get_container_status,
    get_profile_disk_usage,
    container_stats_cache,
    run_profile_action,
    paste_text_to_container,
    remove_container,
)
from backend.services.proxy_service import (
    assign_proxy,
    release_proxy,
    sync_proxy_assignment,
)
from backend.services.evidence_cleanup import clean_profile_evidence

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

@router.get("")
def list_profiles():
    if not PROFILES_DIR.exists():
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        return []

    profiles = []
    for entry in PROFILES_DIR.iterdir():
        if entry.is_dir() and entry.name != "shared_media":
            config_path = entry / "config.json"
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    pid = data.get("id", entry.name)
                    data["status"] = get_container_status(pid)
                    c_name = f"isolated_{pid}"
                    if c_name in container_stats_cache:
                        data["cpu_usage"] = container_stats_cache[c_name].get("cpu")
                        data["ram_usage"] = container_stats_cache[c_name].get("mem")
                    disk = get_profile_disk_usage(pid)
                    if disk:
                        data["disk_usage"] = disk
                    profiles.append(data)
                except Exception as e:
                    print(f"Error parsing config for {entry.name}: {e}")

    profiles.sort(key=lambda p: p.get("id", ""))
    return profiles

@router.post("", status_code=201)
def create_profile(profile: dict = Body(...)):
    pid = profile.get("id")
    name = profile.get("name")
    if not pid or not name:
        raise HTTPException(status_code=400, detail="Missing profile id or name")

    profile_dir = PROFILES_DIR / pid
    data_dir = profile_dir / "chrome_data"
    config_path = profile_dir / "config.json"
    data_dir.mkdir(parents=True, exist_ok=True)

    network = profile.get("network") or {"proxy_type": "socks5", "proxy_host": "", "proxy_port": 1080}
    profile["network"] = network

    assigned_proxy = None
    if network.get("auto_assign") or (not network.get("proxy_host") and network.get("auto_assign") is not False and not network.get("direct")):
        assigned_proxy = assign_proxy(pid)
        if assigned_proxy:
            network["proxy_host"] = assigned_proxy["host"]
            network["proxy_port"] = assigned_proxy["port"]
            network["proxy_user"] = assigned_proxy.get("username", "")
            network["proxy_pass"] = assigned_proxy.get("password", "")
    elif network.get("proxy_host"):
        assigned_proxy = sync_proxy_assignment(pid, network.get("proxy_host"), network.get("proxy_port"))

    fp = profile.get("fingerprint") or {}
    selected_res = fp.get("screen_resolution") or "1920x1080"
    selected_tz = (assigned_proxy and assigned_proxy.get("timezone")) or fp.get("timezone") or "America/Los_Angeles"

    profile["fingerprint"] = {
        **REAL_HOST_SPECS,
        **fp,
        "webgl_vendor": REAL_HOST_SPECS["webgl_vendor"],
        "webgl_renderer": REAL_HOST_SPECS["webgl_renderer"],
        "hardware_concurrency": REAL_HOST_SPECS["hardware_concurrency"],
        "device_memory": REAL_HOST_SPECS["device_memory"],
        "user_agent": REAL_HOST_SPECS["user_agent"],
        "color_depth": REAL_HOST_SPECS["color_depth"],
        "screen_resolution": selected_res,
        "timezone": selected_tz,
    }

    profile["created_at"] = datetime.now(timezone.utc).isoformat()
    profile["status"] = "stopped"
    profile["container"] = profile.get("container") or {}
    profile["container"]["volume_path"] = f"profiles/{pid}/chrome_data"

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    return profile

@router.put("/{profile_id}")
def update_profile(profile_id: str, updates: dict = Body(...)):
    config_path = PROFILES_DIR / profile_id / "config.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")

    with open(config_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    if "name" in updates:
        existing["name"] = updates["name"]
    if "fingerprint" in updates:
        existing["fingerprint"] = {**existing.get("fingerprint", {}), **updates["fingerprint"]}
    if "network" in updates:
        existing["network"] = {**existing.get("network", {}), **updates["network"]}
    if "account" in updates:
        existing["account"] = {**existing.get("account", {}), **updates["account"]}

    if "network" in updates:
        bound = sync_proxy_assignment(profile_id, existing["network"].get("proxy_host"), existing["network"].get("proxy_port"))
        if bound and bound.get("timezone"):
            existing["fingerprint"]["timezone"] = bound["timezone"]

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    return existing

@router.post("/{profile_id}/action")
async def profile_action(profile_id: str, payload: dict = Body(...)):
    action = payload.get("action")
    if action not in ("start", "stop", "pause", "unpause"):
        raise HTTPException(status_code=400, detail="Invalid action")
    try:
        res = await run_profile_action(profile_id, action)
        return res
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.post("/{profile_id}/paste")
async def profile_paste(profile_id: str, payload: dict = Body(...)):
    text = payload.get("text")
    mode = payload.get("mode", "both")
    if text is None:
        raise HTTPException(status_code=400, detail="Text is required")
    try:
        res = await paste_text_to_container(profile_id, text, mode)
        return res
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.delete("/{profile_id}")
def delete_profile(profile_id: str, deleteData: bool = Query(False)):
    try:
        release_proxy(profile_id)
        remove_container(profile_id)

        profile_dir = PROFILES_DIR / profile_id
        if profile_dir.exists():
            if deleteData:
                shutil.rmtree(profile_dir, ignore_errors=True)
            else:
                cfg = profile_dir / "config.json"
                if cfg.exists():
                    cfg.unlink()

        return {"success": True}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))

@router.post("/{profile_id}/clean-evidence")
def clean_evidence_for_profile(profile_id: str, payload: dict = Body(...)):
    profile_dir = PROFILES_DIR / profile_id
    if not profile_dir.exists():
        raise HTTPException(status_code=404, detail="Profile not found")
    days = int(payload.get("days", 3))
    metrics = clean_profile_evidence(profile_dir, days_threshold=days)
    return {"success": True, "profile_id": profile_id, "days_kept": days, "metrics": metrics}
