"""Auto warming uses spare capacity only and always gives way to posts."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import auto_warming
from backend.services.auto_warming import (
    add_visit, candidates, expire_unstarted, spare_capacity, yield_for_post,
)

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)  # 11:00 in New York
NOW_MS = NOW.timestamp() * 1000


def config(pid="profile_001", **extra):
    return {"id": pid, "created_at": (NOW - timedelta(days=40)).isoformat(),
            "fingerprint": {"timezone": "America/New_York"}, **extra}


def at(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def history_entry(hours_ago, status="published", kind="post"):
    return {"at": at(hours_ago), "status": status, "kind": kind, "error": ""}


def queue(*executions, batch_id="batch_1", kind="photo"):
    return {"daily_batches": [{"batch_id": batch_id, "posts": [{"type": kind, "executions": list(executions)}]}]}


@pytest.fixture(autouse=True)
def awake(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_warming.active_hours, "persona_hours_admit", lambda config, now_ms: True)
    monkeypatch.setattr(auto_warming, "persona_day", lambda config, now_ms: {"rest_day": False, "frequency": 1.0})
    monkeypatch.setattr(auto_warming, "YIELD_FILE", tmp_path / "yield.json")


def rank(configs, health, history=None, q=None):
    return candidates(q or {"daily_batches": []}, configs, health, history or {}, NOW_MS)


def test_new_accounts_come_first_and_attention_never_warms():
    configs = {pid: config(pid) for pid in ("a", "b", "c")}
    health = {"a": {"stage": "ready"}, "b": {"stage": "new"}, "c": {"stage": "attention"}}
    ranked = rank(configs, health)
    assert [item["profile_id"] for item in ranked] == ["b", "a"]


def test_daily_cap_and_gaps_are_respected():
    configs = {"a": config("a")}
    health = {"a": {"stage": "ready"}}
    two_visits = {"a": {"v1": history_entry(5, "completed", "warming"), "v2": history_entry(3, "completed", "warming")}}
    assert rank(configs, health, two_visits) == []
    recent_visit = {"a": {"v1": history_entry(1, "completed", "warming")}}
    assert rank(configs, health, recent_visit) == []
    recent_post = {"a": {"p1": history_entry(0.2)}}
    assert rank(configs, health, recent_post) == []


def test_rest_days_and_sleeping_hours_skip_the_account(monkeypatch):
    configs, health = {"a": config("a")}, {"a": {"stage": "new"}}
    monkeypatch.setattr(auto_warming, "persona_day", lambda config, now_ms: {"rest_day": True, "frequency": 1.0})
    assert rank(configs, health) == []
    monkeypatch.setattr(auto_warming, "persona_day", lambda config, now_ms: {"rest_day": False, "frequency": 1.0})
    monkeypatch.setattr(auto_warming.active_hours, "persona_hours_admit", lambda config, now_ms: False)
    assert rank(configs, health) == []


def test_a_post_due_soon_keeps_the_account_free_and_a_later_one_gets_a_warm_up():
    configs, health = {"a": config("a"), "b": config("b")}, {"a": {"stage": "ready"}, "b": {"stage": "ready"}}
    soon = {"profile_id": "a", "status": "pending", "scheduled_at": (NOW + timedelta(minutes=10)).isoformat()}
    assert [item["profile_id"] for item in rank(configs, health, q=queue(soon))] == ["b"]
    later = dict(soon, scheduled_at=(NOW + timedelta(minutes=40)).isoformat())
    ranked = rank(configs, health, q=queue(later))
    assert ranked[0]["profile_id"] == "a"
    assert "Warm-up before its post" in ranked[0]["reason"]


def test_accounts_already_busy_are_skipped():
    configs, health = {"a": config("a")}, {"a": {"stage": "new"}}
    running = {"profile_id": "a", "status": "running", "scheduled_at": at(0.1)}
    assert rank(configs, health, q=queue(running)) == []


def test_spare_capacity_keeps_slots_for_posts_due_soon():
    cfg = {"max_publishers": 1, "max_preparers": 2, "max_total_automation_tasks": 2, "max_active_profile_containers": 2}
    assert spare_capacity({"daily_batches": []}, [], cfg, NOW_MS)
    due = [{"profile_id": f"p{i}", "status": "pending", "scheduled_at": NOW.isoformat()} for i in range(2)]
    assert not spare_capacity(queue(*due), [], cfg, NOW_MS)


def test_visits_go_to_one_daily_batch_at_the_end_of_the_queue():
    q = queue({"profile_id": "x", "status": "pending", "scheduled_at": at(1)})
    choice = {"profile_id": "a", "stage": "new", "score": 4.2, "reason": "New account, quiet for 9 h"}
    first = add_visit(q, choice, NOW, random.Random(1))
    add_visit(q, dict(choice, profile_id="b"), NOW, random.Random(2))
    batch = q["daily_batches"][-1]
    assert batch["batch_id"].startswith(auto_warming.AUTO_BATCH_PREFIX)
    assert len(q["daily_batches"]) == 2 and len(batch["posts"]) == 2
    assert batch["posts"][0]["type"] == "warming" and batch["posts"][0]["warming_options"]["auto"] is True
    assert 180 <= batch["posts"][0]["warming_options"]["max_seconds"] <= 360
    assert first["auto_warming"]["reason"] == choice["reason"]
    assert first["preparation_mode"] == "off"


def test_unstarted_visits_expire_and_switching_off_cancels_them():
    q = {"daily_batches": []}
    choice = {"profile_id": "a", "stage": "new", "score": 1, "reason": "r"}
    visit = add_visit(q, choice, NOW - timedelta(minutes=40), random.Random(1))
    assert expire_unstarted(q, NOW_MS) == 1 and visit["status"] == "cancelled"
    fresh = add_visit(q, dict(choice, profile_id="b"), NOW, random.Random(1))
    assert expire_unstarted(q, NOW_MS) == 0
    assert expire_unstarted(q, NOW_MS, all_pending=True) == 1 and fresh["status"] == "cancelled"


def running_visit(pid, score):
    return {"profile_id": pid, "status": "running", "scheduled_at": at(0.1), "auto_warming": {"score": score}}


def test_a_post_on_a_warming_account_takes_over_its_browser():
    q = queue(running_visit("a", 2.0), batch_id="auto_warming_2026-10-31", kind="warming")
    assert yield_for_post(q, {"profile_id": "a", "execution_id": "post_1"})
    assert auto_warming.read_yields()["a"]["handoff_to"] == "post_1"


def test_another_post_ends_the_visit_with_the_lowest_score_once():
    q = queue(running_visit("a", 3.0), running_visit("b", 1.0), batch_id="auto_warming_2026-10-31", kind="warming")
    assert yield_for_post(q, {"profile_id": "c", "execution_id": "post_1"})
    assert list(auto_warming.read_yields()) == ["b"]
    yield_for_post(q, {"profile_id": "d", "execution_id": "post_2"})
    assert list(auto_warming.read_yields()) == ["b"]
    assert not yield_for_post({"daily_batches": []}, {"profile_id": "c"})


def test_a_yielded_visit_hands_the_container_to_its_post(monkeypatch):
    from backend.services import automation_service
    post = {"execution_id": "post_1", "profile_id": "a", "status": "pending", "preparation_status": "pending", "stage": "pending"}
    q = queue(post)
    saved = []
    handoffs = []
    monkeypatch.setattr(automation_service, "load_posting_queue", lambda: q)
    monkeypatch.setattr(automation_service, "save_posting_queue", lambda value: saved.append(value) or True)
    monkeypatch.setattr(automation_service.lifecycle, "handoff",
                        lambda pid, token, seconds, execution_id=None: handoffs.append((pid, execution_id)) or True)
    auto_warming.request_yield("a", "own_post", handoff_to="post_1")
    assert automation_service._hand_auto_visit_to_post("a", "token", "visit_1", {"success": True})
    assert handoffs == [("a", "post_1")]
    assert post["status"] == "ready" and post["preparation_status"] == "ready"
    assert post["stage_history"][-1]["reason"] == "warmed_by_auto_visit"
    assert auto_warming.read_yields() == {}


def test_a_visit_without_a_handoff_request_releases_its_container(monkeypatch):
    from backend.services import automation_service
    monkeypatch.setattr(automation_service.lifecycle, "handoff", lambda *a, **k: pytest.fail("no handoff expected"))
    assert not automation_service._hand_auto_visit_to_post("a", "token", "visit_1", {"success": True})


def test_dispatcher_starts_no_visit_while_a_post_waits_for_a_slot(monkeypatch):
    import asyncio
    from backend.services import automation_service as automation
    visit = {"execution_id": "visit_1", "profile_id": "a", "status": "pending", "scheduled_at": at(0.1),
             "auto_warming": {"score": 1.0}, "preparation_status": "not_requested"}
    post = {"execution_id": "post_1", "profile_id": "b", "status": "pending", "scheduled_at": at(0.2),
            "preparation_mode": "off", "preparation_status": "not_requested"}
    q = {"daily_batches": [
        {"batch_id": "auto_warming_2026-10-31", "posts": [{"type": "warming", "executions": [visit]}]},
        {"batch_id": "batch_1", "posts": [{"type": "photo", "executions": [post]}]},
    ]}
    started, yields = [], []
    monkeypatch.setattr(automation, "load_posting_queue", lambda: q)
    monkeypatch.setattr(automation, "recover_stale_queue_executions", lambda reason: None)
    monkeypatch.setattr(automation, "get_scheduler_config", lambda: {
        "max_publishers": 1, "max_preparers": 1, "max_total_automation_tasks": 1,
        "max_active_profile_containers": 1, "lease_ttl_ms": 60000})
    # Visits sort first by time; the dispatcher must still try the post first.
    monkeypatch.setattr(automation, "ordered_due_executions", lambda queue, now_ms: [visit, post])
    monkeypatch.setattr(automation, "cloud_execution_allowed", lambda execution: True)
    monkeypatch.setattr(automation, "get_container_status", lambda pid: "stopped")
    monkeypatch.setattr(automation, "count_running_profile_containers", lambda: 1)
    monkeypatch.setattr(auto_warming, "enabled", lambda: True)
    monkeypatch.setattr(auto_warming, "yield_for_post", lambda queue, execution: yields.append(execution["execution_id"]))
    monkeypatch.setattr(automation, "_run_dispatched_queue_task", lambda exec_id, kind: started.append(exec_id))
    monkeypatch.setattr("backend.services.queue_network_recovery.proxy_measurement_active", lambda *args: False)
    asyncio.run(automation.dispatch_pending_queue())
    assert yields == ["post_1"]
    assert started == []


PHONE = {"kind": "android_user", "device_serial": "PHONE1", "android_user_id": 11}


def phone_config():
    return config("phone_1", runtime=PHONE, automation={"enabled": False, "reason": "android_automation_unavailable"},
                  network={"mode": "pool", "proxy_id": "proxy_1"})


def test_phone_accounts_get_visits_when_their_phone_can_take_one(monkeypatch):
    from backend.services import android_jobs
    health = {"phone_1": {"stage": "ready"}}
    monkeypatch.setattr(android_jobs, "can_take_auto_visit", lambda cfg: True)
    assert [c["profile_id"] for c in candidates(queue(), {"phone_1": phone_config()}, health, {}, NOW_MS)] == ["phone_1"]
    monkeypatch.setattr(android_jobs, "can_take_auto_visit", lambda cfg: False)
    assert candidates(queue(), {"phone_1": phone_config()}, health, {}, NOW_MS) == []


def test_a_phone_account_marked_manual_is_left_alone(monkeypatch):
    from backend.services import android_jobs
    monkeypatch.setattr(android_jobs, "can_take_auto_visit", lambda cfg: True)
    manual = config("phone_1", runtime=PHONE, automation={"enabled": False, "reason": "manual_control_only"})
    assert candidates(queue(), {"phone_1": manual}, {"phone_1": {"stage": "ready"}}, {}, NOW_MS) == []


def test_a_failed_phone_visit_is_not_tried_again_every_minute(monkeypatch):
    from backend.services import android_jobs
    monkeypatch.setattr(android_jobs, "can_take_auto_visit", lambda cfg: True)
    history = {"phone_1": {"e1": history_entry(0.2, status="failed_before_publish", kind="warming")}}
    assert candidates(queue(), {"phone_1": phone_config()}, {"phone_1": {"stage": "ready"}}, history, NOW_MS) == []
    history = {"phone_1": {"e1": history_entry(2, status="failed_before_publish", kind="warming")}}
    assert candidates(queue(), {"phone_1": phone_config()}, {"phone_1": {"stage": "ready"}}, history, NOW_MS)


def test_a_running_visit_ends_for_work_on_its_phone_only(monkeypatch):
    from backend.services import android_jobs
    phones = {"phone_1": {"device_serial": "PHONE1"}, "phone_2": {"device_serial": "PHONE2"}}
    monkeypatch.setattr(android_jobs, "phone_of", phones.get)
    asked = []
    monkeypatch.setattr(auto_warming, "read_yields", lambda: {})
    monkeypatch.setattr(auto_warming, "request_yield", lambda pid, reason, handoff_to=None: asked.append((pid, reason, handoff_to)))
    running = queue({"profile_id": "phone_2", "status": "running"}, batch_id="auto_warming_2026-10-31", kind="warming")
    assert auto_warming.yield_phone_for_post(running, "PHONE1") is False
    assert auto_warming.yield_phone_for_post(running, "PHONE2") is True
    assert asked == [("phone_2", "post_needs_phone", None)]


def test_a_phone_account_without_a_proxy_gets_no_visit(monkeypatch):
    from backend.services import android_jobs
    monkeypatch.setattr(android_jobs, "can_take_auto_visit", lambda cfg: True)
    own_network = config("phone_1", runtime=PHONE, automation={"enabled": False, "reason": "android_automation_unavailable"},
                         network={"mode": "direct", "proxy_id": None})
    assert candidates(queue(), {"phone_1": own_network}, {"phone_1": {"stage": "ready"}}, {}, NOW_MS) == []


class _Store:
    """The queue file, with a hook that runs while plan_once is choosing (as another thread would)."""

    def __init__(self, queue):
        self.queue, self.saves = queue, 0

    def load(self):
        import copy
        return copy.deepcopy(self.queue)

    def save(self, queue):
        import copy
        self.queue, self.saves = copy.deepcopy(queue), self.saves + 1
        return True


@pytest.fixture
def planner(monkeypatch):
    from backend.services import account_health, automation_service, docker_service, queue_service

    def setup(queue, during_choice=lambda store: None):
        store = _Store(queue)
        monkeypatch.setattr(queue_service, "load_posting_queue", store.load)
        monkeypatch.setattr(queue_service, "save_posting_queue", store.save)
        monkeypatch.setattr(auto_warming, "enabled", lambda: True)
        monkeypatch.setattr(auto_warming, "_machine_calm", lambda: True)
        monkeypatch.setattr(auto_warming, "spare_capacity", lambda *args, **kwargs: True)
        monkeypatch.setattr(account_health, "sync_history", lambda queue, now: {})
        monkeypatch.setattr(account_health, "compute", lambda config, entries, now: {"stage": "new"})
        monkeypatch.setattr(automation_service, "active_in_memory_scheduler_leases", lambda: [])
        monkeypatch.setattr(docker_service, "get_scheduler_config", lambda: {})

        def configs():
            during_choice(store)
            return {"profile_001": config()}
        monkeypatch.setattr(auto_warming, "_profile_configs", configs)
        return store
    return setup


def _status(store, execution_id):
    return next(e["status"] for _b, _p, e in auto_warming._executions(store.queue) if e["execution_id"] == execution_id)


def test_choosing_a_visit_never_undoes_a_save_made_meanwhile(planner):
    other = {"execution_id": "e_other", "profile_id": "profile_002", "status": "uncertain", "scheduled_at": at(3)}

    def resolved_by_user(store):
        store.queue["daily_batches"][0]["posts"][0]["executions"][0]["status"] = "published"
    store = planner(queue(other), resolved_by_user)

    choice = auto_warming.plan_once(NOW)

    assert choice and choice["profile_id"] == "profile_001"
    assert _status(store, "e_other") == "published"
    assert any(auto_warming.is_auto_batch(batch) for batch in store.queue["daily_batches"])


def test_no_visit_when_the_account_gets_a_post_while_choosing(planner):
    def post_claimed(store):
        store.queue["daily_batches"][0]["posts"][0]["executions"].append(
            {"execution_id": "e_post", "profile_id": "profile_001", "status": "running", "scheduled_at": at(0)})
    store = planner(queue(), post_claimed)

    assert auto_warming.plan_once(NOW) is None
    assert not any(auto_warming.is_auto_batch(batch) for batch in store.queue["daily_batches"])
    assert _status(store, "e_post") == "running"


def test_a_visit_starts_on_the_profile_when_its_page_reading_is_missing_or_old(monkeypatch):
    stale = {"profile_001"}
    monkeypatch.setattr(auto_warming.facebook_page, "needs_refresh", lambda pid, now=None: pid in stale)
    configs = {"profile_001": config("profile_001"), "profile_002": config("profile_002")}
    health = {"profile_001": {"stage": "new"}, "profile_002": {"stage": "new"}}
    by_id = {item["profile_id"]: item for item in rank(configs, health)}
    assert by_id["profile_001"]["read_profile"] is True and "read_profile" not in by_id["profile_002"]
    q = {"daily_batches": []}
    first = add_visit(q, by_id["profile_001"], NOW, random.Random(1))
    second = add_visit(q, by_id["profile_002"], NOW, random.Random(1))
    posts = q["daily_batches"][0]["posts"]
    assert posts[0]["warming_options"]["surface"] == "profile" and posts[1]["warming_options"]["surface"] == "random"
    assert first["profile_id"] == "profile_001" and second["profile_id"] == "profile_002"


def test_phone_visits_keep_a_random_surface(monkeypatch):
    monkeypatch.setattr(auto_warming.facebook_page, "needs_refresh", lambda pid, now=None: True)
    from backend.services import android_jobs
    monkeypatch.setattr(android_jobs, "can_take_auto_visit", lambda config: True)
    phone = config("profile_003", runtime={"kind": "android_user"}, network={"proxy_id": "proxy_1"})
    [choice] = rank({"profile_003": phone}, {"profile_003": {"stage": "new"}})
    assert "read_profile" not in choice
