import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.services import proxy_service

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    original_pool = proxy_service.PROXY_POOL_FILE
    proxy_service.PROXY_POOL_FILE = tmp_path_factory.mktemp("proxy-api") / "proxy_pool.json"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        proxy_service.PROXY_POOL_FILE = original_pool

def test_settings_resource_mode(client):
    res = client.get("/api/settings/resource-mode")
    assert res.status_code == 200
    data = res.json()
    assert "selected_mode" in data
    assert "hardware" in data
    assert "limits" in data

    # Test PUT
    put_res = client.put("/api/settings/resource-mode", json={"mode": "low"})
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["selected_mode"] == "low"

    # Reset to auto
    client.put("/api/settings/resource-mode", json={"mode": "auto"})

def test_system_stats(client):
    res = client.get("/api/system/stats")
    assert res.status_code == 200
    data = res.json()
    assert "docker_running" in data
    assert "cpu_cores" in data
    assert "total_memory_mb" in data
    assert "windows_host" in data
    assert data["runtime_environment"] in {"wsl", "linux"}
    assert "runtime" in data
    assert "wsl_runtime" in data
    assert data["wsl_runtime"]["cpu_threads"] == data["cpu_cores"]

def test_profiles_list(client):
    res = client.get("/api/profiles")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_proxies_list_and_import(client):
    res = client.get("/api/proxies")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # Test import format
    import_res = client.post("/api/proxies/import", json={"text": "127.0.0.1:9999:user:pass"})
    assert import_res.status_code == 200
    data = import_res.json()
    assert "added" in data

def test_queue_endpoints(client):
    res = client.get("/api/queue")
    assert res.status_code == 200
    data = res.json()
    assert "queue_version" in data
    assert "stats" in data
    assert "batches" in data

    # Test creating batch with start_now=True
    batch_payload = {
        "name": "Test Instant Batch",
        "target_profiles": ["profile_001"],
        "start_now": True,
        "schedule_window": {
            "start_time": "00:00",
            "end_time": "23:59",
            "profile_stagger_seconds": 0,
            "session_preparation_mode": "brief",
            "start_now": True,
        },
        "posts": [
            {
                "type": "reel",
                "media_file": "test_video.mp4",
                "base_caption": "Test caption",
                "first_comment": "https://example.com",
                "ai_spin": False,
            }
        ],
    }
    batch_res = client.post("/api/queue/batch", json=batch_payload)
    assert batch_res.status_code == 200
    batch_data = batch_res.json()
    assert batch_data["success"] is True
    assert "batch_id" in batch_data

    # Clean up test batch
    del_res = client.delete(f"/api/queue/batch/{batch_data['batch_id']}")
    assert del_res.status_code == 200

def test_ai_spin_caption(client):
    res = client.post("/api/ai/spin-caption", json={"base_caption": "Testing automation pipeline", "count": 2})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["variations"]) == 2

def test_media_list(client):
    res = client.get("/api/media/list")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_brains_list(client):
    res = client.get("/api/brains")
    assert res.status_code == 200
    data = res.json()
    assert "brains" in data or isinstance(data, dict)

def test_spa_dashboard(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "html" in res.headers.get("content-type", "").lower()

