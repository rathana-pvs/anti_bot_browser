"""Prevent mixed code/data roots from using a stale cloud-unsafe runner."""
from backend.config import automation_runner


def test_worker_runner_uses_matching_code_with_separate_runtime(tmp_path):
    code = tmp_path / 'code'
    runtime = tmp_path / 'installed-data'
    for root in (code, runtime):
        (root / 'automation').mkdir(parents=True)
        (root / 'automation' / 'runner.py').write_text('# runner')
    assert automation_runner(code, runtime) == code / 'automation' / 'runner.py'
    # Packaged backends without an adjacent Python runner use their runtime bundle.
    (code / 'automation' / 'runner.py').unlink()
    assert automation_runner(code, runtime) == runtime / 'automation' / 'runner.py'


def test_catalog_contains_only_assigned_profile_display_metadata(tmp_path, monkeypatch):
    import json
    from backend.worker import adapter
    from backend.worker.config import WorkerConfig
    from backend.services import profile_groups
    profile = tmp_path / 'profile-1'
    profile.mkdir()
    (profile / 'config.json').write_text(json.dumps({'name': 'Account', 'group': 'Build', 'network': {'proxy_pass': 'secret'}, 'account': {'email': 'private'}}))
    monkeypatch.setattr(adapter, 'PROFILES_DIR', tmp_path)
    monkeypatch.setattr(profile_groups, 'list_groups', lambda: ['Empty', 'Build'])
    config = WorkerConfig(server_url='wss://server.example/ws/workers',worker_id='w',credential='secret',account_profiles={'account-1': 'profile-1'})
    catalog = adapter.QueueAdapter(config).profile_catalog()
    assert catalog == {'groups': ['Build', 'Empty'], 'accounts': [{'account_id': 'account-1', 'name': 'Account', 'group': 'Build'}]}
    assert 'secret' not in json.dumps(catalog) and 'private' not in json.dumps(catalog)
