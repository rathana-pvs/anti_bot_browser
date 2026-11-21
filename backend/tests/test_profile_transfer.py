import json
import zipfile

import pytest

from backend.services import profile_service, profile_transfer, proxy_service
from backend.services.profile_transfer import TransferError


def _config(profile_id="profile_003", network=None):
    return {
        "schema_version": profile_service.PROFILE_SCHEMA_VERSION, "configuration_revision": 4,
        "id": profile_id, "name": "Food page", "group": "Cambodia", "status": "stopped",
        "network": network or {"mode": "pool", "proxy_id": "proxy_old", "proxy_type": "socks5",
                               "proxy_host": "1.2.3.4", "proxy_port": 1080, "proxy_user": "u", "proxy_pass": "p"},
        "requested_environment": {"timezone": "Asia/Phnom_Penh"}, "fingerprint": {"timezone": "Asia/Phnom_Penh"},
        "effective_environment": {"x": 1}, "resources": {"cpu_limit": 999, "memory_mb": 999_999_999},
        "behavior_seed": 42,
        "container": {"id": "abc", "vnc_port": 5903, "ws_port": 6083, "volume_path": f"profiles/{profile_id}/chrome_data"},
        "account": {"warming_week": 3},
    }


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """One PC: its profiles folder and proxy pool."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(profile_transfer, "PROFILES_DIR", profiles)
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", tmp_path / "pool.json")
    monkeypatch.setattr(proxy_service, "account_limit", lambda: 1)
    monkeypatch.setattr(profile_transfer, "allocate_profile_identity",
                        lambda: profile_service.allocate_profile_identity(profiles))
    return profiles


def _make_account(profiles, config):
    directory = profiles / config["id"]
    chrome = directory / "chrome_data"
    (chrome / "Default" / "Cache" / "Cache_Data").mkdir(parents=True)
    (chrome / "Default" / "Cache" / "Cache_Data" / "big").write_bytes(b"x" * 1000)
    (chrome / "Default" / "Cookies").write_bytes(b"session-cookie")
    (chrome / "Local State").write_text("{}")
    (chrome / "SingletonLock").symlink_to("nowhere")
    (directory / "automation_evidence").mkdir()
    (directory / "automation_evidence" / "shot.png").write_bytes(b"png")
    (directory / "facebook_page.json").write_text('{"name": "Page"}')
    (directory / "config.json").write_text(json.dumps(config))
    return directory


def _move(pc, tmp_path, password=None, import_password=None):
    _make_account(pc, _config())
    path, name = profile_transfer.build_export("profile_003", tmp_path / "work", password)
    assert name.endswith(".abprofile")
    # Arrive on a "new PC": empty profiles and pool.
    for child in pc.iterdir():
        import shutil
        shutil.rmtree(child)
    proxy_service.save_proxy_pool([])
    plain = profile_transfer.open_package(path, tmp_path / "work", import_password)
    return profile_transfer.import_package(plain)


def test_export_skips_caches_evidence_and_links(pc, tmp_path):
    _make_account(pc, _config())
    path, _ = profile_transfer.build_export("profile_003", tmp_path / "work")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert "chrome_data/Default/Cookies" in names and "facebook_page.json" in names
    assert not any("Cache" in n or "Singleton" in n or "automation_evidence" in n for n in names)
    assert manifest["proxy"]["host"] == "1.2.3.4" and manifest["proxy"]["password"] == "p"


@pytest.mark.parametrize("password", [None, "correct horse"])
def test_round_trip_keeps_login_fingerprint_and_proxy(pc, tmp_path, password):
    config = _move(pc, tmp_path, password, password)
    assert config["id"] == "profile_001"
    directory = pc / "profile_001"
    assert (directory / "chrome_data" / "Default" / "Cookies").read_bytes() == b"session-cookie"
    assert json.loads((directory / "config.json").read_text())["behavior_seed"] == 42
    assert config["fingerprint"] == {"timezone": "Asia/Phnom_Penh"} and config["group"] == "Cambodia"
    assert config["container"]["id"] is None and config["container"]["volume_path"] == "profiles/profile_001/chrome_data"
    assert config["effective_environment"] is None
    assert config["resources"]["cpu_limit"] <= profile_transfer.CPU_THREADS
    pool = proxy_service.load_proxy_pool()
    assert len(pool) == 1 and pool[0]["profile_ids"] == ["profile_001"] and pool[0]["password"] == "p"
    assert config["network"]["proxy_id"] == pool[0]["id"]
    assert not (pc / ".transfer").exists()


def test_encrypted_file_needs_the_right_password(pc, tmp_path):
    _make_account(pc, _config())
    path, _ = profile_transfer.build_export("profile_003", tmp_path / "work", "correct horse")
    assert profile_transfer.is_encrypted(path)
    with pytest.raises(TransferError, match="password"):
        profile_transfer.open_package(path, tmp_path / "work", None)
    with pytest.raises(TransferError, match="Wrong password"):
        profile_transfer.open_package(path, tmp_path / "work", "wrong horse")


def test_cut_encrypted_file_is_refused(pc, tmp_path, monkeypatch):
    monkeypatch.setattr(profile_transfer, "CHUNK_BYTES", 64)
    _make_account(pc, _config())
    path, _ = profile_transfer.build_export("profile_003", tmp_path / "work", "correct horse")
    data = path.read_bytes()
    first_chunk_end = len(profile_transfer.ENCRYPTED_MAGIC) + 20 + 4 + 64 + 16
    path.write_bytes(data[:first_chunk_end])  # ends cleanly after a whole chunk
    with pytest.raises(TransferError, match="damaged|incomplete"):
        profile_transfer.open_package(path, tmp_path / "work", "correct horse")


def test_short_password_is_refused(pc, tmp_path):
    _make_account(pc, _config())
    with pytest.raises(TransferError, match="8 characters"):
        profile_transfer.build_export("profile_003", tmp_path / "work", "short")


def test_full_proxy_refuses_the_import_and_leaves_nothing(pc, tmp_path):
    _make_account(pc, _config())
    path, _ = profile_transfer.build_export("profile_003", tmp_path / "work")
    # The proxy already serves profile_003 here (the limit is 1), as on a PC importing its own export.
    proxy_service.save_proxy_pool([{"id": "proxy_old", "host": "1.2.3.4", "port": 1080, "username": "u",
                                    "password": "p", "profile_ids": ["profile_003"]}])
    with pytest.raises(ValueError, match="limit per proxy"):
        profile_transfer.import_package(path)
    assert sorted(p.name for p in pc.iterdir()) == ["profile_003"]
    assert proxy_service.load_proxy_pool()[0]["profile_ids"] == ["profile_003"]


def test_direct_account_needs_no_proxy(pc, tmp_path):
    _make_account(pc, _config(network={"mode": "direct", "proxy_id": None}))
    path, _ = profile_transfer.build_export("profile_003", tmp_path / "work")
    config = profile_transfer.import_package(path)
    assert config["network"]["mode"] == "direct" and proxy_service.load_proxy_pool() == []


def test_phone_accounts_are_not_exported(pc, tmp_path):
    config = _config()
    config["runtime"] = {"kind": "android", "device_serial": "x"}
    _make_account(pc, config)
    with pytest.raises(TransferError, match="Phone accounts"):
        profile_transfer.build_export("profile_003", tmp_path / "work")


@pytest.mark.parametrize("bad", ["../escape", "chrome_data/../../escape", "/abs", "other.txt"])
def test_unexpected_entries_are_refused(pc, tmp_path, bad):
    path = tmp_path / "evil.abprofile"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": profile_transfer.FORMAT, "format_version": 1}))
        zf.writestr("config.json", json.dumps(_config()))
        zf.writestr(bad, "x")
    with pytest.raises(TransferError, match="unexpected"):
        profile_transfer.import_package(path)
    assert not (tmp_path / "escape").exists() and list(pc.iterdir()) == []


def test_newer_app_export_is_refused(pc, tmp_path):
    path = tmp_path / "new.abprofile"
    config = _config()
    config["schema_version"] = profile_service.PROFILE_SCHEMA_VERSION + 1
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": profile_transfer.FORMAT, "format_version": 1}))
        zf.writestr("config.json", json.dumps(config))
    with pytest.raises(TransferError, match="newer version"):
        profile_transfer.import_package(path)


def test_routes_export_hold_and_import(pc, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import profiles
    from backend.services import container_lifecycle
    monkeypatch.setattr(profiles, "PROFILES_DIR", pc)
    monkeypatch.setattr(profiles, "EXPORT_WORK_DIR", tmp_path / "exports")
    monkeypatch.setattr(profiles, "IMPORT_WORK_DIR", tmp_path / "imports")
    monkeypatch.setattr(container_lifecycle, "OWNERS_FILE", tmp_path / "owners.json")
    monkeypatch.setattr(container_lifecycle, "LOCK_FILE", tmp_path / "owners.lock")
    status = {"value": "running"}
    monkeypatch.setattr(profiles, "get_container_status", lambda _pid: status["value"])
    app = FastAPI()
    app.include_router(profiles.router)
    client = TestClient(app)
    _make_account(pc, _config())

    assert client.post("/api/profiles/profile_003/export", json={}).status_code == 409
    status["value"] = "stopped"
    container_lifecycle.acquire("profile_003", "exec_busy")
    assert "busy" in client.post("/api/profiles/profile_003/export", json={}).json()["detail"]
    container_lifecycle._write({})

    res = client.post("/api/profiles/profile_003/export", json={"password": "correct horse"})
    assert res.status_code == 200 and ".abprofile" in res.headers["content-disposition"]
    exported = res.content
    held = json.loads((pc / "profile_003" / "config.json").read_text())
    assert held["account"]["on_hold"]["note"] == "Moved to another PC"
    assert container_lifecycle._read() == {}

    proxy_service.save_proxy_pool([])
    wrong = client.post("/api/profiles/import", files={"file": ("a.abprofile", exported)}, data={"password": "nope nope"})
    assert wrong.status_code == 400 and "Wrong password" in wrong.json()["detail"]
    res = client.post("/api/profiles/import", files={"file": ("a.abprofile", exported)}, data={"password": "correct horse"})
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["id"] == "profile_004" and "on_hold" not in created["account"]
    assert (pc / "profile_004" / "chrome_data" / "Default" / "Cookies").read_bytes() == b"session-cookie"
    assert list((tmp_path / "imports").iterdir()) == []
