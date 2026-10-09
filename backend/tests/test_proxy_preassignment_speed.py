from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import proxies


@pytest.fixture
def speed_client(monkeypatch):
    pool = [{"id": "proxy_1", "host": "proxy.example", "port": 8080,
             "type": "http", "username": "user", "password": "secret",
             "assigned": False, "profile_id": None}]
    original = deepcopy(pool)
    calls = []
    monkeypatch.setattr(proxies, "load_proxy_pool", lambda: pool)

    def forbid_save(*args, **kwargs):
        pytest.fail("Speed tests must not write proxy assignments")

    monkeypatch.setattr(proxies, "save_proxy_pool", forbid_save)
    monkeypatch.setattr(proxies, "test_proxy_ping", lambda host, port: {
        "success": True, "latency_ms": 123})

    def speed(network, direction):
        calls.append((network, direction))
        return {"success": direction == "download", "mbps": 12.5 if direction == "download" else None,
                "bytes": 5000000 if direction == "download" else 0, "provider": "Cloudflare",
                "error": None if direction == "download" else "Upload test timed out"}

    monkeypatch.setattr(proxies, "check_proxy_speed", speed)
    app = FastAPI()
    app.include_router(proxies.router)
    with TestClient(app) as client:
        yield client, calls
    assert pool == original


def test_pool_speed_before_assignment(speed_client):
    client, calls = speed_client
    response = client.post("/api/proxies/speed-test", json={"mode": "pool", "proxy_id": "proxy_1"})
    assert response.status_code == 200
    result = response.json()
    assert result["latency"]["latency_ms"] == 123
    assert result["download"]["mbps"] == 12.5
    assert result["upload"]["error"] == "Upload test timed out"
    assert result["last_checked"]
    assert "secret" not in response.text
    assert [direction for _, direction in calls] == ["download", "upload"]
    assert calls[0][0] == {"mode": "pool", "proxy_host": "proxy.example", "proxy_port": 8080,
                            "proxy_type": "http", "proxy_user": "user", "proxy_pass": "secret"}


def test_custom_speed_before_save(speed_client):
    client, calls = speed_client
    response = client.post("/api/proxies/speed-test", json={
        "mode": "custom", "host": "custom.example", "port": 1080,
        "username": "custom-user", "password": "custom-secret"})
    assert response.status_code == 200
    assert calls[0][0] == {"mode": "custom", "proxy_host": "custom.example", "proxy_port": 1080,
                            "proxy_type": "socks5", "proxy_user": "custom-user", "proxy_pass": "custom-secret"}


@pytest.mark.parametrize("payload,status", [
    ({"mode": "direct"}, 400),
    ({"mode": "pool", "proxy_id": "missing"}, 404),
    ({"mode": "custom", "host": "proxy.example", "port": 0}, 422),
    ({"mode": "custom", "host": "", "port": 1080}, 422),
])
def test_invalid_selection_does_not_measure(speed_client, payload, status):
    client, calls = speed_client
    assert client.post("/api/proxies/speed-test", json=payload).status_code == status
    assert not calls
