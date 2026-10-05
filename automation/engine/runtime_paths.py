"""Runtime paths shared by the backend and standalone automation workers."""

import os
from pathlib import Path


def runtime_root(default_root: Path | None = None) -> Path:
    configured = os.environ.get("AUTOMAT_FB_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (default_root or Path(__file__).resolve().parents[2]).resolve()


def profiles_dir() -> Path:
    return runtime_root() / "profiles"


def automation_dir() -> Path:
    return runtime_root() / "automation"
