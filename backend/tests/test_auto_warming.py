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
