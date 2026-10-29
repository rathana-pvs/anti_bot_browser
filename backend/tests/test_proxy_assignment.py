import json

import pytest

from backend.services import proxy_service


def _proxy(proxy_id, **extra):
    return {"id": proxy_id, "host": "10.0.0.1", "port": 1080, "username": "", "password": "",
            "type": "socks5", "assigned": False, "profile_id": None, "latency_ms": None, **extra}


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / "proxy_pool.json"
    path.write_text(json.dumps([_proxy("p1"), _proxy("p2")]))
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", path)
    return lambda: {p["id"]: p for p in json.loads(path.read_text())}


def test_one_proxy_serves_only_one_account(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    with pytest.raises(ValueError, match="already assigned"):
        proxy_service.assign_proxy_by_id("profile_002", "p1")
    # Re-assigning the same account is not a conflict.
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    assert pool()["p1"]["profile_id"] == "profile_001"


def test_moving_an_account_frees_its_old_proxy(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_001", "p2")
    current = pool()
    assert current["p1"]["profile_id"] is None and current["p1"]["assigned"] is False
    assert current["p2"]["profile_id"] == "profile_001"
