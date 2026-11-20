"""Replace and move: accounts are made inactive before Webshare replaces the IP, then moved."""
import json
from contextlib import contextmanager

import pytest

from backend.services import proxy_change, proxy_replace_move, proxy_service, webshare_service
from backend.services.adb_client import AndroidUser


@pytest.fixture
def world(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    monkeypatch.setattr(proxy_change, "PROFILES_DIR", profiles)
    pool = tmp_path / "proxy_pool.json"
    monkeypatch.setattr(proxy_service, "PROXY_POOL_FILE", pool)
    state = {"jobs": {}, "browsers": {}, "replaced": [], "moved": [], "stopped_browsers": []}

    def account(profile_id, phone_user=None):
        (profiles / profile_id).mkdir(parents=True)
        runtime = {"kind": "android_user", "device_serial": "PIXEL", "android_user_id": phone_user} if phone_user else {"kind": "docker"}
        (profiles / profile_id / "config.json").write_text(json.dumps({"id": profile_id, "name": profile_id.upper(), "runtime": runtime}))

    def proxy(proxy_id, holders, **extra):
        data = json.loads(pool.read_text()) if pool.exists() else []
        data.append({"id": proxy_id, "host": f"10.0.0.{len(data) + 1}", "port": 1080, "username": "u", "password": "p",
                     "type": "socks5", "source_id": "default", "profile_ids": holders, **extra})
        pool.write_text(json.dumps(data))

    monkeypatch.setattr(proxy_change, "job_reason", lambda pid, config=None: state["jobs"].get(pid))
    monkeypatch.setattr(proxy_service, "account_limit", lambda: 2)  # a proxy that serves two accounts

    def replace_one(proxy_id):
        state["replaced"].append(proxy_id)
        proxy("new_" + proxy_id, [])
        old = next(p for p in proxy_service.load_proxy_pool() if p["id"] == proxy_id)
        proxy_service.update_proxy_metadata([proxy_id], replaced_by="new_" + proxy_id)
        return {"proxy_id": proxy_id, "old": f"{old['host']}:1080", "new": "x:1", "new_id": "new_" + proxy_id,
                "accounts": proxy_service.holders(old)}
    monkeypatch.setattr(webshare_service, "replace_one", replace_one)

    def change(profile_id, new_id, move_pc):
        if state.get("refuse") == profile_id:
            raise ValueError("Traffic didn't get through this proxy, so the phone kept its old one.")
        state["moved"].append((profile_id, new_id))
        proxy_service.assign_proxy_by_id(profile_id, new_id)
    monkeypatch.setattr(proxy_change, "change", change)

    from backend.services import docker_service
    monkeypatch.setattr(docker_service, "get_container_status", lambda pid, **k: state["browsers"].get(pid, "stopped"))

    async def run_profile_action(pid, action):
        state["stopped_browsers"].append(pid)
        state["browsers"][pid] = "stopped"
    monkeypatch.setattr(docker_service, "run_profile_action", run_profile_action)
    state.update(account=account, proxy=proxy, pool=lambda: {p["id"]: p for p in json.loads(pool.read_text())})
    return state


def test_browsers_stop_before_the_ip_is_replaced_then_every_account_moves(world):
    world["account"]("profile_001")
    world["account"]("profile_002")
    world["browsers"]["profile_001"] = "running"
    world["proxy"]("old", ["profile_001", "profile_002"])
    result = proxy_replace_move.replace_and_move("old", move_pc=None)
    assert world["stopped_browsers"] == ["profile_001"] and world["replaced"] == ["old"]
    assert world["moved"] == [("profile_001", "new_old"), ("profile_002", "new_old")]
    assert result["moved"] == ["profile_001", "profile_002"] and result["move_failed"] == []
    assert "old" not in world["pool"]()  # every account left it
    assert world["browsers"]["profile_001"] == "stopped"  # nothing is started again


def test_a_job_on_any_account_leaves_the_proxy_and_every_browser_alone(world):
    world["account"]("profile_001")
    world["account"]("profile_002")
    world["browsers"]["profile_001"] = "running"
    world["jobs"]["profile_002"] = "A job is running on this account. Change the proxy when it finishes."
    world["proxy"]("old", ["profile_001", "profile_002"])
    with pytest.raises(webshare_service.WebshareError, match="PROFILE_002 isn't ready, A job is running"):
        proxy_replace_move.replace_and_move("old", move_pc=None)
    assert world["replaced"] == [] and world["stopped_browsers"] == []
    assert world["pool"]()["old"]["profile_ids"] == ["profile_001", "profile_002"]


def test_an_account_that_cannot_move_keeps_the_old_proxy_and_is_reported(world):
    world["account"]("profile_001")
    world["account"]("profile_002")
    world["proxy"]("old", ["profile_001", "profile_002"])
    world["refuse"] = "profile_002"
    result = proxy_replace_move.replace_and_move("old", move_pc=None)
    assert result["moved"] == ["profile_001"]
    assert result["move_failed"][0]["name"] == "PROFILE_002" and "Traffic didn't get through" in result["move_failed"][0]["error"]
    assert world["pool"]()["old"]["profile_ids"] == ["profile_002"]


def test_a_proxy_already_replaced_only_moves_its_accounts(world):
    world["account"]("profile_001")
    world["proxy"]("new", [])
    world["proxy"]("old", ["profile_001"], replaced_by="new", replaced_by_address="10.0.0.1:1080")
    result = proxy_replace_move.replace_and_move("old", move_pc=None)
    assert world["replaced"] == [] and result["already_replaced"] is True
    assert world["moved"] == [("profile_001", "new")] and "old" not in world["pool"]()


class Phone:
    def __init__(self, current, running):
        self.current, self.running, self.calls = current, set(running), []

    def current_user(self, serial):
        return self.current

    def users(self, serial):
        return [AndroidUser(uid, str(uid), 0, uid in self.running) for uid in (0, 11, 12)]

    def stop_user(self, serial, user_id):
        self.calls.append(("stop", user_id))
        self.running.discard(user_id)


@pytest.fixture
def phone(monkeypatch):
    from backend.services import android_devices, android_ownership

    @contextmanager
    def lock(serial):
        yield

    device = Phone(current=11, running={0, 11})
    monkeypatch.setattr(android_devices, "_client", lambda: device)
    monkeypatch.setattr(android_devices, "phone_lock", lock)
    monkeypatch.setattr(android_ownership, "ensure_primary", lambda serial, client=None: None)

    def switch_and_wait(client, serial, user_id, progress=None):
        device.calls.append(("switch", user_id))
        left, device.current = device.current, user_id
        client.stop_user(serial, left)
        return {"ready": True}
    monkeypatch.setattr(android_devices, "switch_and_wait", switch_and_wait)
    return device


def test_a_phone_account_on_screen_goes_to_the_main_user_and_is_stopped(world, phone):
    world["account"]("profile_001", phone_user=11)
    proxy_replace_move.make_inactive("profile_001")
    assert phone.calls == [("switch", 0), ("stop", 11)] and phone.running == {0}


def test_a_phone_account_in_the_background_is_stopped_without_a_switch(world, phone):
    phone.current, phone.running = 0, {0, 12}
    world["account"]("profile_002", phone_user=12)
    proxy_replace_move.make_inactive("profile_002")
    assert phone.calls == [("stop", 12)]


def test_a_phone_user_that_keeps_running_stops_the_replacement(world, phone, monkeypatch):
    phone.current, phone.running = 0, {0, 12}
    monkeypatch.setattr(phone, "stop_user", lambda serial, user_id: None)
    world["account"]("profile_002", phone_user=12)
    world["proxy"]("old", ["profile_002"])
    with pytest.raises(webshare_service.WebshareError, match="kept running"):
        proxy_replace_move.replace_and_move("old", move_pc=None)
    assert world["replaced"] == []
