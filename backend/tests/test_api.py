import pytest
from pathlib import Path

RELEASE_VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()
from fastapi.testclient import TestClient
from backend.main import app
from backend.routers import settings as settings_router
from backend.services import docker_service, proxy_service, queue_service, resource_service

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    runtime_dir = tmp_path_factory.mktemp("api-runtime")
    original_pool = proxy_service.PROXY_POOL_FILE
    original_queue_file = queue_service.QUEUE_FILE
    original_claim_lock = queue_service.QUEUE_CLAIM_LOCK_FILE
    original_resource_service_manager = resource_service.settings_manager
    original_docker_service_manager = docker_service.settings_manager
    original_settings_router_manager = settings_router.settings_manager

    proxy_service.PROXY_POOL_FILE = runtime_dir / "proxies" / "proxy_pool.json"
    queue_service.QUEUE_FILE = runtime_dir / "data" / "posting_queue.json"
    queue_service.QUEUE_CLAIM_LOCK_FILE = runtime_dir / "data" / "posting_queue.claim.lock"
    isolated_settings = resource_service.ResourceSettingsManager(
        runtime_dir / "data" / "manager_settings.json"
    )
    resource_service.settings_manager = isolated_settings
    docker_service.settings_manager = isolated_settings
    settings_router.settings_manager = isolated_settings
    try:
        with TestClient(app) as c:
            yield c
    finally:
        proxy_service.PROXY_POOL_FILE = original_pool
        queue_service.QUEUE_FILE = original_queue_file
        queue_service.QUEUE_CLAIM_LOCK_FILE = original_claim_lock
        resource_service.settings_manager = original_resource_service_manager
        docker_service.settings_manager = original_docker_service_manager
        settings_router.settings_manager = original_settings_router_manager

def test_settings_resource_mode(client):
    res = client.get("/api/settings/resource-mode")
    assert res.status_code == 200
    data = res.json()
    assert "selected_mode" in data
    assert "hardware" in data
    assert "limits" in data

    # Test PUT
    put_res = client.put("/api/settings/resource-mode", json={"mode": "custom", "max_parallel": 2,
                                                          "max_posts": 1, "max_live": 1})
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["selected_mode"] == "custom"

    # Reset to auto
    client.put("/api/settings/resource-mode", json={"mode": "auto"})


def test_runtime_version(client):
    res = client.get("/api/system/runtime-version")
    assert res.status_code == 200
    assert res.json()["version"] == RELEASE_VERSION


def test_health_and_reliability_endpoints(client):
    health = client.get("/api/system/health")
    assert health.status_code == 200
    assert health.json()["status"] in {"ready", "degraded", "not_ready"}

    readiness = client.get("/api/system/ready")
    assert readiness.status_code == 200
    assert {"lifecycle", "data_directory", "queue", "scheduler"} <= set(readiness.json()["checks"])

    reliability = client.get("/api/system/reliability")
    assert reliability.status_code == 200
    assert reliability.json()["status"] in {"pass", "fail", "insufficient_data"}


def test_desktop_session_token_protects_api(client, monkeypatch):
    monkeypatch.setenv("MANAGER_API_TOKEN", "test-desktop-session")
    assert client.get("/api/profiles").status_code == 403
    authorized = client.get(
        "/api/profiles",
        headers={"X-Manager-Token": "test-desktop-session"},
    )
    assert authorized.status_code == 200
    assert client.get("/api/system/runtime-version").status_code == 403
    version = client.get(
        "/api/system/runtime-version",
        headers={"X-Manager-Token": "test-desktop-session"},
    )
    assert version.status_code == 200

def test_system_stats(client):
    res = client.get("/api/system/stats")
    assert res.status_code == 200
    data = res.json()
    assert data["build_version"] == RELEASE_VERSION
    assert "docker_running" in data
    assert "cpu_cores" in data
    assert "total_memory_mb" in data
    assert data["runtime_environment"] == "linux"
    assert data["runtime"]["cpu_threads"] == data["cpu_cores"]

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

def test_scheduled_batch_spreads_accounts_and_shows_when_each_starts(client):
    from datetime import datetime, timedelta, timezone
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    payload = {
        "name": "Spread batch", "target_profiles": ["profile_001"], "start_now": False,
        "schedule_window": {"start_time": "09:00", "end_time": "21:00", "profile_stagger_seconds": 30,
                            "batch_iteration_delay_seconds": 0, "session_preparation_mode": "off",
                            "start_now": False, "start_at": start.isoformat(), "spread_minutes": 30,
                            "follow_active_hours": False},
        "posts": [{"type": "photo", "media_file": "", "base_caption": "Spread", "ai_spin": False}],
    }
    res = client.post("/api/queue/batch", json=payload)
    assert res.status_code == 200, res.text
    batch = res.json()["batch"]
    assert batch["schedule_window"]["follow_active_hours"] is False and batch["schedule_window"]["spread_minutes"] == 30
    at = datetime.fromisoformat(batch["posts"][0]["executions"][0]["scheduled_at"])
    assert start <= at < start + timedelta(minutes=30)

    item = next(e for e in client.get("/api/queue").json()["executions"] if e["batch_id"] == batch["batch_id"])
    assert item["wait_reason"] == "scheduled"
    assert datetime.fromisoformat(item["expected_start_at"]) == at

    payload["schedule_window"]["start_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    past = client.post("/api/queue/batch", json=payload)
    assert past.status_code == 400 and "already passed" in past.json()["detail"]

    preview = client.post("/api/queue/schedule-preview", json={
        "profile_ids": ["profile_001", "missing"], "schedule_window": {"start_at": start.isoformat(), "spread_minutes": 30}})
    assert preview.status_code == 200, preview.text
    assert [a["profile_id"] for a in preview.json()["accounts"]] in (["profile_001"], [])


def test_follow_active_hours_batch_is_planned_without_a_time(client):
    window = {"timing": "auto", "batch_iteration_delay_seconds": 0, "session_preparation_mode": "off",
              "start_time": "09:00", "end_time": "21:00", "profile_stagger_seconds": 30}
    preview = client.post("/api/queue/schedule-preview", json={
        "profile_ids": ["profile_001"], "posts_per_account": 2, "schedule_window": window})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["timing"] == "auto" and body["plan"]["max_posts_per_day"] == 5
    res = client.post("/api/queue/batch", json={
        "name": "Planned", "target_profiles": ["profile_001"],
        "schedule_window": {**window, "plan_seed": body["plan_seed"]},
        "posts": [{"type": "photo", "media_file": "", "base_caption": f"Planned {i}", "ai_spin": False} for i in range(2)],
    })
    assert res.status_code == 200, res.text
    batch = res.json()["batch"]
    assert batch["schedule_window"]["timing"] == "auto"
    times = sorted(e["scheduled_at"] for post in batch["posts"] for e in post["executions"])
    from datetime import datetime
    first, second = (datetime.fromisoformat(t) for t in times)
    assert (second - first).total_seconds() >= 2 * 3600
    accounts = {a["profile_id"]: a for a in body["accounts"]}
    if "profile_001" in accounts and accounts["profile_001"]["planned_at"]:
        assert [datetime.fromisoformat(t) for t in accounts["profile_001"]["planned_at"]] == [first, second]

    settings = client.put("/api/settings/posting-plan", json={"max_posts_per_day": 0})
    assert settings.status_code == 400
    assert client.put("/api/settings/posting-plan", json={"max_posts_per_day": 4}).json()["max_posts_per_day"] == 4
    client.put("/api/settings/posting-plan", json={"max_posts_per_day": 5})


def test_api_edit_reports_jobs_that_started_while_editing(client):
    from datetime import datetime, timedelta, timezone
    start = datetime.now(timezone.utc) + timedelta(hours=3)
    window = {"timing": "exact", "start_at": start.isoformat(), "spread_minutes": 0,
              "batch_iteration_delay_seconds": 0, "session_preparation_mode": "off"}
    created = client.post("/api/queue/batch", json={
        "name": "Edit me", "target_profiles": ["profile_001"], "schedule_window": window,
        "posts": [{"type": "photo", "media_file": "", "base_caption": "Hello", "ai_spin": False}]})
    assert created.status_code == 200, created.text
    batch_id = created.json()["batch_id"]
    info = client.get(f"/api/queue/batch/{batch_id}/edit").json()
    assert info["changeable"] == info["total"] == 1
    post_id = info["batch"]["posts"][0]["post_id"]

    from backend.services.queue_service import load_posting_queue, save_posting_queue
    queue = load_posting_queue()
    for batch in queue["daily_batches"]:
        if batch["batch_id"] == batch_id:
            batch["posts"][0]["executions"][0]["status"] = "running"
    save_posting_queue(queue)

    res = client.put(f"/api/queue/batch/{batch_id}", json={
        "name": "Edited", "posts": [{"post_id": post_id, "base_caption": "Changed"}],
        "expected_ids": info["changeable_ids"]})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["jobs_changed"] == 0 and [s["execution_id"] for s in body["started_while_editing"]] == info["changeable_ids"]
    assert body["batch"]["name"] == "Edited" and body["batch"]["posts"][0]["base_caption"] == "Hello"

    assert client.post(f"/api/queue/batch/{batch_id}/pause").json()["paused"] is True
    assert client.post(f"/api/queue/batch/{batch_id}/resume").json()["paused"] is False
    past = client.put(f"/api/queue/batch/{batch_id}", json={
        "schedule_window": {"timing": "exact", "start_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}})
    assert past.status_code == 400
    assert client.put("/api/queue/batch/missing", json={"name": "x"}).status_code == 404
    queue = load_posting_queue()
    for batch in queue["daily_batches"]:
        if batch["batch_id"] == batch_id:
            batch["posts"][0]["executions"][0]["status"] = "stopped"
    save_posting_queue(queue)
    assert client.delete(f"/api/queue/batch/{batch_id}").status_code == 200


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
            "batch_iteration_delay_seconds": 45,
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
    assert batch_data["batch"]["schedule_window"]["batch_iteration_delay_seconds"] == 45

    append_res = client.post(
        f"/api/queue/batch/{batch_data['batch_id']}/posts",
        json={"posts": [{
            "type": "photo",
            "media_file": "",
            "base_caption": "Appended while running",
            "ai_spin": False,
        }]},
    )
    assert append_res.status_code == 200
    assert append_res.json()["posts_added"] == 1
    assert append_res.json()["executions_added"] == 1

    updated_queue = client.get("/api/queue").json()
    updated_batch = next(batch for batch in updated_queue["batches"] if batch["batch_id"] == batch_data["batch_id"])
    assert len(updated_batch["posts"]) == 2
    assert updated_batch["posts"][-1]["executions"][0]["stage_history"][0]["reason"] == "appended_to_active_batch"

    # Reset status to stopped so test cleanup delete succeeds
    from backend.services.queue_service import save_posting_queue, load_posting_queue
    q = load_posting_queue()
    for b in q.get("daily_batches", []):
        if b.get("batch_id") == batch_data["batch_id"]:
            for post in b.get("posts", []):
                for ex in post.get("executions", []):
                    ex["status"] = "stopped"
                    ex["stage"] = "stopped"
    save_posting_queue(q)

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

