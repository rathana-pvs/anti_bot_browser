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


def test_one_proxy_serves_one_browser_and_one_phone_account(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")
    p1 = pool()["p1"]
    assert (p1["profile_id"], p1["assigned"], p1["phone_profile_id"]) == ("profile_001", True, "profile_002")
    assert p1["group"] == "Used"


def test_no_two_accounts_of_the_same_kind_share_a_proxy(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")
    with pytest.raises(ValueError, match="another browser account"):
        proxy_service.assign_proxy_by_id("profile_003", "p1")
    with pytest.raises(ValueError, match="another phone account"):
        proxy_service.assign_proxy_by_id("profile_004", "p1", kind="phone")
    # Re-assigning the same account is not a conflict.
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")


def test_phone_only_proxy_is_still_free_for_a_browser_account(pool):
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")
    p1 = pool()["p1"]
    assert p1["assigned"] is False and p1["profile_id"] is None
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    assert pool()["p1"]["profile_id"] == "profile_001"


def test_moving_a_phone_account_frees_only_its_phone_slot(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")
    proxy_service.assign_proxy_by_id("profile_002", "p2", kind="phone")
    current = pool()
    assert current["p1"]["phone_profile_id"] is None and current["p1"]["profile_id"] == "profile_001"
    assert current["p2"]["phone_profile_id"] == "profile_002"


def test_release_clears_whichever_slot_the_account_holds(pool):
    proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")
    assert proxy_service.release_proxy("profile_002") is True
    p1 = pool()["p1"]
    assert p1["phone_profile_id"] is None and p1["profile_id"] == "profile_001"
    assert proxy_service.release_proxy("profile_001") is True
    p1 = pool()["p1"]
    assert p1["assigned"] is False and p1["group"] != "Used"


def test_legacy_assigned_flag_still_reserves_the_browser_slot(pool, tmp_path):
    proxy_service.PROXY_POOL_FILE.write_text(json.dumps([_proxy("p1", assigned=True)]))
    with pytest.raises(ValueError, match="another browser account"):
        proxy_service.assign_proxy_by_id("profile_001", "p1")
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")


def test_proxy_used_only_by_a_phone_cannot_be_deleted(pool):
    proxy_service.assign_proxy_by_id("profile_002", "p1", kind="phone")
    result = proxy_service.delete_proxies(["p1", "p2"])
    assert result["blocked_ids"] == ["p1"] and result["deleted_ids"] == ["p2"]


def test_unknown_kind_is_rejected(pool):
    with pytest.raises(ValueError, match="kind"):
        proxy_service.assign_proxy_by_id("profile_001", "p1", kind="tablet")
