"""Viewing an execution's evidence screenshots stays inside its own profile folder."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.routers import queue as queue_router


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    run = tmp_path / "profile-1" / "automation_evidence" / "20260101T000000_reel"
    run.mkdir(parents=True)
    (run / "01_composer.png").write_bytes(b"png-1")
    (run / "02_post_clicked.png").write_bytes(b"png-2")
    (run / "result.json").write_text("{}")
    (tmp_path / "secret.png").write_bytes(b"secret")
    execution = {"execution_id": "exec-1", "profile_id": "profile-1", "evidence_dir": str(run)}
    queue = {"daily_batches": [{"posts": [{"executions": [execution]}]}]}
    monkeypatch.setattr(queue_router, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(queue_router, "load_posting_queue", lambda: queue)
    return execution, run


def test_lists_only_png_screenshots_in_order(evidence):
    assert queue_router.list_execution_evidence("exec-1") == {"images": ["01_composer.png", "02_post_clicked.png"]}


def test_serves_a_screenshot(evidence):
    response = queue_router.get_execution_evidence_image("exec-1", "02_post_clicked.png")
    assert response.media_type == "image/png"
    assert str(response.path).endswith("02_post_clicked.png")


@pytest.mark.parametrize("filename", ["../../secret.png", "result.json", "missing.png"])
def test_rejects_files_outside_or_unknown(evidence, filename):
    with pytest.raises(HTTPException) as error:
        queue_router.get_execution_evidence_image("exec-1", filename)
    assert error.value.status_code in (400, 404)


def test_ignores_evidence_dir_outside_the_profile(evidence, tmp_path):
    execution, _run = evidence
    execution["evidence_dir"] = str(tmp_path)
    assert queue_router.list_execution_evidence("exec-1") == {"images": []}
    execution["evidence_dir"] = str(tmp_path / "profile-1" / "automation_evidence")
    assert queue_router.list_execution_evidence("exec-1") == {"images": []}


def test_unknown_execution_is_not_found(evidence):
    with pytest.raises(HTTPException) as error:
        queue_router.list_execution_evidence("nope")
    assert error.value.status_code == 404
