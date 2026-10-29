"""Viewing an execution's evidence screenshots stays inside its own profile folder."""

from __future__ import annotations

import pytest
import json
from fastapi import HTTPException

from backend.routers import queue as queue_router
from backend.services import queue_service
from automation.engine import evidence as evidence_module


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


def interrupted_run(evidence):
    execution, run = evidence
    actual = run.with_name('20260101T000000_FacebookReelTask')
    run.rename(actual)
    execution['evidence_dir'] = None
    execution['container_owner_token'] = 'owner-1'
    execution['last_scheduler_lease'] = {'lease_id': 'lease-1'}
    execution['stage_history'] = [
        {'stage': 'running', 'reason': 'publisher_scheduler_lease_claimed'},
        {'stage': 'preparing', 'timestamp': '2026-01-01T07:00:00.123499'},
        {'stage': 'composing', 'timestamp': '2026-01-01T07:00:10.123499'},
    ]
    history = [dict(entry, timestamp=entry['timestamp'].replace('499', '400'))
               for entry in execution['stage_history'][1:]]
    (actual / 'stage.json').write_text(json.dumps({'history': history}))
    queue_router.load_posting_queue()['daily_batches'][0]['posts'][0]['type'] = 'reel'
    return execution, actual


def test_interrupted_attempt_displays_its_saved_screenshots_without_folder_link(evidence):
    execution, run = interrupted_run(evidence)
    assert queue_router.list_execution_evidence('exec-1')['images'] == ['01_composer.png', '02_post_clicked.png']
    assert str(queue_router.get_execution_evidence_image('exec-1', '02_post_clicked.png').path) == str(run / '02_post_clicked.png')
    assert execution['evidence_dir'] is None  # Viewing never changes job outcomes or queue state.


@pytest.mark.parametrize('case', ['wrong_time', 'only_one_stage', 'previous_attempt', 'ambiguous', 'wrong_type', 'corrupt'])
def test_discovery_never_substitutes_an_unmatched_run(evidence, case):
    execution, run = interrupted_run(evidence)
    if case == 'wrong_time':
        execution['stage_history'][1]['timestamp'] = '2026-01-01T07:05:00'
    elif case == 'only_one_stage':
        execution['stage_history'].pop()
    elif case == 'previous_attempt':
        execution['stage_history'].append({'stage': 'running', 'reason': 'publisher_scheduler_lease_claimed'})
    elif case == 'ambiguous':
        duplicate = run.with_name('20260101T000001_FacebookReelTask')
        duplicate.mkdir()
        (duplicate / 'stage.json').write_bytes((run / 'stage.json').read_bytes())
    elif case == 'wrong_type':
        run.rename(run.with_name('20260101T000000_FacebookPreparationTask'))
    else:
        (run / 'stage.json').write_text('{broken')
    assert queue_router.list_execution_evidence('exec-1') == {'images': []}


@pytest.mark.parametrize('correct', [True, False])
def test_explicit_attempt_identity_overrides_legacy_stage_matching(evidence, correct):
    execution, run = interrupted_run(evidence)
    (run / 'attempt.json').write_text(json.dumps({
        'execution_id': 'exec-1', 'lease_id': 'lease-1' if correct else 'previous-lease',
        'owner_token': 'owner-1',
    }))
    if correct:
        execution['stage_history'] = []
    assert bool(queue_router.list_execution_evidence('exec-1')['images']) is correct


def test_first_screenshot_link_is_persisted_only_for_its_own_profile_and_lease(evidence, tmp_path, monkeypatch):
    execution, run = evidence
    execution['scheduler_lease'] = {'lease_id': 'lease-1'}
    monkeypatch.setattr(queue_service, 'PROFILES_DIR', tmp_path)
    monkeypatch.setattr(queue_service, 'QUEUE_FILE', tmp_path / 'queue.json')
    queue_service.save_posting_queue(queue_router.load_posting_queue())
    screenshot = str(run / '01_composer.png')
    assert not queue_service.persist_queue_execution_evidence('exec-1', 'wrong-lease', screenshot)
    assert not queue_service.persist_queue_execution_evidence('exec-1', 'lease-1', str(tmp_path / 'secret.png'))
    assert queue_service.persist_queue_execution_evidence('exec-1', 'lease-1', screenshot)
    assert queue_service.find_queue_execution(queue_service.load_posting_queue(), 'exec-1')['execution']['evidence_dir'] == str(run)


def test_new_run_records_durable_attempt_identity_before_any_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_module, 'profiles_dir', lambda: tmp_path)
    context = {'execution_id': 'exec-1', 'lease_id': 'lease-1', 'owner_token': 'owner-1'}
    monkeypatch.setenv('AUTOMATION_EVIDENCE_CONTEXT', json.dumps(context))
    recorder = evidence_module.EvidenceRecorder('profile-1', 'FacebookReelTask')
    from pathlib import Path
    assert json.loads((Path(recorder.directory) / 'attempt.json').read_text()) == context
