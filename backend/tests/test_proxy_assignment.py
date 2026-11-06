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


def test_one_proxy_serves_only_one_account_by_default(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    with pytest.raises(ValueError, match="limit per proxy"):
        proxy_service.assign_proxy_by_id("profile_002", "p1")
    # Re-assigning the same account is not a conflict.
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    assert pool()["p1"]["profile_ids"] == ["profile_001"]


def test_moving_an_account_frees_its_old_proxy(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_001", "p2")
    current = pool()
    assert current["p1"]["profile_id"] is None and current["p1"]["assigned"] is False
    assert current["p1"]["profile_ids"] == []
    assert current["p2"]["profile_ids"] == ["profile_001"]


def test_a_proxy_serves_accounts_up_to_the_limit(pool):
    proxy_service.set_account_limit(2)
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1")
    with pytest.raises(ValueError, match="already serves 2 accounts"):
        proxy_service.assign_proxy_by_id("profile_003", "p1")
    current = pool()["p1"]
    assert current["profile_ids"] == ["profile_001", "profile_002"]
    assert current["assigned"] is True and current["group"] == "Used"


def test_releasing_one_account_keeps_the_others(pool):
    proxy_service.set_account_limit(3)
    for pid in ("profile_001", "profile_002"):
        proxy_service.assign_proxy_by_id(pid, "p1")
    assert proxy_service.release_proxy("profile_001")
    assert pool()["p1"]["profile_ids"] == ["profile_002"]
    assert proxy_service.delete_proxies(["p1"])["blocked_ids"] == ["p1"]
    proxy_service.release_proxy("profile_002")
    assert pool()["p1"]["assigned"] is False and pool()["p1"]["group"] != "Used"


def test_lowering_the_limit_keeps_existing_accounts_but_admits_no_more(pool):
    proxy_service.set_account_limit(2)
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1")
    proxy_service.set_account_limit(1)
    assert pool()["p1"]["profile_ids"] == ["profile_001", "profile_002"]
    with pytest.raises(ValueError):
        proxy_service.assign_proxy_by_id("profile_003", "p1")


def test_auto_assignment_prefers_the_least_shared_proxy(pool):
    proxy_service.set_account_limit(2)
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    assert proxy_service.assign_proxy("profile_002")["id"] == "p2"
    assert proxy_service.assign_proxy("profile_003")["id"] in {"p1", "p2"}
    assert proxy_service.assign_proxy("profile_004") is not None
    assert proxy_service.assign_proxy("profile_005") is None


def test_a_pool_saved_before_sharing_keeps_its_account(pool, tmp_path):
    (tmp_path / "proxy_pool.json").write_text(json.dumps([_proxy("p1", assigned=True, profile_id="profile_009")]))
    assert proxy_service.load_proxy_pool()[0]["profile_ids"] == ["profile_009"]


@pytest.mark.parametrize("value", [0, 21, True, "2"])
def test_the_limit_must_be_a_whole_number_in_range(pool, value):
    with pytest.raises(ValueError):
        proxy_service.set_account_limit(value)
    assert proxy_service.account_limit() == 1
