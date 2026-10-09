"""Resolve Live workflow data from installed versions or the matching code bundle."""
from pathlib import Path
import shutil
import tempfile
from .brain_runtime import BrainRegistry, load_brain_package


def install_bundled_live_brain(code_automation: Path, runtime_automation: Path):
    """Seed a new workflow family without replacing installed user versions."""
    target = runtime_automation / 'brains' / 'facebook_live'
    if target.exists():
        return False
    source = code_automation / 'brains' / 'facebook_live'
    load_brain_package(source / 'bundled_default', expected_id='facebook_live')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.live-install-', dir=target.parent) as temporary:
        staged = Path(temporary) / 'facebook_live'
        shutil.copytree(source, staged)
        try:
            staged.rename(target)
        except FileExistsError:
            return False
    return True


def resolve_live_brain(code_automation: Path, runtime_automation: Path):
    installed = runtime_automation / 'brains'
    bundled = code_automation / 'brains'
    root = installed if (installed / 'facebook_live').is_dir() else bundled
    return BrainRegistry(root).resolve('facebook_live')


def pinned_live_brain(path, digest):
    package = load_brain_package(path, expected_id='facebook_live')
    if package.digest != digest:
        raise RuntimeError('Live workflow changed during this session; review before continuing')
    return package
