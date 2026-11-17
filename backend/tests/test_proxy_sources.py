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
