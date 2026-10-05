import json
import secrets
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from backend.services.profile_groups import list_groups, create_group

from backend.config import CPU_THREADS, PROFILES_DIR, TOTAL_MEMORY_GB, FALLBACK_TIMEZONE
from backend.models.profile import NetworkIntent, ProfileCreateRequest, ProfileUpdateRequest
from backend.services.docker_service import (
    get_container_stats, get_container_status, get_profile_disk_usage,
    paste_text_to_container, remove_container, run_profile_action,
)
from backend.services.evidence_cleanup import clean_profile_evidence
from backend.services.profile_service import (
    PROFILE_ID_RE, PROFILE_SCHEMA_VERSION, allocate_profile_identity, atomic_write_json,
    compatibility_fingerprint, creation_lock, migrate_profile,
    profile_defaults, resolve_requested_environment, utc_now, validate_timezone,
)
from backend.services.proxy_service import assign_proxy_by_id, release_proxy, load_proxy_pool, lookup_proxy_geography, test_proxy_ping, check_facebook_response, check_proxy_speed


router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def _profile_dir(profile_id: str) -> Path:
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile id")
    return PROFILES_DIR / profile_id


def _default_account(account: dict | None = None) -> dict:
    value = {
        "platform": "facebook", "email": "", "notes": "",
        "warming_start_date": None, "warming_complete": False,
        "warming_week": 1, "posts_today": 0, "last_post_date": None,
    }
    value.update(account or {})
    return value


def _validated_resources(resources: dict) -> dict:
    cpu_limit = float(resources["cpu_limit"])
    memory_mb = int(resources["memory_mb"])
    if cpu_limit > CPU_THREADS:
        raise ValueError(f"CPU limit cannot exceed host capacity ({CPU_THREADS} threads)")
    host_memory_mb = TOTAL_MEMORY_GB * 1024
    if memory_mb > host_memory_mb:
        raise ValueError(f"Memory limit cannot exceed host capacity ({host_memory_mb} MiB)")
    return {"cpu_limit": cpu_limit, "memory_mb": memory_mb}


def _proxy_timezone(network: dict) -> str | None:
    """Use the exit IP, never cached geography or the proxy endpoint's IP."""
    try:
        geo = lookup_proxy_geography({
            "host": network.get("proxy_host"), "port": network.get("proxy_port"),
            "username": network.get("proxy_user"), "password": network.get("proxy_pass"),
        })
        return validate_timezone(geo["timezone"])
    except Exception:
        return None


def _network_from_intent(profile_id: str, intent: dict) -> tuple[dict, str | None]:
    mode = intent["mode"]
    if mode == "direct":
        release_proxy(profile_id)
        return {
            "mode": "direct", "proxy_id": None, "proxy_type": "socks5",
            "proxy_host": "", "proxy_port": None, "proxy_user": "", "proxy_pass": "",
        }, None
    if mode == "pool":
        proxy = assign_proxy_by_id(profile_id, str(intent["proxy_id"]))
        network = {
            "mode": "pool", "proxy_id": proxy["id"],
            "proxy_type": proxy.get("type") or "socks5",
            "proxy_host": proxy["host"], "proxy_port": int(proxy["port"]),
            "proxy_user": proxy.get("username") or "",
            "proxy_pass": proxy.get("password") or "",
        }
    else:
        release_proxy(profile_id)
        network = {
            "mode": "custom", "proxy_id": None, "proxy_type": "socks5",
            "proxy_host": str(intent["host"]).strip(), "proxy_port": int(intent["port"]),
            "proxy_user": str(intent.get("username") or "").strip(),
            "proxy_pass": str(intent.get("password") or ""),
        }
    return network, _proxy_timezone(network)


def _environment_for_network(requested: dict, network: dict, proxy_timezone: str | None) -> dict:
    value = deepcopy(requested)
    if network["mode"] != "direct":
        if proxy_timezone:
            value.update(timezone_policy="proxy", timezone=proxy_timezone)
        elif value.get("timezone_policy") != "manual":
            # An unavailable lookup must require an explicit manual confirmation.
            value.update(timezone_policy="proxy", timezone=None)
    return value


class GroupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


@router.get("/groups")
def get_profile_groups():
    return list_groups()


@router.post("/groups", status_code=201)
def add_profile_group(request: GroupCreateRequest):
    try:
        return {"name": create_group(request.name)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/detect-timezone")
def detect_network_timezone(intent: NetworkIntent):
    defaults = profile_defaults()
    if intent.mode == "direct":
        return {"detected": defaults["host_timezone_detected"],
                "timezone": defaults["default_environment"]["timezone"],
                "source": "host", "timezone_options": defaults["timezone_options"]}
    if intent.mode == "pool":
        proxy = next((p for p in load_proxy_pool() if p["id"] == intent.proxy_id), None)
        if not proxy:
            raise HTTPException(status_code=404, detail="Selected proxy does not exist")
        network = {"proxy_host": proxy["host"], "proxy_port": proxy["port"],
                   "proxy_user": proxy.get("username"), "proxy_pass": proxy.get("password")}
    else:
        network = {"proxy_host": intent.host, "proxy_port": intent.port,
                   "proxy_user": intent.username, "proxy_pass": intent.password}
    timezone_name = _proxy_timezone(network)
    return {"detected": timezone_name is not None,
            "timezone": timezone_name or FALLBACK_TIMEZONE,
            "source": "proxy", "timezone_options": defaults["timezone_options"],
            "fallback_timezone": FALLBACK_TIMEZONE}


@router.get("/defaults")
def get_profile_defaults():
    return profile_defaults()


@router.get("")
def list_profiles():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profiles = []
    for entry in PROFILES_DIR.iterdir():
        if not entry.is_dir() or entry.name == "shared_media":
            continue
        config_path = entry / "config.json"
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            data, _ = migrate_profile(data, entry)
            profile_id = data.get("id", entry.name)
            data["status"] = get_container_status(profile_id)
            stats = get_container_stats(f"isolated_{profile_id}")
            if stats:
                data["cpu_usage"] = stats.get("cpu")
                data["ram_usage"] = stats.get("mem")
            elif data["status"] == "running":
                data["cpu_usage"] = "0.0%"
                data["ram_usage"] = "Active"
            disk = get_profile_disk_usage(profile_id)
            if disk:
                data["disk_usage"] = disk
            profiles.append(data)
        except Exception as exc:
            print(f"Error parsing config for {entry.name}: {exc}")
    profiles.sort(key=lambda item: item.get("id", ""))
    return profiles


@router.post("", status_code=201)
def create_profile(request: ProfileCreateRequest):
    with creation_lock():
        profile_id, vnc_port, ws_port = allocate_profile_identity()
        profile_dir = _profile_dir(profile_id)
        config_path = profile_dir / "config.json"
        if config_path.exists():
            raise HTTPException(status_code=409, detail="Allocated profile id already exists")
        proxy_reserved = False
        try:
            network, proxy_timezone = _network_from_intent(profile_id, request.network.model_dump())
            proxy_reserved = network["mode"] == "pool"
            requested = resolve_requested_environment(
                _environment_for_network(request.requested_environment.model_dump(), network, proxy_timezone),
                network_mode=network["mode"], proxy_timezone=proxy_timezone,
            )
            profile = {
                "schema_version": PROFILE_SCHEMA_VERSION, "configuration_revision": 1,
                "id": profile_id, "name": request.name.strip(), "group": request.group, "status": "stopped",
                "created_at": utc_now(), "updated_at": utc_now(),
                "network": network, "requested_environment": requested,
                "effective_environment": None, "observed_environment": None,
                "restart_required": False,
                "resources": _validated_resources(request.resources.model_dump()),
                "effective_resources": None,
                "behavior_mode": request.behavior_mode,
                "behavior_seed": secrets.randbits(63) or 1,
                "automation": request.automation.model_dump(),
                "fingerprint": compatibility_fingerprint(requested),
                "container": {
                    "id": None, "vnc_port": vnc_port, "ws_port": ws_port,
                    "volume_path": f"profiles/{profile_id}/chrome_data",
                },
                "account": _default_account(request.account),
            }
            (profile_dir / "chrome_data").mkdir(parents=True, exist_ok=False)
            atomic_write_json(config_path, profile)
            return profile
        except HTTPException:
            if proxy_reserved:
                release_proxy(profile_id)
            raise
        except Exception as exc:
            if proxy_reserved:
                release_proxy(profile_id)
            if profile_dir.exists() and not config_path.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{profile_id}")
def update_profile(profile_id: str, request: ProfileUpdateRequest):
    profile_dir = _profile_dir(profile_id)
    config_path = profile_dir / "config.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")
    existing = json.loads(config_path.read_text(encoding="utf-8"))
    existing, _ = migrate_profile(existing, profile_dir)
    original = deepcopy(existing)
    try:
        if request.group is not None:
            existing["group"] = request.group
        if request.name is not None:
            existing["name"] = request.name.strip()
        proxy_timezone = None
        if request.network is not None:
            existing["network"], proxy_timezone = _network_from_intent(
                profile_id, request.network.model_dump()
            )
        if request.requested_environment is not None or request.network is not None:
            requested_input = (
                request.requested_environment.model_dump()
                if request.requested_environment is not None
                else deepcopy(existing["requested_environment"])
            )
            if request.network is None and existing["network"]["mode"] != "direct":
                proxy_timezone = _proxy_timezone(existing["network"])
            existing["requested_environment"] = resolve_requested_environment(
                _environment_for_network(requested_input, existing["network"], proxy_timezone), network_mode=existing["network"]["mode"],
                proxy_timezone=proxy_timezone,
            )
            existing["fingerprint"] = compatibility_fingerprint(
                existing["requested_environment"], existing.get("fingerprint")
            )
        if request.account is not None:
            existing["account"] = {**existing.get("account", {}), **request.account}
        if request.resources is not None:
            existing["resources"] = _validated_resources(request.resources.model_dump())
        if request.behavior_mode is not None:
            existing["behavior_mode"] = request.behavior_mode
        if request.automation is not None:
            existing["automation"] = request.automation.model_dump()
        runtime_changed = (
            original.get("network") != existing.get("network")
            or original.get("requested_environment") != existing.get("requested_environment")
            or original.get("resources") != existing.get("resources")
        )
        existing["configuration_revision"] = int(existing.get("configuration_revision") or 1) + 1
        existing["updated_at"] = utc_now()
        if runtime_changed:
            existing["restart_required"] = True
        atomic_write_json(config_path, existing)
        return existing
    except Exception as exc:
        release_proxy(profile_id)
        previous = original.get("network") or {}
        if previous.get("mode") == "pool" and previous.get("proxy_id"):
            try:
                assign_proxy_by_id(profile_id, previous["proxy_id"])
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{profile_id}/proxy-latency")
def check_profile_proxy_latency(profile_id: str):
    config_path = _profile_dir(profile_id) / "config.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")
    network = json.loads(config_path.read_text()).get("network") or {}
    host, port = network.get("proxy_host"), network.get("proxy_port")
    if not host or not port or network.get("mode") == "direct":
        raise HTTPException(status_code=400, detail="This profile uses a direct connection")
    result = test_proxy_ping(host, port)
    return {**result, "last_checked": utc_now()}


@router.post("/{profile_id}/facebook-response")
def check_profile_facebook_response(profile_id: str):
    config_path = _profile_dir(profile_id) / "config.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")
    network = json.loads(config_path.read_text(encoding="utf-8")).get("network") or {}
    try:
        return {**check_facebook_response(network), "last_checked": utc_now()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{profile_id}/proxy-speed")
def check_profile_proxy_speed(profile_id: str, direction: Literal["download", "upload"] = Query(...)):
    config_path = _profile_dir(profile_id) / "config.json"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Profile not found")
    network = json.loads(config_path.read_text(encoding="utf-8")).get("network") or {}
    try:
        return {**check_proxy_speed(network, direction), "last_checked": utc_now()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{profile_id}/action")
async def profile_action(profile_id: str, payload: dict = Body(...)):
    _profile_dir(profile_id)
    action = payload.get("action")
    if action not in ("start", "stop", "pause", "unpause"):
        raise HTTPException(status_code=400, detail="Invalid action")
    try:
        return await run_profile_action(profile_id, action)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{profile_id}/paste")
async def profile_paste(profile_id: str, payload: dict = Body(...)):
    _profile_dir(profile_id)
    text = payload.get("text")
    if text is None:
        raise HTTPException(status_code=400, detail="Text is required")
    try:
        return await paste_text_to_container(profile_id, text, payload.get("mode", "both"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{profile_id}")
def delete_profile(profile_id: str, deleteData: bool = Query(False)):
    profile_dir = _profile_dir(profile_id)
    try:
        release_proxy(profile_id)
        remove_container(profile_id)
        if profile_dir.exists():
            if deleteData:
                shutil.rmtree(profile_dir, ignore_errors=True)
            else:
                config_path = profile_dir / "config.json"
                if config_path.exists():
                    config_path.unlink()
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{profile_id}/clean-evidence")
def clean_evidence_for_profile(profile_id: str, payload: dict = Body(...)):
    profile_dir = _profile_dir(profile_id)
    if not profile_dir.exists():
        raise HTTPException(status_code=404, detail="Profile not found")
    days = int(payload.get("days", 3))
    metrics = clean_profile_evidence(profile_dir, days_threshold=days)
    return {"success": True, "profile_id": profile_id, "days_kept": days, "metrics": metrics}
