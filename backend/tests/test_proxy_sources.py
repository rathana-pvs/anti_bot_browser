import json
import stat

import pytest

from backend.services import proxy_change, proxy_service, proxy_sources, webshare_service


def _proxy(proxy_id, host, **extra):
    return {"id": proxy_id, "host": host, "port": 1080, "username": "u", "password": "pw", "type": "socks5",
            "geo_source": "unknown", **extra}


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / "proxy_pool.json"
    path.write_text(json.dumps([_proxy("p1", "10.0.0.1"), _proxy("p2", "10.0.0.2")]))
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", path)
    monkeypatch.setattr(proxy_sources, "LEGACY_KEY_FILE", tmp_path / "webshare.json")
    monkeypatch.setattr(proxy_service, "refresh_proxy_statuses", lambda ids: {})
    return lambda: {p["id"]: p for p in json.loads(path.read_text())}


def test_existing_proxies_join_the_first_source(pool):
    assert {p["source_id"] for p in proxy_service.load_proxy_pool()} == {proxy_sources.DEFAULT_ID}
    assert {p["source_id"] for p in pool().values()} == {proxy_sources.DEFAULT_ID}  # saved on first load
    [source] = proxy_sources.list_public()
    assert source["id"] == "default" and source["provider"] == "manual" and source["proxy_count"] == 2


def test_a_webshare_key_saved_before_sources_becomes_the_first_sources_key(tmp_path, monkeypatch):
    (tmp_path / "proxy_pool.json").write_text("[]")
    (tmp_path / "webshare.json").write_text(json.dumps({"api_key": "legacykey9876"}))
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", tmp_path / "proxy_pool.json")
    monkeypatch.setattr(proxy_sources, "LEGACY_KEY_FILE", tmp_path / "webshare.json")
    [source] = proxy_sources.list_public()
    assert source["provider"] == "webshare" and source["key_hint"] == "…9876"
    assert proxy_sources.api_key("default") == "legacykey9876"
    assert stat.S_IMODE(proxy_sources._keys_file().stat().st_mode) == 0o600


def test_sources_keep_their_own_keys(pool):
    a = proxy_sources.create({"name": "Webshare residential", "provider": "webshare", "api_key": "residential1111"},
                             verify=lambda key: None)
    b = proxy_sources.create({"name": "Webshare datacenter", "provider": "webshare", "api_key": "datacenter2222"},
                             verify=lambda key: None)
    assert proxy_sources.api_key(a["id"]) == "residential1111"
    assert proxy_sources.api_key(b["id"]) == "datacenter2222"
    assert "residential1111" not in json.dumps(proxy_sources.list_public())
    # Leaving the key out keeps it; changing to manual drops it.
    proxy_sources.update(a["id"], {"name": "Residential US"})
    assert proxy_sources.api_key(a["id"]) == "residential1111"
    proxy_sources.update(a["id"], {"provider": "manual"})
    assert proxy_sources.api_key(a["id"]) is None


def test_source_names_are_unique_and_required(pool):
    proxy_sources.create({"name": "Manual list"})
    with pytest.raises(ValueError, match="already exists"):
        proxy_sources.create({"name": " manual  LIST "})
    with pytest.raises(ValueError, match="name"):
        proxy_sources.create({"name": "  "})


def test_an_http_source_is_for_pc_accounts_only(pool):
    with pytest.raises(ValueError, match="Phones only use SOCKS5"):
        proxy_sources.create({"name": "HTTP", "protocol": "http"})
    source = proxy_sources.create({"name": "HTTP", "protocol": "http", "use_on": "pc"})
    assert source["use_on"] == "pc"


def test_import_goes_into_the_chosen_source_with_its_protocol(pool):
    source = proxy_sources.create({"name": "HTTP", "protocol": "http", "use_on": "pc"})
    result = proxy_service.import_proxies_from_text("10.0.0.9:8080:u:p\n10.0.0.1:1080:u:pw", source["id"])
    assert result["added"] == 1 and result["skipped"] == 1
    added = pool()[result["added_ids"][0]]
    assert added["source_id"] == source["id"] and added["type"] == "http"
    with pytest.raises(ValueError, match="protocol can't change"):
        proxy_sources.update(source["id"], {"protocol": "socks5", "use_on": "both"})
    with pytest.raises(ValueError, match="no longer exists"):
        proxy_service.import_proxies_from_text("10.0.0.8:1080", "src_missing")


def test_a_source_with_its_own_limit_overrides_the_global_one(pool):
    source = proxy_sources.create({"name": "Shared", "accounts_per_proxy": 2})
    new_id = proxy_service.import_proxies_from_text("10.0.0.7:1080:u:p", source["id"])["added_ids"][0]
    proxy_service.assign_proxy_by_id("a1", new_id)
    proxy_service.assign_proxy_by_id("a2", new_id)
    with pytest.raises(ValueError, match="already serves 2 accounts"):
        proxy_service.assign_proxy_by_id("a3", new_id)
    # The first source still follows the global limit of 1.
    proxy_service.assign_proxy_by_id("a4", "p1")
    with pytest.raises(ValueError, match="limit per proxy"):
        proxy_service.assign_proxy_by_id("a5", "p1")


def test_a_pc_only_source_never_serves_a_phone_account(pool):
    source = proxy_sources.create({"name": "PC only", "use_on": "pc"})
    new_id = proxy_service.import_proxies_from_text("10.0.0.6:1080:u:p", source["id"])["added_ids"][0]
    with pytest.raises(ValueError, match="PC accounts only"):
        proxy_service.assign_proxy_by_id("phone1", new_id, surface="phone")
    proxy_service.assign_proxy_by_id("pc1", new_id, surface="pc")
    picked = proxy_service.assign_proxy("phone2", surface="phone")
    assert picked["source_id"] == proxy_sources.DEFAULT_ID


def test_assign_can_keep_to_one_source(pool):
    source = proxy_sources.create({"name": "Second"})
    new_id = proxy_service.import_proxies_from_text("10.0.0.5:1080:u:p", source["id"])["added_ids"][0]
    assert proxy_service.assign_proxy("a1", source_id=source["id"])["id"] == new_id
    assert proxy_service.assign_proxy("a2", source_id=source["id"]) is None


def test_a_source_with_proxies_cannot_be_deleted(pool):
    source = proxy_sources.create({"name": "Second"})
    proxy_service.import_proxies_from_text("10.0.0.5:1080:u:p", source["id"])
    with pytest.raises(ValueError, match="still has 1 proxy"):
        proxy_sources.delete(source["id"])
    with pytest.raises(ValueError, match="can't be deleted"):
        proxy_sources.delete(proxy_sources.DEFAULT_ID)
    empty = proxy_sources.create({"name": "Empty", "provider": "webshare", "api_key": "emptykey1234"}, verify=lambda k: None)
    proxy_sources.delete(empty["id"])
    assert not proxy_sources.exists(empty["id"]) and proxy_sources.api_key(empty["id"]) is None


def test_replace_uses_the_proxys_own_source_key_and_keeps_the_new_proxy_there(pool, monkeypatch):
    source = proxy_sources.create({"name": "Residential", "provider": "webshare", "api_key": "residential1111"},
                                  verify=lambda key: None)
    old_id = proxy_service.import_proxies_from_text("1.1.1.1:6000:u:pw", source["id"])["added_ids"][0]
    keys = set()

    def fake(method, path, key=None, **kwargs):
        keys.add(key)
        if path == "/v2/proxy/list/":
            ip = kwargs["params"].get("proxy_address")
            rows = {"1.1.1.1": {"proxy_address": "1.1.1.1", "port": 6000, "username": "u", "password": "pw", "country_code": "US"},
                    "2.2.2.2": {"proxy_address": "2.2.2.2", "port": 7000, "username": "u", "password": "pw2", "country_code": "US"}}
            return {"results": [rows[ip]] if ip in rows else []}
        if path == "/v3/proxy/replace/":
            return {"id": 42}
        if path == "/v3/proxy/replace/42/":
            return {"state": "completed"}
        if path == "/v2/proxy/list/replaced/":
            return {"results": [{"proxy": "1.1.1.1", "proxy_port": 6000, "replaced_with": "2.2.2.2", "replaced_with_port": 7000}]}
        raise AssertionError(path)

    monkeypatch.setattr(webshare_service, "_request", fake)
    result = webshare_service.replace_one(old_id, sleep=lambda s: None)
    assert keys == {"residential1111"}
    assert pool()[result["new_id"]]["source_id"] == source["id"]


def test_a_manual_sources_proxy_cannot_be_replaced(pool):
    with pytest.raises(webshare_service.WebshareError, match="isn't a Webshare source"):
        webshare_service.ReplaceRun().start(["p1"])


def test_change_proxy_lists_the_accounts_own_source_first(pool, monkeypatch):
    source = proxy_sources.create({"name": "Second"})
    other = proxy_service.import_proxies_from_text("10.0.0.5:1080:u:p", source["id"])["added_ids"][0]
    proxy_service.assign_proxy_by_id("acct", "p1")
    monkeypatch.setattr(proxy_change, "_config", lambda pid: {"network": {"mode": "pool", "proxy_id": "p1"}})
    monkeypatch.setattr(proxy_change, "running_reason", lambda pid, config: None)
    monkeypatch.setattr(proxy_change.account_activity, "last_active", lambda *a: None)
    options = proxy_change.options("acct")["options"]
    assert [o["id"] for o in options] == ["p2", other]
    assert options[0]["same_source"] is True and options[0]["source_name"] == "My proxies"


def _webshare_source(name="Webshare DC"):
    return proxy_sources.create({"name": name, "provider": "webshare", "api_key": "datacenter2222"},
                                verify=lambda key: None)


def test_sync_adds_the_webshare_accounts_new_proxies_to_the_source(pool, monkeypatch):
    source = _webshare_source()
    pages = {1: {"results": [{"proxy_address": "10.0.0.1", "port": 1080, "username": "u", "password": "pw", "valid": True},
                             {"proxy_address": "10.0.0.9", "port": 6000, "username": "w", "password": "s", "valid": True}],
                 "next": "page2"},
             2: {"results": [{"proxy_address": "10.0.0.8", "port": 6001, "username": "w", "password": "s", "valid": False}],
                 "next": None}}
    seen = []

    def fake_request(method, path, key=None, **kwargs):
        seen.append((path, key))
        return pages[kwargs["params"]["page"]]

    monkeypatch.setattr(webshare_service, "_request", fake_request)
    result = webshare_service.sync_source(source["id"])
    assert seen == [("/v2/proxy/list/", "datacenter2222")] * 2
    # 10.0.0.1 is already in the pool (in another source) and stays there; the invalid one is left out.
    assert (result["listed"], result["added"], result["skipped"]) == (2, 1, 1)
    [new] = [p for p in pool().values() if p["host"] == "10.0.0.9"]
    assert new["source_id"] == source["id"] and new["port"] == 6000 and new["username"] == "w"
    assert pool()["p1"]["source_id"] == proxy_sources.DEFAULT_ID


def test_sync_counts_but_keeps_proxies_webshare_no_longer_lists(pool, monkeypatch):
    source = _webshare_source()
    monkeypatch.setattr(webshare_service, "_request", lambda *a, **k: {
        "results": [{"proxy_address": "10.0.0.9", "port": 6000, "username": "w", "password": "s"}], "next": None})
    webshare_service.sync_source(source["id"])
    monkeypatch.setattr(webshare_service, "_request", lambda *a, **k: {"results": [], "next": None})
    result = webshare_service.sync_source(source["id"])
    assert result["not_in_webshare"] == 1 and any(p["host"] == "10.0.0.9" for p in pool().values())


def test_sync_needs_a_webshare_source_with_a_key(pool):
    manual = proxy_sources.create({"name": "Manual list"})
    with pytest.raises(webshare_service.WebshareError, match="isn't a Webshare source"):
        webshare_service.sync_source(manual["id"])
    keyless = proxy_sources.create({"name": "Webshare later", "provider": "webshare"})
    with pytest.raises(webshare_service.WebshareError, match="API key"):
        webshare_service.sync_source(keyless["id"])


def test_a_source_with_an_api_key_refuses_pasted_proxies(pool, monkeypatch):
    from fastapi import HTTPException
    from backend.routers import proxies as proxies_router
    monkeypatch.setattr(proxies_router, "refresh_proxy_statuses",
                        lambda ids: {"online": 0, "offline": 0, "geo_checked": 0, "geo_failed": 0})
    source = _webshare_source()
    assert proxy_sources.public(proxy_sources.get(source["id"]))["uses_api"] is True
    with pytest.raises(HTTPException) as refused:
        proxies_router.import_proxies({"text": "10.0.0.7:1080", "source_id": source["id"]})
    assert refused.value.status_code == 409 and "API" in refused.value.detail
    assert not any(p["host"] == "10.0.0.7" for p in pool().values())
    # A manual source and a Webshare source without a key still take pasted proxies.
    keyless = proxy_sources.create({"name": "Webshare later", "provider": "webshare"})
    imported = proxies_router.import_proxies({"text": "10.0.0.7:1080", "source_id": keyless["id"]})
    assert imported["added"] == 1


def _listing(*hosts):
    return lambda *a, **k: {"results": [{"proxy_address": h, "port": 1080, "username": "new", "password": "new"}
                                        for h in hosts], "next": None}


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from backend import config
    profiles = tmp_path / "profiles"
    monkeypatch.setattr(config, "PROFILES_DIR", profiles)

    def make(profile_id, phone=False):
        (profiles / profile_id).mkdir(parents=True)
        runtime = {"kind": "android_user"} if phone else {"kind": "docker"}
        (profiles / profile_id / "config.json").write_text(json.dumps({"id": profile_id, "runtime": runtime}))
    return make


def test_sync_reports_proxies_in_a_pasted_source_and_moving_changes_only_their_source(pool, monkeypatch, accounts):
    accounts("profile_001")
    data = pool()
    data["p1"].update(profile_ids=["profile_001"], profile_id="profile_001", assigned=True, latency_ms=80)
    proxy_service.save_proxy_pool(list(data.values()))
    before = pool()["p1"]
    source = _webshare_source()
    monkeypatch.setattr(webshare_service, "_request", _listing("10.0.0.1", "10.0.0.2"))
    result = webshare_service.sync_source(source["id"])
    assert result["added"] == 0
    assert [(item["id"], item["from_source"], item["accounts"]) for item in result["in_other_sources"]] == [
        ("p1", "My proxies", 1), ("p2", "My proxies", 0)]
    assert pool()["p1"]["source_id"] == proxy_sources.DEFAULT_ID  # the sync itself moves nothing

    moved = webshare_service.move_into_source(source["id"], ["p1"])
    assert moved == {"moved": 1, "kept": [], "not_movable": 0}
    after = pool()["p1"]
    assert after["source_id"] == source["id"]
    # Everything else stays: the account keeps the proxy, the login is not taken from Webshare.
    assert {k: v for k, v in after.items() if k != "source_id"} == {k: v for k, v in before.items() if k != "source_id"}
    assert pool()["p2"]["source_id"] == proxy_sources.DEFAULT_ID  # not chosen, not moved


def test_proxies_another_api_source_lists_are_never_moved(pool, monkeypatch):
    first = proxy_sources.create({"name": "Webshare A", "provider": "webshare", "api_key": "sameaccount1"},
                                 verify=lambda key: None)
    monkeypatch.setattr(webshare_service, "_request", _listing("10.0.0.9"))
    webshare_service.sync_source(first["id"])
    second = proxy_sources.create({"name": "Webshare B", "provider": "webshare", "api_key": "sameaccount2"},
                                  verify=lambda key: None)
    result = webshare_service.sync_source(second["id"])
    [kept] = result["kept_elsewhere"]
    assert result["in_other_sources"] == [] and "Webshare A gets it from its own API" in kept["reason"]
    moved = webshare_service.move_into_source(second["id"], [kept["id"]])
    assert moved["moved"] == 0 and moved["kept"][0]["id"] == kept["id"]
    assert pool()[kept["id"]]["source_id"] == first["id"]


def test_a_proxy_stays_when_its_account_or_protocol_does_not_fit_the_new_source(pool, monkeypatch, accounts):
    accounts("profile_001", phone=True)
    data = pool()
    data["p1"].update(profile_ids=["profile_001"])
    data["p2"]["type"] = "http"
    proxy_service.save_proxy_pool(list(data.values()))
    pc_only = proxy_sources.create({"name": "Webshare PC", "provider": "webshare", "api_key": "pconly3333",
                                    "use_on": "pc"}, verify=lambda key: None)
    monkeypatch.setattr(webshare_service, "_request", _listing("10.0.0.1", "10.0.0.2"))
    result = webshare_service.sync_source(pc_only["id"])
    reasons = {item["id"]: item["reason"] for item in result["kept_elsewhere"]}
    assert "a phone account uses it" in reasons["p1"] and "HTTP" in reasons["p2"]
    assert webshare_service.move_into_source(pc_only["id"], ["p1", "p2"])["moved"] == 0
    assert {p["source_id"] for p in pool().values()} == {proxy_sources.DEFAULT_ID}
