"""The backend's adaptive resource signal, read before safe steps.

Missing, unreadable or stale signals are neutral, as is any run started
outside the manager (no AUTOMAT_FB_ROOT), so automation never waits on a
backend that is not there.
"""

import json
import os
import time
from pathlib import Path

NEUTRAL = {"hold": False, "elastic_scale": 1.0, "publish_slots_full": False, "reason": ""}
MAX_AGE_SECONDS = 30.0


def read_signal(path: Path | None = None, now=time.time) -> dict:
    if path is None:
        root = os.environ.get("AUTOMAT_FB_ROOT", "").strip()
        if not root:
            return dict(NEUTRAL)
        path = Path(root).expanduser() / "data" / "resources" / "signal.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or now() - float(value.get("updated_at", 0)) > MAX_AGE_SECONDS:
            return dict(NEUTRAL)
        scale = float(value.get("elastic_scale", 1.0))
    except (OSError, ValueError, TypeError):
        return dict(NEUTRAL)
    return {
        "hold": value.get("hold") is True,
        "elastic_scale": min(1.0, max(0.3, scale)),
        "publish_slots_full": value.get("publish_slots_full") is True,
        "reason": str(value.get("reason") or ""),
    }
