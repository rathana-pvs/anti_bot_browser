import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.runtime_paths import automation_dir, profiles_dir, runtime_root
from engine.behavior_profile import load_behavior_session
from tasks.base_task import BaseTask
from tasks.facebook_post import FacebookPostTask
from tasks.facebook_reel import FacebookReelTask


def test_default_and_configured_root_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv('AUTOMAT_FB_ROOT', raising=False)
    assert runtime_root() == Path(__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AUTOMAT_FB_ROOT', '  runtime  ')
    assert runtime_root() == tmp_path / 'runtime'
    monkeypatch.setenv('AUTOMAT_FB_ROOT', '~/runtime')
    assert runtime_root() == Path.home() / 'runtime'


def test_profile_settings_behavior_cache_and_evidence_use_configured_root(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    profile_id = 'root_override_test'
    directory = tmp_path / 'profiles' / profile_id
    directory.mkdir(parents=True)
    config = {
        'behavior_mode': 'slow', 'behavior_seed': 456,
        'fingerprint': {'screen_resolution': '1366x768', 'language': 'en-US'},
    }
    (directory / 'config.json').write_text(json.dumps(config))
    task = BaseTask(profile_id)
    assert task.profile_config == config
    assert task.behavior_session.mode == 'slow'
    assert Path(task.vision.cache_file) == directory / 'element_cache.json'
    task.vision._cache['test'] = {'x': 12}
    task.vision._save_cache()
    assert json.loads((directory / 'element_cache.json').read_text())['test']['x'] == 12
    evidence_path = task.evidence.capture('root', np.zeros((10, 10, 3), dtype=np.uint8))
    assert Path(evidence_path).is_relative_to(directory / 'automation_evidence')
    assert task.vision.configured_resolution == '1366x768'
    assert profiles_dir() == tmp_path / 'profiles'
    assert automation_dir() == tmp_path / 'automation'


def test_explicit_behavior_root_still_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path / 'other'))
    directory = tmp_path / 'custom_profiles' / 'p1'
    directory.mkdir(parents=True)
    (directory / 'config.json').write_text(json.dumps({'behavior_mode': 'fast'}))
    assert load_behavior_session('p1', 'run', profiles_root=directory.parent).mode == 'fast'


def test_composer_packages_load_from_runtime_instead_of_source(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOMAT_FB_ROOT', str(tmp_path))
    registries = []

    class Registry:
        def __init__(self, path):
            registries.append(path)
        def load(self, *_args):
            return {}

    monkeypatch.setattr(BaseTask, '__init__', lambda self, *_args: None)
    monkeypatch.setattr('tasks.facebook_post.ComposerTemplateRegistry', Registry)
    monkeypatch.setattr('tasks.facebook_reel.ComposerTemplateRegistry', Registry)
    monkeypatch.setattr('tasks.facebook_reel.RecognitionProfileRegistry', Registry)
    FacebookPostTask('p1', caption='text')
    FacebookReelTask('p1', video_path='video.mp4', caption='text')
    brains = tmp_path / 'automation' / 'brains'
    assert registries == [
        brains / 'facebook_post' / 'bundled_default',
        brains / 'facebook_reel' / 'bundled_default',
        brains / 'facebook_reel' / 'bundled_default' / 'routing' / 'entry.yaml',
    ]


def test_fresh_process_uses_same_root_for_backend_and_cli(tmp_path):
    source = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'AUTOMAT_FB_ROOT': str(tmp_path), 'PYTHONPATH': str(source)}
    script = '''
import sys
from pathlib import Path
from backend.config import ROOT_DIR, PROFILES_DIR, BRAINS_DIR
sys.path.insert(0, str(Path.cwd() / 'automation'))
from engine.runtime_paths import runtime_root, profiles_dir, automation_dir
from brain_cli import DEFAULT_BRAINS_ROOT
from runner import BRAINS_ROOT
assert ROOT_DIR == runtime_root()
assert PROFILES_DIR == profiles_dir()
assert BRAINS_DIR == automation_dir() / 'brains' == DEFAULT_BRAINS_ROOT == BRAINS_ROOT
'''
    subprocess.run([sys.executable, '-c', script], cwd=source, env=env, check=True, timeout=20)


def test_backend_workers_receive_resolved_root(tmp_path, monkeypatch):
    from backend.services import docker_service
    monkeypatch.setenv('AUTOMAT_FB_ROOT', 'relative/runtime')
    monkeypatch.setattr(docker_service, 'ROOT_DIR', tmp_path)
    monkeypatch.setattr(docker_service, 'get_scheduler_config', lambda: {'max_total_automation_tasks': 2})
    assert docker_service.automation_worker_env()['AUTOMAT_FB_ROOT'] == str(tmp_path)
