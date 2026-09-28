from __future__ import annotations

import json
import re
import socket
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.config import CPU_THREADS, PROFILES_DIR, TOTAL_MEMORY_GB, get_host_timezone
from backend.services.proxy_service import load_proxy_pool


PROFILE_SCHEMA_VERSION = 4
DEFAULT_RESOURCE_LIMITS = {"cpu_limit": 4.0, "memory_mb": 4096}
SUPPORTED_RESOLUTIONS = (
    "1920x1080",
    "1600x900",
    "1536x864",
    "1440x900",
    "1366x768",
    "2560x1440",
)
PROFILE_ID_RE = re.compile(r"^profile_(\d+)$")
_profile_creation_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    tmp.replace(path)


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc
    return value


def profile_defaults() -> dict[str, Any]:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "supported_resolutions": list(SUPPORTED_RESOLUTIONS),
        "default_environment": {
            "screen_resolution": "1920x1080",
            "timezone_policy": "host",
            "timezone": get_host_timezone(),
            "language": "en-US",
            "user_agent_policy": "browser_default",
            "user_agent": None,
            "rendering_mode": "host_gpu",
        },
        "default_resources": dict(DEFAULT_RESOURCE_LIMITS),
        "resource_options": {
            "cpu_limits": [1, 2, 4, 6, 8],
            "memory_mb": [1024, 2048, 3072, 4096, 6144, 8192],
            "host_cpu_threads": CPU_THREADS,
            "host_memory_mb": TOTAL_MEMORY_GB * 1024,
        },
        "enforceable_fields": [
            "screen_resolution",
            "timezone",
            "language",
            "rendering_mode",
        ],
        "observed_fields": [
            "screen_resolution",
            "timezone",
            "language",
            "browser_version",
            "webgl_renderer",
        ],
    }


def _port_is_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def allocate_profile_identity(root: Path = PROFILES_DIR) -> tuple[str, int, int]:
    used_ids: set[str] = set()
    used_ports: set[int] = set()
    highest_index = 0
    if root.exists():
        for directory in root.iterdir():
            if not directory.is_dir() or directory.name == "shared_media":
                continue
            used_ids.add(directory.name)
            match = PROFILE_ID_RE.fullmatch(directory.name)
            if match:
                highest_index = max(highest_index, int(match.group(1)))
            config_path = directory / "config.json"
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                container = config.get("container") or {}
                for key in ("vnc_port", "ws_port"):
                    if container.get(key):
                        used_ports.add(int(container[key]))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue

    index = highest_index + 1
    while f"profile_{index:03d}" in used_ids:
        index += 1
    profile_id = f"profile_{index:03d}"

    vnc_port = 5901
    while vnc_port in used_ports or not _port_is_available(vnc_port):
        vnc_port += 1
    ws_port = 6081
    while ws_port in used_ports or ws_port == vnc_port or not _port_is_available(ws_port):
        ws_port += 1
    return profile_id, vnc_port, ws_port


def creation_lock():
    return _profile_creation_lock


def resolve_requested_environment(
    requested: dict[str, Any],
    *,
    network_mode: str,
    proxy_timezone: str | None = None,
) -> dict[str, Any]:
    value = deepcopy(requested)
    resolution = value.get("screen_resolution") or "1920x1080"
    if resolution not in SUPPORTED_RESOLUTIONS:
        raise ValueError(f"Unsupported screen resolution: {resolution}")
    policy = value.get("timezone_policy") or ("proxy" if network_mode != "direct" else "host")
    if policy == "proxy":
        if network_mode == "direct":
            raise ValueError("Proxy timezone policy requires a proxy")
        timezone_name = proxy_timezone or value.get("timezone")
        if not timezone_name:
            raise ValueError("The selected proxy has no timezone")
    elif policy == "host":
        timezone_name = get_host_timezone()
    else:
        timezone_name = value.get("timezone")
    value["screen_resolution"] = resolution
    value["timezone_policy"] = policy
    value["timezone"] = validate_timezone(str(timezone_name))
    value["language"] = value.get("language") or "en-US"
    value["user_agent_policy"] = value.get("user_agent_policy") or "browser_default"
    if value["user_agent_policy"] == "browser_default":
        value["user_agent"] = None
    value["rendering_mode"] = value.get("rendering_mode") or "host_gpu"
    return value


def compatibility_fingerprint(requested: dict[str, Any], legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    legacy = legacy or {}
    return {
        "webgl_vendor": legacy.get("webgl_vendor"),
        "webgl_renderer": legacy.get("webgl_renderer"),
        "user_agent": requested.get("user_agent") or "",
        "screen_resolution": requested["screen_resolution"],
        "color_depth": legacy.get("color_depth", 24),
        "timezone": requested["timezone"],
        "language": requested["language"],
        "hardware_concurrency": legacy.get("hardware_concurrency"),
        "device_memory": legacy.get("device_memory"),
    }


def legacy_network_to_v2(network: dict[str, Any], profile_id: str) -> dict[str, Any]:
    host = str(network.get("proxy_host") or "").strip()
    port = network.get("proxy_port")
    if not host:
        return {
            "mode": "direct",
            "proxy_id": None,
            "proxy_type": "socks5",
            "proxy_host": "",
            "proxy_port": None,
            "proxy_user": "",
            "proxy_pass": "",
        }
    match = next(
        (
            proxy
            for proxy in load_proxy_pool()
            if proxy.get("profile_id") == profile_id
            or (proxy.get("host") == host and str(proxy.get("port")) == str(port))
        ),
        None,
    )
    return {
        "mode": "pool" if match else "custom",
        "proxy_id": match.get("id") if match else None,
        "proxy_type": network.get("proxy_type") or "socks5",
        "proxy_host": host,
        "proxy_port": int(port or 1080),
        "proxy_user": network.get("proxy_user") or "",
        "proxy_pass": network.get("proxy_pass") or "",
    }


def migrate_profile(profile: dict[str, Any], profile_dir: Path, *, persist: bool = True) -> tuple[dict[str, Any], bool]:
    source_version = int(profile.get("schema_version") or 1)
    if source_version >= PROFILE_SCHEMA_VERSION:
        return profile, False
    migrated = deepcopy(profile)
    if source_version == 3:
        requested = migrated.get("requested_environment") or {}
        requested["user_agent_policy"] = "browser_default"
        requested["user_agent"] = None
        migrated["requested_environment"] = requested
        migrated["fingerprint"] = compatibility_fingerprint(
            requested, migrated.get("fingerprint")
        )
        migrated["schema_version"] = PROFILE_SCHEMA_VERSION
        migrated["configuration_revision"] = int(migrated.get("configuration_revision") or 1) + 1
        migrated["effective_environment"] = None
        migrated["observed_environment"] = None
        migrated["restart_required"] = True
        if persist:
            config_path = profile_dir / "config.json"
            backup = profile_dir / "config.json.v3.bak"
            if config_path.exists() and not backup.exists():
                backup.write_bytes(config_path.read_bytes())
            atomic_write_json(config_path, migrated)
        return migrated, True
    if source_version == 2:
        migrated["schema_version"] = PROFILE_SCHEMA_VERSION
        migrated["configuration_revision"] = int(migrated.get("configuration_revision") or 1) + 1
        previous_requested = migrated.get("requested_environment") or {}
        requested = resolve_requested_environment(
            {
                "screen_resolution": previous_requested.get("screen_resolution") or "1920x1080",
                "timezone_policy": previous_requested.get("timezone_policy") or "host",
                "timezone": previous_requested.get("timezone") or get_host_timezone(),
                "language": previous_requested.get("language") or "en-US",
                "user_agent_policy": "browser_default",
                "user_agent": None,
                "rendering_mode": previous_requested.get("rendering_mode") or "host_gpu",
            },
            network_mode=(migrated.get("network") or {}).get("mode", "direct"),
        )
        migrated["requested_environment"] = requested
        migrated["fingerprint"] = compatibility_fingerprint(
            requested, migrated.get("fingerprint")
        )
        migrated["resources"] = dict(DEFAULT_RESOURCE_LIMITS)
        migrated["effective_resources"] = None
        migrated["restart_required"] = True
        if persist:
            config_path = profile_dir / "config.json"
            backup = profile_dir / f"config.json.v{source_version}.bak"
            if config_path.exists() and not backup.exists():
                backup.write_bytes(config_path.read_bytes())
            atomic_write_json(config_path, migrated)
        return migrated, True
    profile_id = str(migrated.get("id") or profile_dir.name)
    legacy_fp = migrated.get("fingerprint") or {}
    network = legacy_network_to_v2(migrated.get("network") or {}, profile_id)
    timezone_policy = "proxy" if network["mode"] != "direct" else "host"
    proxy_tz = None
    if network.get("proxy_id"):
        match = next((p for p in load_proxy_pool() if p.get("id") == network["proxy_id"]), None)
        proxy_tz = match.get("timezone") if match else None
    requested = resolve_requested_environment(
        {
            "screen_resolution": legacy_fp.get("screen_resolution") or "1920x1080",
            "timezone_policy": timezone_policy,
            "timezone": proxy_tz or legacy_fp.get("timezone") or get_host_timezone(),
            "language": legacy_fp.get("language") or "en-US",
            "user_agent_policy": "browser_default",
            "user_agent": None,
            "rendering_mode": "host_gpu",
        },
        network_mode=network["mode"],
        proxy_timezone=proxy_tz,
    )
    migrated.update(
        {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "configuration_revision": 1,
            "network": network,
            "requested_environment": requested,
            "effective_environment": None,
            "observed_environment": None,
            "restart_required": False,
            "resources": dict(DEFAULT_RESOURCE_LIMITS),
            "effective_resources": None,
            "fingerprint": compatibility_fingerprint(requested, legacy_fp),
        }
    )
    if persist:
        config_path = profile_dir / "config.json"
        backup = profile_dir / f"config.json.v{source_version}.bak"
        if config_path.exists() and not backup.exists():
            backup.write_bytes(config_path.read_bytes())
        atomic_write_json(config_path, migrated)
    return migrated, True


def environment_differences(requested: dict[str, Any], observed: dict[str, Any]) -> dict[str, str]:
    differences: dict[str, str] = {}
    for key in ("screen_resolution", "timezone", "language"):
        actual = observed.get(key)
        if actual is None:
            differences[key] = "not_measured"
        elif str(actual).casefold() == str(requested.get(key)).casefold():
            differences[key] = "matched"
        else:
            differences[key] = "different"
    return differences

