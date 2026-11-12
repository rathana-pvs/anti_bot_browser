"""Queue jobs on phones: one job per phone, the publish rules, and the first comment as a follow-up."""
import asyncio
import copy
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.config import AUTOMATION_RUNNER, automation_runner
from backend.services import android_devices, android_jobs, automation_service
from backend.services import container_lifecycle as lifecycle

SERIAL, USER = "PIXEL", 11
RUNTIME = {"kind": "android_user", "device_serial": SERIAL, "android_user_id": USER}


def test_phone_runner_is_found_next_to_the_automation_runner():
    assert android_jobs.PHONE_RUNNER == AUTOMATION_RUNNER.with_name("phone_runner.py")
    assert android_jobs.PHONE_RUNNER.is_file()


def test_installed_build_uses_the_runner_folder_outside_its_temporary_bundle(tmp_path):
    # An installed backend has no automation folder inside its temporary bundle.
    runtime = tmp_path / "runtime" / "automation"
    runtime.mkdir(parents=True)
    (runtime / "runner.py").write_text("")
    runner = automation_runner(tmp_path / "_MEI_bundle", tmp_path / "runtime")
    assert runner.with_name("phone_runner.py") == runtime / "phone_runner.py"


@pytest.fixture
def world(monkeypatch, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.png").write_bytes(b"png")
    (media / "clip.mp4").write_bytes(b"mp4")
    profiles = tmp_path / "profiles"
    (profiles / "phone_1").mkdir(parents=True)
    (profiles / "phone_1" / "config.json").write_text(json.dumps({"runtime": RUNTIME, "account": {}}))
    state = {"queue": {"daily_batches": [{"batch_id": "b1", "posts": [
        {"type": "photo", "media_file": "a.png", "base_caption": "Morning coffee", "first_comment": "First!",
         "executions": [{"execution_id": "e1", "profile_id": "phone_1", "status": "running", "stage": "running",
                         "spun_caption": "Morning coffee", "stage_history": [],
                         "scheduler_lease": {"lease_id": "L1"}}]}]}]},
             "runs": [], "opened": []}
    monkeypatch.setattr(android_jobs, "SHARED_MEDIA_DIR", media)
    monkeypatch.setattr(android_jobs, "PROFILES_DIR", profiles)
    monkeypatch.setattr(android_jobs, "load_posting_queue", lambda: copy.deepcopy(state["queue"]))
    monkeypatch.setattr(android_jobs, "save_posting_queue", lambda q: state.update(queue=copy.deepcopy(q)) or True)
    monkeypatch.setattr(android_jobs, "persist_queue_execution_stage",
                        lambda eid, stage, at, lease: state.setdefault("stages", []).append(stage))
    monkeypatch.setattr(android_jobs, "phone_of", lambda pid: RUNTIME if pid == "phone_1" else None)
    monkeypatch.setattr(android_jobs.android_network, "open_account",
                        lambda pid, job=False: state["opened"].append((pid, job)) or {"ready": True})

    async def no_dispatch(delay_ms=100):
        return None
    monkeypatch.setattr(automation_service, "_delayed_dispatch", no_dispatch)
    monkeypatch.setattr(android_devices, "_job_holders", {})
    monkeypatch.setattr(android_jobs, "_last_post_at", {})
    monkeypatch.setattr(lifecycle, "OWNERS_FILE", tmp_path / "owners.json")
    monkeypatch.setattr(lifecycle, "LOCK_FILE", tmp_path / "owners.lock")
    automation_service.active_automation_tasks.pop("phone_1", None)
    return state


def runner(monkeypatch, state, events, result):
    async def fake(args, record, on_event):
        state["runs"].append(args)
        for event in events:
            on_event(event)
        record["exit_code"] = 0 if result.get("status") == "published" else 1
        return result
    monkeypatch.setattr(android_jobs, "_run_runner", fake)


def claim(state):
    execution = state["queue"]["daily_batches"][0]["posts"][0]["executions"][0]
    post = state["queue"]["daily_batches"][0]["posts"][0]
    return {"targetExec": execution, "targetPost": post, "leaseId": "L1", "lease": {"lease_id": "L1"}}


def execution(state):
    return state["queue"]["daily_batches"][0]["posts"][0]["executions"][0]


def test_unsupported_jobs_are_named():
    assert "not available on phones" in android_jobs.unsupported_reason({"type": "live", "media_file": "x.mp4"})
    assert "not available on phones" in android_jobs.unsupported_reason({"type": "warming", "warming_options": {"check": True}})
    assert android_jobs.unsupported_reason({"type": "warming"}) is None


def test_media_must_suit_the_kind(world):
    assert android_jobs.unsupported_reason({"type": "photo", "media_file": "a.png"}) is None
    assert android_jobs.unsupported_reason({"type": "reel", "media_file": "clip.mp4"}) is None
    assert "MP4" in android_jobs.unsupported_reason({"type": "reel", "media_file": "a.png"})
    assert "JPEG or PNG" in android_jobs.unsupported_reason({"type": "photo", "media_file": "clip.mp4"})
    assert "media library" in android_jobs.unsupported_reason({"type": "photo", "media_file": "../secret.png"})


def test_phone_accounts_are_accepted_by_the_queue():
    config = {"runtime": RUNTIME, "automation": {"enabled": False, "reason": android_devices.AUTOMATION_UNAVAILABLE}}
    assert android_jobs.accepts_automation(config)
    assert not android_jobs.accepts_automation({"automation": {"enabled": False, "reason": "manual_control_only"}})


def test_published_post_leaves_the_comment_for_a_follow_up(world, monkeypatch):
    runner(monkeypatch, world, [{"event": "about_to_publish"}, {"event": "published"}],
           {"status": "published", "comment": "pending"})
    result = asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    ex = execution(world)
    assert result == {"success": True, "status": "published"}
    assert ex["status"] == "published" and ex["first_comment_status"] == "phone_pending"
    assert ex["publish_tapped_at"] and ex["phone_comment_due_at"]
    assert world["stages"] == ["publish_clicked"]
    assert world["opened"] == [("phone_1", True)]
    args = world["runs"][0]
    assert args[args.index("--task") + 1] == "photo" and "--comment-later" in args
    assert android_devices.job_on(SERIAL) is None  # released


def test_the_comment_is_due_as_soon_as_the_post_is_confirmed(world, monkeypatch):
    """No gap in which another account's post could take the phone first."""
    runner(monkeypatch, world, [{"event": "about_to_publish"}, {"event": "published"}],
           {"status": "published", "comment": "pending"})
    asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    due = android_jobs.due_comments(world["queue"], time.time())
    assert [ex["execution_id"] for ex, _ in due] == [execution(world)["execution_id"]]


def test_a_stop_after_the_publish_tap_is_uncertain_never_failed(world, monkeypatch):
    runner(monkeypatch, world, [{"event": "about_to_publish"}], {})
    result = asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    assert result["status"] == "uncertain"
    assert execution(world).get("retry_count") is None


def test_failure_before_the_tap_counts_a_retry(world, monkeypatch):
    runner(monkeypatch, world, [], {"status": "failed", "reason": "composer: Gallery not found"})
    result = asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    assert result["status"] == "failed_before_publish"
    assert execution(world)["retry_count"] == 1
    assert execution(world)["error"] == "composer: Gallery not found"


def test_logged_out_puts_the_account_on_hold(world, monkeypatch, tmp_path):
    runner(monkeypatch, world, [], {"status": "blocked", "reason": "Facebook shows its login screen"})
    result = asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    assert result["status"] == "needs_review"
    config = json.loads((tmp_path / "profiles" / "phone_1" / "config.json").read_text())
    assert config["account"]["on_hold"]["since"]


def test_phone_not_ready_fails_before_publish_without_running(world, monkeypatch):
    monkeypatch.setattr(android_jobs.android_network, "open_account",
                        lambda pid, job=False: {"ready": False, "reason": "proxy check failed"})
    runner(monkeypatch, world, [], {"status": "published"})
    result = asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    assert result["status"] == "failed_before_publish"
    assert world["runs"] == []


def test_a_busy_phone_sends_the_job_back_to_pending(world, monkeypatch):
    runner(monkeypatch, world, [], {"status": "published"})
    android_devices.hold_for_job(SERIAL, "post:other")
    result = asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    assert result["status"] == "deferred"
    assert execution(world)["status"] == "pending"
    assert world["runs"] == []


def test_manual_switches_wait_for_the_queued_job(world):
    android_devices.hold_for_job(SERIAL, "post:e1")
    with pytest.raises(android_devices.AdbError):
        with android_devices.phone_lock(SERIAL):
            pass
    with android_devices.phone_lock(SERIAL, job=True):
        pass


def _published(world, tapped_seconds_ago=40):
    ex = execution(world)
    tapped = datetime.now(timezone.utc) - timedelta(seconds=tapped_seconds_ago)
    ex.update(status="published", first_comment_status="phone_pending", scheduler_lease=None,
              publish_tapped_at=tapped.isoformat(), phone_comment_due_at=tapped.isoformat())


def test_due_comment_runs_with_its_publish_time(world, monkeypatch):
    _published(world)
    runner(monkeypatch, world, [{"event": "about_to_comment"}], {"status": "published", "comment": "posted"})

    async def go():
        assert android_jobs.dispatch_comments(set()) == ["e1"]
        await asyncio.sleep(0.05)
    asyncio.run(go())
    args = world["runs"][0]
    assert args[args.index("--task") + 1] == "comment"
    assert 35 <= float(args[args.index("--published-ago") + 1]) <= 60
    # The comment's screenshots land in the post's own folder, which the job panel shows.
    assert args[args.index("--evidence-dir") + 1].endswith("/automation_evidence/e1")
    assert execution(world)["first_comment_status"] == "submitted_verified"
    assert android_devices.job_on(SERIAL) is None


def test_comment_waits_while_the_phone_is_busy(world, monkeypatch):
    _published(world)
    runner(monkeypatch, world, [], {"status": "published", "comment": "posted"})
    android_devices.hold_for_job(SERIAL, "post:other")
    assert android_jobs.dispatch_comments(set()) == []


def test_interrupted_comment_is_never_sent_again(world):
    _published(world)
    execution(world)["first_comment_status"] = "phone_commenting"
    assert android_jobs.dispatch_comments(set()) == []
    assert execution(world)["first_comment_status"] == "submission_pending"


def test_runner_dying_after_the_send_tap_is_unverified(world, monkeypatch):
    _published(world)
    runner(monkeypatch, world, [{"event": "about_to_comment"}], {})

    async def go():
        android_jobs.dispatch_comments(set())
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert execution(world)["first_comment_status"] == "submitted_unverified"


def test_not_found_comment_can_be_retried_at_any_age(world, monkeypatch):
    _published(world)
    execution(world)["first_comment_status"] = "failed_input_not_found"
    android_jobs.request_comment_retry("e1")
    assert execution(world)["first_comment_status"] == "phone_pending"
    runner(monkeypatch, world, [], {"status": "published", "comment": "posted"})

    async def go():
        android_jobs.dispatch_comments(set())
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert "--published-ago" not in world["runs"][0]


def warming_claim(state, options=None):
    post = state["queue"]["daily_batches"][0]["posts"][0]
    post.update(type="warming", media_file="", base_caption="", first_comment=None, warming_options=options or {"max_seconds": 60})
    return claim(state)


def test_a_warming_visit_completes_without_publishing_or_a_post_cooldown(world, monkeypatch):
    seen = {}

    async def fake(args, record, on_event):
        seen["args"] = args
        record["exit_code"] = 0
        return {"status": "completed", "warming_surface": "news_feed", "warming_surface_requested": "news_feed",
                "duration_seconds": 60, "scroll_actions": 9}
    monkeypatch.setattr(android_jobs, "_run_runner", fake)
    android_jobs._last_post_at.pop("phone_1", None)
    result = asyncio.run(android_jobs.execute_queue_phone(warming_claim(world)))
    ex = execution(world)
    assert result == {"success": True, "status": "completed"}
    assert ex["status"] == "completed" and ex["warming_surface"] == "news_feed" and ex["warming_duration_seconds"] == 60
    assert not ex.get("published_at")
    assert "--task" in seen["args"] and seen["args"][seen["args"].index("--task") + 1] == "warming"
    assert seen["args"][seen["args"].index("--profile") + 1] == "phone_1"
    assert "--media" not in seen["args"]
    assert world["opened"] == [("phone_1", True)]
    assert "phone_1" not in android_jobs._last_post_at
    assert android_devices.job_on(RUNTIME["device_serial"]) is None


def test_a_warming_visit_that_finds_the_account_stopped_holds_it(world, monkeypatch):
    held = []

    async def fake(args, record, on_event):
        return {"status": "blocked", "reason": "logged out", "account_blocked": True}
    monkeypatch.setattr(android_jobs, "_run_runner", fake)
    monkeypatch.setattr(android_jobs, "_put_on_hold", lambda pid, note: held.append(pid))
    asyncio.run(android_jobs.execute_queue_phone(warming_claim(world)))
    assert execution(world)["status"] == "needs_review" and held == ["phone_1"]


def test_a_warming_visit_waits_for_a_busy_phone(world, monkeypatch):
    async def fake(args, record, on_event):
        raise AssertionError("must not run")
    monkeypatch.setattr(android_jobs, "_run_runner", fake)
    android_devices.hold_for_job(RUNTIME["device_serial"], "post:other")
    result = asyncio.run(android_jobs.execute_queue_phone(warming_claim(world)))
    assert result["status"] == "deferred" and execution(world)["status"] == "pending"


def test_a_phone_that_could_not_open_fails_the_visit_without_running(world, monkeypatch):
    monkeypatch.setattr(android_jobs.android_network, "open_account", lambda pid, job=False: {"ready": False, "reason": "proxy down"})
    asyncio.run(android_jobs.execute_queue_phone(warming_claim(world)))
    assert execution(world)["status"] == "failed_before_publish" and "proxy down" in execution(world)["error"]
    assert android_devices.job_on(RUNTIME["device_serial"]) is None


def test_auto_visits_only_use_a_set_up_idle_connected_primary_phone(monkeypatch):
    serial = RUNTIME["device_serial"]
    config = {"runtime": RUNTIME, "android_setup": {"state": "done"}}
    store = {"devices": {serial: {"role": "primary"}}}

    class Client:
        connected = [{"serial": serial, "state": "device"}]

        def devices(self):
            return self.connected
    monkeypatch.setattr(android_devices, "_client", Client)
    monkeypatch.setattr(android_devices, "load_store", lambda: store)
    monkeypatch.setattr(android_devices, "_job_holders", {})
    monkeypatch.setattr(android_jobs.android_live, "_sessions", {})
    assert android_jobs.can_take_auto_visit(config)
    assert not android_jobs.can_take_auto_visit({"runtime": RUNTIME, "android_setup": {"state": "waiting_login"}})
    android_jobs.android_live._sessions[serial] = object()
    assert not android_jobs.can_take_auto_visit(config)
    android_jobs.android_live._sessions.clear()
    android_devices.hold_for_job(serial, "post:x")
    assert not android_jobs.can_take_auto_visit(config)
    android_devices.release_job(serial)
    store["devices"][serial]["role"] = "secondary"
    assert not android_jobs.can_take_auto_visit(config)
    store["devices"][serial]["role"] = "primary"
    Client.connected = [{"serial": serial, "state": "offline"}]
    assert not android_jobs.can_take_auto_visit(config)


def test_a_reel_comment_is_timed_from_when_the_upload_was_confirmed(world, monkeypatch):
    _published(world, tapped_seconds_ago=120)
    execution(world)["published_at"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    runner(monkeypatch, world, [], {"status": "published", "comment": "posted"})

    async def go():
        assert android_jobs.dispatch_comments(set()) == ["e1"]
        await asyncio.sleep(0.05)
    asyncio.run(go())
    args = world["runs"][0]
    assert 25 <= float(args[args.index("--published-ago") + 1]) <= 40
