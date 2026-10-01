import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.models.profile import NetworkIntent, RequestedEnvironmentInput
from backend.models.profile import ProfileCreateRequest
from backend.routers import profiles as profile_router
from backend.services import profile_service
from backend.services import proxy_service
from backend.services import docker_service


def _config(root, profile_id, vnc, ws):
    directory = root / profile_id
    directory.mkdir()
    (directory / "config.json").write_text(
        json.dumps({"container": {"vnc_port": vnc, "ws_port": ws}}),
        encoding="utf-8",
    )


def test_identity_allocation_uses_monotonic_id_and_free_ports(tmp_path, monkeypatch):
    _config(tmp_path, "profile_001", 5901, 6081)
    _config(tmp_path, "profile_003", 5902, 6082)
    monkeypatch.setattr(profile_service, "_port_is_available", lambda _port: True)

    profile_id, vnc_port, ws_port = profile_service.allocate_profile_identity(tmp_path)

    assert profile_id == "profile_004"
    assert vnc_port == 5903
    assert ws_port == 6083


def test_network_modes_require_explicit_fields():
    assert NetworkIntent(mode="direct").mode == "direct"
    with pytest.raises(ValidationError):
        NetworkIntent(mode="pool")
    with pytest.raises(ValidationError):
        NetworkIntent(mode="custom", host="proxy.example")
    custom = NetworkIntent(mode="custom", host="proxy.example", port=1080)
    assert custom.port == 1080


def test_environment_rejects_unsupported_resolution():
    with pytest.raises(ValueError, match="Unsupported screen resolution"):
        profile_service.resolve_requested_environment(
            RequestedEnvironmentInput(screen_resolution="1111x777").model_dump(),
            network_mode="direct",
        )


def test_direct_legacy_profile_migrates_with_backup(tmp_path):
    directory = tmp_path / "profile_001"
    directory.mkdir()
    legacy = {
        "id": "profile_001",
        "network": {"proxy_host": "", "proxy_port": 1080},
        "fingerprint": {
            "screen_resolution": "1920x1080",
            "timezone": "UTC",
            "language": "en-US",
        },
    }
    config_path = directory / "config.json"
    config_path.write_text(json.dumps(legacy), encoding="utf-8")

    migrated, changed = profile_service.migrate_profile(legacy, directory)

    assert changed is True
    assert migrated["schema_version"] == 4
    assert migrated["network"]["mode"] == "direct"
    assert migrated["network"]["proxy_port"] is None
    assert migrated["effective_environment"] is None
    assert (directory / "config.json.v1.bak").exists()


def test_v2_profile_migrates_to_default_resource_limits(tmp_path):
    directory = tmp_path / "profile_002"
    directory.mkdir()
    profile = {
        "schema_version": 2,
        "configuration_revision": 1,
        "id": "profile_002",
        "network": {"mode": "direct"},
        "requested_environment": {"screen_resolution": "1920x1080"},
    }
    (directory / "config.json").write_text(json.dumps(profile), encoding="utf-8")

    migrated, changed = profile_service.migrate_profile(profile, directory)

    assert changed is True
    assert migrated["schema_version"] == 4
    assert migrated["resources"] == {"cpu_limit": 4.0, "memory_mb": 4096}
    assert migrated["effective_resources"] is None
    assert migrated["restart_required"] is True
    assert (directory / "config.json.v2.bak").exists()


def test_v3_profile_migrates_to_browser_default_without_resetting_resources(tmp_path):
    directory = tmp_path / "profile_003"
    directory.mkdir()
    profile = {
        "schema_version": 3,
        "configuration_revision": 7,
        "id": "profile_003",
        "network": {"mode": "direct"},
        "requested_environment": {
            "screen_resolution": "1920x1080",
            "timezone": "UTC",
            "language": "en-US",
            "user_agent_policy": "pinned",
            "user_agent": "Old Browser/120",
            "rendering_mode": "host_gpu",
        },
        "fingerprint": {"user_agent": "Old Browser/120"},
        "resources": {"cpu_limit": 6.0, "memory_mb": 6144},
    }
    (directory / "config.json").write_text(json.dumps(profile), encoding="utf-8")

    migrated, changed = profile_service.migrate_profile(profile, directory)

    assert changed is True
    assert migrated["schema_version"] == 4
    assert migrated["requested_environment"]["user_agent_policy"] == "browser_default"
    assert migrated["requested_environment"]["user_agent"] is None
    assert migrated["fingerprint"]["user_agent"] == ""
    assert migrated["resources"] == {"cpu_limit": 6.0, "memory_mb": 6144}
    assert migrated["restart_required"] is True
    assert (directory / "config.json.v3.bak").exists()


def test_observation_comparison_does_not_invent_missing_values():
    comparison = profile_service.environment_differences(
        {"screen_resolution": "1920x1080", "timezone": "UTC", "language": "en-US"},
        {"screen_resolution": "1920x1080", "timezone": "Europe/London"},
    )
    assert comparison == {
        "screen_resolution": "matched",
        "timezone": "different",
        "language": "not_measured",
    }


def test_direct_creation_never_auto_assigns_proxy(tmp_path, monkeypatch):
    released = []
    monkeypatch.setattr(profile_router, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(
        profile_router, "allocate_profile_identity", lambda: ("profile_001", 5901, 6081)
    )
    monkeypatch.setattr(profile_router, "release_proxy", lambda profile_id: released.append(profile_id))

    profile = profile_router.create_profile(
        ProfileCreateRequest(
            name="Direct Profile",
            network=NetworkIntent(mode="direct"),
            requested_environment=RequestedEnvironmentInput(
                timezone_policy="manual", timezone="UTC"
            ),
        )
    )

    assert released == ["profile_001"]
    assert profile["network"]["mode"] == "direct"
    assert profile["network"]["proxy_id"] is None
    assert profile["network"]["proxy_host"] == ""
    assert profile["network"]["proxy_port"] is None


def test_proxy_entrypoint_is_fail_closed_for_both_ip_families():
    root = Path(__file__).resolve().parents[2]
    entrypoint = (root / "container" / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'network_fail "/dev/net/tun is unavailable' in entrypoint
    assert "unsafe browser-only proxy fallback is disabled" in entrypoint
    assert "iptables -P OUTPUT DROP" in entrypoint
    assert "ip6tables -P OUTPUT DROP" in entrypoint
    assert "ip -6 route del default" in entrypoint
    assert "ip route show default dev tun0 | grep -q '^default'" in entrypoint
    assert "touch /run/network-preflight.ok" in entrypoint
    assert "using Chrome-level proxy flags" not in entrypoint


def test_browser_uses_its_native_user_agent_and_client_hints():
    root = Path(__file__).resolve().parents[2]
    entrypoint = (root / "container" / "entrypoint.sh").read_text(encoding="utf-8")
    launcher = (root / "scripts" / "run_profile.sh").read_text(encoding="utf-8")

    assert "--user-agent" not in entrypoint
    assert "UserAgentClientHint" not in entrypoint
    assert "USER_AGENT=" not in launcher


def test_browser_launcher_supports_linux_and_wsl_gpu_devices():
    root = Path(__file__).resolve().parents[2]
    launcher = (root / "scripts" / "run_profile.sh").read_text(encoding="utf-8")
    entrypoint = (root / "container" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "--device /dev/dri:/dev/dri" in launcher
    assert "--device /dev/dxg:/dev/dxg" in launcher
    assert "/usr/lib/wsl:/usr/lib/wsl:ro" in launcher
    assert "GPU_DEVICE_BACKEND" in launcher
    assert "/run/rendering-status.json" in entrypoint
    assert "GALLIUM_DRIVER=d3d12" in entrypoint
    assert "--use-gl=angle --use-angle=gl-egl" in entrypoint


def test_proxy_pool_removes_fabricated_legacy_location(tmp_path, monkeypatch):
    pool_path = tmp_path / "proxy_pool.json"
    pool_path.write_text(json.dumps([{
        "id": "proxy_1", "host": "203.0.113.1", "port": 1080,
        "timezone": "America/Los_Angeles", "country": "United States",
        "country_code": "US", "region": "California", "city": "Los Angeles",
        "geo_source": "unknown",
    }]), encoding="utf-8")
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool_path)

    proxy = proxy_service.load_proxy_pool()[0]

    assert proxy["geo_source"] == "unknown"
    assert proxy["timezone"] is None
    assert proxy["city"] is None
    assert proxy["country"] is None


def test_new_proxy_import_does_not_invent_location(tmp_path, monkeypatch):
    pool_path = tmp_path / "proxy_pool.json"
    pool_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool_path)

    proxy_service.import_proxies_from_text("203.0.113.2:1080:user:password")
    proxy = proxy_service.load_proxy_pool()[0]

    assert proxy["geo_source"] == "unknown"
    assert proxy["timezone"] is None
    assert proxy["city"] is None


def test_proxy_geo_lookup_uses_stdin_config_and_parses_exit(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({
            "success": True,
            "ip": "45.58.228.187",
            "country": "United States",
            "country_code": "US",
            "region": "California",
            "city": "San Jose",
            "latitude": 37.33,
            "longitude": -121.89,
            "timezone": {"id": "America/Los_Angeles"},
        }), stderr="")

    monkeypatch.setattr(proxy_service.subprocess, "run", fake_run)
    geo = proxy_service.lookup_proxy_geography({
        "host": "45.58.228.187", "port": 5859,
        "username": "proxy-user", "password": "proxy-password",
    })

    assert captured["args"] == ["curl", "--config", "-"]
    assert "proxy-user:proxy-password" in captured["input"]
    assert "proxy-user" not in " ".join(captured["args"])
    assert geo["city"] == "San Jose"
    assert geo["timezone"] == "America/Los_Angeles"
    assert geo["geo_source"] == "proxy_exit_lookup"


def test_refresh_proxy_statuses_persists_latency_and_geography(tmp_path, monkeypatch):
    pool_path = tmp_path / "proxy_pool.json"
    pool_path.write_text(json.dumps([{
        "id": "proxy_status", "host": "45.58.228.187", "port": 5859,
        "username": "user", "password": "password", "geo_source": "unknown",
    }]), encoding="utf-8")
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool_path)
    monkeypatch.setattr(proxy_service, "test_proxy_ping", lambda *_args, **_kwargs: {
        "success": True, "latency_ms": 42,
    })
    monkeypatch.setattr(proxy_service, "lookup_proxy_geography", lambda _proxy: {
        "exit_ip": "45.58.228.187", "city": "San Jose", "country": "United States",
        "country_code": "US", "region": "California", "timezone": "America/Los_Angeles",
        "geo_source": "proxy_exit_lookup", "geo_checked_at": "2026-09-27T00:00:00+00:00",
        "geo_last_error": None,
    })

    result = proxy_service.refresh_proxy_statuses(["proxy_status"])
    stored = proxy_service.load_proxy_pool()[0]

    assert result == {"total": 1, "online": 1, "offline": 0, "geo_checked": 1, "geo_failed": 0}
    assert stored["reachable"] is True
    assert stored["latency_ms"] == 42
    assert stored["city"] == "San Jose"
    assert stored["last_checked"]


def test_profile_action_error_redacts_proxy_credentials(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / "profile_001"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.json").write_text(json.dumps({
        "network": {"proxy_user": "private-user", "proxy_pass": "private-password"},
    }), encoding="utf-8")
    monkeypatch.setattr(docker_service, "PROFILES_DIR", profiles_root)

    sanitized = docker_service.sanitize_profile_action_error(
        "profile_001",
        "failed socks5://private-user:private-password@proxy.example:1080 private-user private-password",
    )

    assert "private-user" not in sanitized
    assert "private-password" not in sanitized
    assert "socks5://[REDACTED]@proxy.example:1080" in sanitized
