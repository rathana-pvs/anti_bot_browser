import shutil
import zipfile
import json

import pytest

from backend.services.queue_service import create_support_archive


def test_global_support_archive_is_created_and_readable():
    archive_path, filename, temp_root = create_support_archive(
        queue={"queue_version": 1, "daily_batches": []},
        description="Download regression test",
    )
    try:
        assert filename.startswith("support_global_")
        assert filename.endswith(".zip")
        assert archive_path.is_file()
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            members = archive.namelist()
            assert any(name.endswith("/report.json") for name in members)
            assert any(name.endswith("/system_info.json") for name in members)
            assert any(name.endswith("/README.txt") for name in members)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def batch_queue(count=5):
    states = ['published', 'failed_before_publish', 'uncertain', 'skipped', 'running']
    return {'daily_batches': [
        {'batch_id': 'batch_report', 'name': 'All outcomes', 'posts': [
            {'post_id': 'post_1', 'type': 'reel', 'base_caption': 'Private caption', 'executions': [
                {'execution_id': f'exec_{index}', 'profile_id': 'profile_001',
                 'status': states[index % len(states)], 'stage': 'verifying',
                 'error': 'Example failure' if index % 5 == 1 else None,
                 'logs': ['Readiness checked', 'password=private-password',
                          'rtmps://live-api-s.facebook.com/rtmp/PRIVATE-STREAM'],
                 'stage_history': [{'stage': 'verifying', 'timestamp': '2026-11-03T01:00:00+00:00'}]}
                for index in range(count)]}]},
        {'batch_id': 'other_batch', 'posts': [{'type': 'photo', 'executions': [
            {'execution_id': 'other_execution', 'status': 'published'}]}]},
    ]}


@pytest.mark.parametrize('include_content', [False, True])
def test_batch_archive_includes_every_outcome_logs_and_no_other_batch(include_content):
    archive_path, _, temp_root = create_support_archive(
        queue=batch_queue(55), batch_id='batch_report', include_content=include_content,
        description='Please check this batch',
    )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            root = archive.namelist()[0].split('/')[0]
            report = json.loads(archive.read(f'{root}/report.json'))
            assert report['report_type'] == 'batch'
            assert report['counts']['total'] == 55  # No global-summary 50-item truncation.
            assert report['counts']['by_status'] == dict.fromkeys(
                ['published', 'failed_before_publish', 'uncertain', 'skipped', 'running'], 11)
            assert len(report['executions']) == 55
            assert len([name for name in archive.namelist() if name.endswith('/logs.txt')]) == 55
            assert len([name for name in archive.namelist() if '/executions/' in name and name.endswith('/report.json')]) == 55
            assert 'published: 11' in archive.read(f'{root}/summary.txt').decode()
            text = '\n'.join(archive.read(name).decode() for name in archive.namelist())
            assert 'other_execution' not in text
            assert 'private-password' not in text
            assert 'PRIVATE-STREAM' not in text
            assert ('Private caption' in text) is include_content
            assert 'Readiness checked' in text
            assert report['executions'][0]['evidence']['availability'] == 'not_found'
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


@pytest.mark.parametrize('screenshots', [False, True])
def test_batch_archive_collects_success_and_failure_evidence_but_not_browser_data(tmp_path, monkeypatch, screenshots):
    from backend.services import queue_service
    profiles = tmp_path / 'profiles'
    evidence = profiles / 'profile_001' / 'automation_evidence' / 'run'
    evidence.mkdir(parents=True)
    (evidence / 'telemetry.json').write_text(json.dumps({'stage': 'published', 'stream_key': 'PRIVATE-KEY'}))
    (evidence / 'success.png').write_bytes(b'success screenshot')
    (evidence / 'error.png').write_bytes(b'error screenshot')
    outside = tmp_path / 'cookies.json'
    outside.write_text('{"private": "DO-NOT-EXPORT"}')
    (evidence / 'linked.json').symlink_to(outside)
    (evidence / 'linked.png').symlink_to(outside)
    browser = profiles / 'profile_001' / 'chrome_data'
    browser.mkdir()
    (browser / 'secrets.json').write_text(outside.read_text())
    monkeypatch.setattr(queue_service, 'PROFILES_DIR', profiles)
    queue = batch_queue()
    executions = queue['daily_batches'][0]['posts'][0]['executions']
    executions[0]['evidence_dir'] = str(evidence)
    executions[1]['evidence_dir'] = str(evidence)
    executions[2]['evidence_dir'] = str(browser)
    archive_path, _, temp_root = create_support_archive(queue=queue, batch_id='batch_report', include_evidence=screenshots)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.namelist()
            assert len([name for name in members if name.endswith('/telemetry.json')]) == 2
            assert len([name for name in members if name.endswith('/success.png')]) == (2 if screenshots else 0)
            assert len([name for name in members if name.endswith('/error.png')]) == (2 if screenshots else 0)
            assert not any('linked.' in name or 'chrome_data' in name for name in members)
            for name in members:
                assert b'DO-NOT-EXPORT' not in archive.read(name)
                assert b'PRIVATE-KEY' not in archive.read(name)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_batch_support_route_rejects_unknown_batch(monkeypatch):
    from backend.routers import support
    from fastapi import BackgroundTasks, HTTPException
    monkeypatch.setattr(support, 'load_posting_queue', batch_queue)
    with pytest.raises(HTTPException) as caught:
        support.export_batch_support('missing', BackgroundTasks(), {})
    assert caught.value.status_code == 404


def test_batch_support_route_passes_options_and_cleans_up(monkeypatch):
    from backend.routers import support
    from fastapi import BackgroundTasks
    monkeypatch.setattr(support, 'load_posting_queue', batch_queue)
    tasks = BackgroundTasks()
    response = support.export_batch_support('batch_report', tasks, {'include_content': True, 'include_evidence': True})
    try:
        with zipfile.ZipFile(response.path) as archive:
            root = archive.namelist()[0].split('/')[0]
            report = json.loads(archive.read(f'{root}/report.json'))
            assert report['privacy']['content_included'] is True
            assert report['privacy']['screenshots_included'] is True
        assert tasks.tasks[0].func is support.cleanup_temp_dir
    finally:
        support.cleanup_temp_dir(tasks.tasks[0].args[0])


def test_execution_export_still_includes_logs_and_comment_diagnosis():
    queue = batch_queue()
    batch = queue['daily_batches'][0]
    post = batch['posts'][0]
    execution = post['executions'][0]
    execution.update(first_comment_status='failed_input_not_found', first_comment_note='Editor not found',
                     review_status='resolved_published')
    archive_path, _, temp_root = create_support_archive(
        queue=queue, match={'batch': batch, 'post': post, 'execution': execution})
    try:
        with zipfile.ZipFile(archive_path) as archive:
            root = archive.namelist()[0].split('/')[0]
            report = json.loads(archive.read(f'{root}/report.json'))
            assert report['report_type'] == 'execution'
            assert report['execution']['status'] == 'published'
            assert report['execution']['first_comment_status'] == 'failed_input_not_found'
            assert report['execution']['first_comment_note'] == 'Editor not found'
            assert report['execution']['review_status'] == 'resolved_published'
            assert b'Readiness checked' in archive.read(f'{root}/logs.txt')
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
