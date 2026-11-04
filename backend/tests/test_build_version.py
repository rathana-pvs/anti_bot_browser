from backend.routers import system


def test_running_code_version_overrides_persistent_runtime(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    runtime = tmp_path / 'runtime'
    source.mkdir()
    runtime.mkdir()
    (source / 'VERSION').write_text('1.2.2\n')
    (runtime / 'VERSION').write_text('1.2.0\n')
    monkeypatch.setattr(system, 'BACKEND_DIR', source / 'backend')
    monkeypatch.setattr(system, 'ROOT_DIR', runtime)
    assert system.read_build_version() == '1.2.2'


def test_runtime_version_fallback_without_source_version(tmp_path, monkeypatch):
    (tmp_path / 'VERSION').write_text('1.2.0\n')
    monkeypatch.setattr(system, 'BACKEND_DIR', tmp_path / 'bundle' / 'backend')
    monkeypatch.setattr(system, 'ROOT_DIR', tmp_path)
    assert system.read_build_version() == '1.2.0'
