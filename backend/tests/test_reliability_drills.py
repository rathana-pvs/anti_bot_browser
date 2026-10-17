"""Offline failure-injection drills. These tests never launch a browser."""

from datetime import datetime, timedelta, timezone
import json

from backend.idempotency import IdempotencyStore
from backend.services import queue_service
from backend.worker.journal import Journal
from backend.worker.protocol import Message, envelope


def test_crash_after_acceptance_preserves_attempt_and_outbox(tmp_path):
    path = tmp_path / "journal.sqlite3"
    message = Message.model_validate(envelope(
        "job_offer", {}, attempt_id="attempt-1", lease_token="token-1",
    ))
    journal = Journal(path)
    journal.accept(message, {"task": "photo"}, "local-1", "profile-1")
    journal.close()  # Simulated abrupt process boundary before acknowledgement.

    reopened = Journal(path)
    assert reopened.get("attempt-1")["state"] == "accepted"
    assert reopened.pending()[0]["type"] == "job_accept"
    reopened.close()


def test_corrupt_primary_recovers_without_promoting_corruption(tmp_path, monkeypatch):
    queue_file = tmp_path / "posting_queue.json"
    safe = {"queue_version": "2.0", "daily_batches": [{"batch_id": "safe"}]}
    queue_file.write_text("{corrupt", encoding="utf-8")
    queue_file.with_suffix(".json.bak").write_text(json.dumps(safe), encoding="utf-8")
    monkeypatch.setattr(queue_service, "QUEUE_FILE", queue_file)
    assert queue_service.load_posting_queue() == safe
    assert json.loads(queue_file.with_suffix(".json.bak").read_text()) == safe


def test_stale_inflight_idempotency_claim_can_be_recovered(tmp_path):
    store = IdempotencyStore(tmp_path / "keys.sqlite3", pending_seconds=1)
    assert store.claim("key", "fingerprint")[0] == "new"
    with store._connect() as db:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()
        db.execute("UPDATE api_idempotency SET updated_at=? WHERE key='key'", (stale,))
    assert store.claim("key", "fingerprint")[0] == "new"

