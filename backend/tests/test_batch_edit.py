from datetime import datetime, timedelta, timezone

import pytest

from backend.services import batch_edit, profile_pipeline, queue_eta
from backend.services.queue_scheduler import spread_offsets_ms

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def job(eid, pid, status="pending", at=NOW + timedelta(hours=1)):
    return {"execution_id": eid, "profile_id": pid, "status": status, "scheduled_at": at.isoformat(),
            "spun_caption": "old", "preparation_mode": "off", "preparation_status": "not_requested"}


def make_batch(statuses=("pending", "pending")):
    return {
        "batch_id": "b1", "name": "Morning", "profile_execution_order": ["a", "b"],
        "schedule_window": {"batch_iteration_delay_seconds": 30, "session_preparation_mode": "off",
                            "timing": "exact", "spread_minutes": 0},
        "posts": [
            {"post_id": "p_1", "type": "photo", "media_file": "one.jpg", "base_caption": "old", "ai_spin": False,
             "executions": [job("e1", "a", statuses[0]), job("e2", "b", statuses[1])]},
            {"post_id": "p_2", "type": "reel", "media_file": "two.mp4", "base_caption": "second", "ai_spin": False,
             "executions": [job("e3", "a"), job("e4", "b")]},
        ],
    }


@pytest.fixture(autouse=True)
def no_phones(monkeypatch):
    monkeypatch.setattr(batch_edit.android_jobs, "phone_of", lambda pid: None)
    monkeypatch.setattr(batch_edit, "generate_spun_caption", lambda caption, rank, pid: f"{caption}#{pid}")


def test_caption_edit_changes_only_jobs_that_have_not_started():
    batch = make_batch(("published", "pending"))
    result = batch_edit.apply(batch, {"posts": [{"post_id": "p_1", "base_caption": "new"}]}, now=NOW)
    original, edited = batch["posts"][0], batch["posts"][1]
    # The posted account keeps the post exactly as it ran; the rest move to an edited copy after it.
    assert original["base_caption"] == "old" and [e["execution_id"] for e in original["executions"]] == ["e1"]
    assert edited["post_id"] == "p_1_edit1" and edited["base_caption"] == "new"
    assert [e["execution_id"] for e in edited["executions"]] == ["e2"]
    assert edited["executions"][0]["spun_caption"] == "new"
    assert original["executions"][0]["spun_caption"] == "old"
    assert result == {"changes": ["post 1: caption"], "jobs_changed": 1}
    assert batch["posts"][2]["post_id"] == "p_2"  # account order of posts is unchanged


def test_edit_in_place_when_nothing_started_and_spin_again():
    batch = make_batch()
    batch_edit.apply(batch, {"posts": [{"post_id": "p_1", "base_caption": "fresh", "ai_spin": True,
                                        "first_comment": " https://x.test "}]}, now=NOW)
    post = batch["posts"][0]
    assert len(batch["posts"]) == 2 and post["first_comment"] == "https://x.test"
    assert [e["spun_caption"] for e in post["executions"]] == ["fresh#a", "fresh#b"]


def test_refused_edits_change_nothing():
    batch = make_batch()
    with pytest.raises(batch_edit.EditError, match="video"):
        batch_edit.apply(batch, {"name": "Renamed", "posts": [{"post_id": "p_2", "media_file": "photo.jpg"}]}, now=NOW)
    assert batch["name"] == "Morning" and batch["posts"][1]["media_file"] == "two.mp4"
    batch["posts"][0]["website_preparation_id"] = "prep"
    with pytest.raises(batch_edit.EditError, match="Read more"):
        batch_edit.apply(batch, {"posts": [{"post_id": "p_1", "base_caption": "x"}]}, now=NOW)
    # The same values as before are not an edit, even on a locked post.
    assert batch_edit.apply(batch, {"posts": [{"post_id": "p_1", "base_caption": "old"}]}, now=NOW)["jobs_changed"] == 0


def test_new_exact_time_moves_only_waiting_jobs_and_keeps_order():
    batch = make_batch(("running", "pending"))
    start = NOW + timedelta(hours=5)
    choice = {"timing": "exact", "start_at": start, "spread_minutes": 0, "follow_active_hours": False}
    batch_edit.apply(batch, {}, choice=choice, offsets=spread_offsets_ms, now=NOW)
    jobs = {e["execution_id"]: e for p in batch["posts"] for e in p["executions"]}
    assert jobs["e1"]["scheduled_at"] == (NOW + timedelta(hours=1)).isoformat()  # running: untouched
    a, b = (datetime.fromisoformat(jobs[e]["scheduled_at"]) for e in ("e3", "e2"))
    assert start <= a < b < start + timedelta(minutes=5)  # a goes first, b a short step after
    assert jobs["e3"]["stage_history"][-1]["reason"] == "rescheduled"
    assert batch["schedule_window"]["start_at"] == start.isoformat()


def test_follow_active_hours_plans_each_account_again():
    batch = make_batch()
    asked = []

    def plan_times(pid, count, exclude):
        asked.append((pid, count, exclude))
        base = NOW.timestamp() * 1000 + (3 if pid == "a" else 4) * 3600_000
        return [base + 3 * 3600_000, base]

    choice = {"timing": "auto", "start_at": None, "spread_minutes": None, "follow_active_hours": True}
    batch_edit.apply(batch, {}, choice=choice, plan_times=plan_times, now=NOW)
    assert asked == [("a", 2, {"e1", "e3"}), ("b", 2, {"e2", "e4"})]
    jobs = {e["execution_id"]: datetime.fromisoformat(e["scheduled_at"]) for p in batch["posts"] for e in p["executions"]}
    assert jobs["e1"] == NOW + timedelta(hours=3) and jobs["e3"] == NOW + timedelta(hours=6)
    window = batch["schedule_window"]
    assert window["timing"] == "auto" and window["follow_active_hours"] and "spread_minutes" not in window


def test_paused_batch_starts_nothing_and_says_so(monkeypatch):
    batch = make_batch()
    for post in batch["posts"]:
        for execution in post["executions"]:
            execution["scheduled_at"] = (NOW - timedelta(minutes=5)).isoformat()
    batch_edit.set_paused(batch, True, NOW)
    queue = {"daily_batches": [batch]}
    assert profile_pipeline.ordered_due_executions(queue, NOW.timestamp() * 1000) == []
    monkeypatch.setattr(queue_eta.active_hours, "_profile_config", lambda pid, cache: {})
    reasons = {v["wait_reason"] for v in queue_eta.explain(queue, NOW.timestamp() * 1000).values()}
    assert reasons == {"paused"}
    batch_edit.set_paused(batch, False, NOW)
    assert "paused" not in batch and [h["changes"] for h in batch["edit_history"]] == [["Paused"], ["Resumed"]]


def test_one_job_time_is_kept_even_in_an_older_batch():
    batch = make_batch()
    batch["schedule_window"] = {"profile_stagger_seconds": 60}
    execution = batch["posts"][0]["executions"][1]
    at = NOW + timedelta(hours=9)
    assert batch_edit.edit_job(execution, batch["posts"][0], scheduled_at=at, caption="mine", now=NOW) == ["caption", "time"]
    start = profile_pipeline.execution_start_ms(batch, execution, NOW.timestamp() * 1000, {"a": 0, "b": 1}, 60_000)
    assert start == at.timestamp() * 1000 and execution["spun_caption"] == "mine"
    execution["status"] = "running"
    with pytest.raises(batch_edit.EditError, match="already started"):
        batch_edit.edit_job(execution, batch["posts"][0], caption="again", now=NOW)


def test_all_at_once_gives_every_account_the_same_start_and_all_are_due(monkeypatch):
    from backend.services.queue_scheduler import ALL_AT_ONCE
    assert spread_offsets_ms(3, ALL_AT_ONCE) == [0.0, 0.0, 0.0]
    batch = make_batch()
    start = NOW + timedelta(hours=2)
    choice = {"timing": "exact", "start_at": start, "spread_minutes": ALL_AT_ONCE, "follow_active_hours": False}
    batch_edit.apply(batch, {}, choice=choice, offsets=spread_offsets_ms, now=NOW)
    starts = {e["scheduled_at"] for p in batch["posts"] for e in p["executions"]}
    assert starts == {start.isoformat()} and batch["schedule_window"]["spread_minutes"] == ALL_AT_ONCE
    monkeypatch.setattr("backend.services.active_hours.admits", lambda *args, **kwargs: True)
    monkeypatch.setattr("backend.services.account_hold.admits", lambda *args, **kwargs: True)
    due = profile_pipeline.ordered_due_executions({"daily_batches": [batch]}, start.timestamp() * 1000 + 1)
    assert {e["profile_id"] for e in due} == {"a", "b"}
