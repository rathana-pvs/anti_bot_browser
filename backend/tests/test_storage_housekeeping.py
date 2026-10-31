"""Housekeeping frees space without touching evidence that may still be needed."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from backend.services import storage_housekeeping as housekeeping
from backend.services.evidence_cleanup import clean_profile_evidence

NOW = time.time()
DAY = 86400


def make_run(root, profile, days_old, execution_id, status, files=("01_login_check.png", "09_before_publish.png")):
    stamp = datetime.fromtimestamp(NOW - days_old * DAY, tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    run = root / profile / "automation_evidence" / f"{stamp}_{abs(hash(execution_id)) % 10**6:06d}Z_FacebookPostTask"
    run.mkdir(parents=True)
    (run / "attempt.json").write_text(json.dumps({"execution_id": execution_id}))
    (run / "result.json").write_text(json.dumps({"status": status}))
    for name in files:
        Image.new("RGB", (1920, 1080), (200, 30, 30)).save(run / name)
    return run


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    root.mkdir()
    monkeypatch.setattr(housekeeping, "PROFILES_DIR", root)
    monkeypatch.setattr(housekeeping, "KEEP_NEWEST_RUNS", 0)
    return root


def queue_with(*executions):
    return {"daily_batches": [{"batch_id": "b", "posts": [{"type": "photo", "executions": list(executions)}]}]}


def test_evidence_that_may_still_be_needed_is_never_removed(profiles):
    unconfirmed = make_run(profiles, "profile_001", 60, "e_uncertain", "uncertain")
    attention = make_run(profiles, "profile_001", 60, "e_check", "skipped_auth_required")
    queue = queue_with({"execution_id": "e_uncertain", "status": "uncertain"})
    protected = housekeeping.protected_execution_ids(queue, {"profile_001": {"evidence": [{"execution_id": "e_check"}]}})
    report = housekeeping.clean_evidence(queue, protected, NOW)
    assert unconfirmed.exists() and attention.exists()
    assert report["kept_protected"] == 2 and report["runs_removed"] == 0
    assert len(list(unconfirmed.glob("*.png"))) == 2


def test_runs_age_out_by_what_they_prove(profiles):
    old_post = make_run(profiles, "profile_001", 31, "e1", "published")
    recent_post = make_run(profiles, "profile_001", 5, "e2", "published")
    old_failure = make_run(profiles, "profile_001", 8, "e3", "failed_before_publish")
    week_post = make_run(profiles, "profile_001", 2, "e4", "published")
    queue = queue_with(*({"execution_id": f"e{i}", "status": s} for i, s in
                         ((1, "published"), (2, "published"), (3, "failed_before_publish"), (4, "published"))))
    housekeeping.clean_evidence(queue, set(), NOW)
    assert not old_post.exists() and not old_failure.exists()
    # After three days a published post keeps only its proof screenshots.
    assert [png.name for png in recent_post.glob("*.png")] == ["09_before_publish.png"]
    assert len(list(week_post.glob("*.png"))) == 2


def test_older_screenshots_are_shrunk_in_place_once(profiles):
    run = make_run(profiles, "profile_001", 2, "e1", "completed")
    before = sum(png.stat().st_size for png in run.glob("*.png"))
    housekeeping.clean_evidence(queue_with({"execution_id": "e1", "status": "completed"}), set(), NOW)
    with Image.open(run / "01_login_check.png") as image:
        assert image.width == housekeeping.SHRINK_WIDTH
    assert sum(png.stat().st_size for png in run.glob("*.png")) < before
    assert (run / housekeeping.SHRUNK_MARKER).exists()
    report = housekeeping.clean_evidence(queue_with({"execution_id": "e1", "status": "completed"}), set(), NOW)
    assert report["screenshots_shrunk"] == 0


def test_each_account_keeps_its_newest_runs(profiles, monkeypatch):
    monkeypatch.setattr(housekeeping, "KEEP_NEWEST_RUNS", 1)
    newest = make_run(profiles, "profile_001", 40, "e1", "failed_before_publish")
    older = make_run(profiles, "profile_001", 50, "e2", "failed_before_publish")
    housekeeping.clean_evidence({"daily_batches": []}, set(), NOW)
    assert newest.exists() and not older.exists()


def test_a_preview_changes_nothing(profiles):
    run = make_run(profiles, "profile_001", 40, "e1", "failed_before_publish")
    report = housekeeping.clean_evidence({"daily_batches": []}, set(), NOW, dry_run=True)
    assert report["runs_removed"] == 1 and run.exists()


def cache(profiles, profile, files, size):
    folder = profiles / profile / "chrome_data" / "Default" / "Cache" / "Cache_Data"
    folder.mkdir(parents=True)
    (folder / "index").write_bytes(b"x")
    for index in range(files):
        path = folder / f"{index:04x}_0"
        path.write_bytes(b"x" * size)
        os.utime(path, (NOW - (files - index) * 60, NOW - (files - index) * 60))
    return folder


def test_chrome_caches_are_trimmed_only_while_the_browser_is_stopped(profiles, monkeypatch):
    monkeypatch.setattr(housekeeping, "CACHE_TRIM_ABOVE", 1000)
    monkeypatch.setattr(housekeeping, "CACHE_TRIM_TO", 500)
    stopped = cache(profiles, "profile_001", 20, 100)
    running = cache(profiles, "profile_002", 20, 100)
    monkeypatch.setattr(housekeeping, "_browser_stopped", lambda pid: pid == "profile_001")
    report = housekeeping.trim_chrome_caches()
    left = sorted(path.name for path in stopped.iterdir())
    assert "index" in left and len(left) == 6  # the five newest entries and the index
    assert "0013_0" in left and "0000_0" not in left
    assert len(list(running.iterdir())) == 21 and report["skipped_running"] == 1


def test_only_old_finished_batches_are_archived(tmp_path, monkeypatch):
    from backend.services import account_health, queue_service
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    queue = {"daily_batches": [
        {"batch_id": "done", "created_at": old, "posts": [{"executions": [{"execution_id": "a", "status": "published", "published_at": old}]}]},
        {"batch_id": "open", "created_at": old, "posts": [{"executions": [{"execution_id": "b", "status": "uncertain", "ended_at": old}]}]},
        {"batch_id": "new", "posts": [{"executions": [{"execution_id": "c", "status": "published", "published_at": datetime.now(timezone.utc).isoformat()}]}]},
    ]}
    saved, synced = [], []
    monkeypatch.setattr(housekeeping, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(queue_service, "load_posting_queue", lambda: queue)
    monkeypatch.setattr(queue_service, "save_posting_queue", lambda value: saved.append(value) or True)
    monkeypatch.setattr(queue_service, "with_queue_claim_lock", lambda callback: callback())
    monkeypatch.setattr(account_health, "sync_history", lambda q, now: synced.append(1))
    report = housekeeping.archive_queue(datetime.now(timezone.utc))
    assert report["batches_archived"] == 1 and synced == [1]
    assert [batch["batch_id"] for batch in saved[0]["daily_batches"]] == ["open", "new"]
    archived = json.loads(next((tmp_path / "archive").glob("*.json")).read_text())
    assert archived["batches"][0]["batch_id"] == "done"


def test_runtime_backups_go_only_after_the_new_runtime_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(housekeeping, "DATA_DIR", tmp_path)
    backup = tmp_path / "python-runtime" / "backups" / "venv-old"
    backup.mkdir(parents=True)
    (backup / "file").write_bytes(b"x" * 10)
    os.utime(backup, (NOW - 5 * DAY, NOW - 5 * DAY))
    assert housekeeping.remove_old_runtime_backups(NOW)["backups_removed"] == 0
    (tmp_path / "runtime-installed-version").write_text("1")
    assert housekeeping.remove_old_runtime_backups(NOW)["backups_removed"] == 1 and not backup.exists()


def test_a_nearly_full_disk_stops_new_queue_browsers(monkeypatch):
    from backend.services import docker_service
    monkeypatch.setattr(housekeeping, "disk_free", lambda path=None: 2 * housekeeping.GB)
    assert docker_service.claim_container_start_admission() == {"allowed": False, "reason": "low_disk_space"}


def test_a_low_disk_runs_housekeeping_early(monkeypatch):
    monkeypatch.setattr(housekeeping, "read_state", lambda: {"ran_at": datetime.fromtimestamp(NOW - 7200, tz=timezone.utc).isoformat()})
    monkeypatch.setattr(housekeeping, "disk_free", lambda path=None: 50 * housekeeping.GB)
    assert housekeeping.due(NOW) is None
    monkeypatch.setattr(housekeeping, "disk_free", lambda path=None: 8 * housekeeping.GB)
    assert housekeeping.due(NOW) == "low_disk"


def test_manual_evidence_cleanup_keeps_protected_runs(profiles):
    kept = make_run(profiles, "profile_001", 10, "e_uncertain", "uncertain")
    removed = make_run(profiles, "profile_001", 10, "e_done", "published")
    clean_profile_evidence(profiles / "profile_001", days_threshold=3, protected={"e_uncertain"})
    assert kept.exists() and not removed.exists()


def test_a_background_pass_reports_progress_to_the_end(monkeypatch):
    import threading
    seen = []
    release = threading.Event()

    def fake_step(name):
        def step(*args, **kwargs):
            seen.append((name, housekeeping.progress()["label"], housekeeping.progress()["percent"]))
            if name == "evidence":
                release.wait(2)
            return {"freed_bytes": 10}
        return step

    from backend.services import account_health, queue_service
    monkeypatch.setattr(queue_service, "load_posting_queue", lambda: {"daily_batches": []})
    monkeypatch.setattr(account_health, "_profile_configs", lambda: {})
    monkeypatch.setattr(account_health, "sync_history", lambda q, now: {})
    monkeypatch.setattr(housekeeping, "atomic_write_json", lambda path, value: None)
    monkeypatch.setattr(housekeeping, "usage", lambda: {})
    for name, function in (("evidence", "clean_evidence"), ("chrome_cache", "trim_chrome_caches"), ("queue", "archive_queue"),
                           ("runtime_backups", "remove_old_runtime_backups"), ("logs", "rotate_logs")):
        monkeypatch.setattr(housekeeping, function, fake_step(name))
    assert housekeeping.start()
    assert housekeeping.progress()["running"] and not housekeeping.start()
    release.set()
    for _ in range(100):
        if not housekeeping.progress()["running"]:
            break
        time.sleep(0.02)
    final = housekeeping.progress()
    assert final["percent"] == 100.0 and final["freed_bytes"] == 50 and final["result"]["freed_bytes"] == 50
    assert [label for _name, label, _percent in seen] == ["Evidence screenshots", "Browser caches", "Queue archive",
                                                          "Old runtime backup", "Logs"]
    assert [percent for *_rest, percent in seen] == sorted(percent for *_rest, percent in seen)
