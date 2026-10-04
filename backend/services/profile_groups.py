"""Persist empty profile groups independently of profile membership."""
import json
import threading

from backend.config import DATA_DIR, PROFILES_DIR
from backend.services.profile_service import atomic_write_json

GROUPS_FILE = DATA_DIR / "profile_groups.json"
_groups_lock = threading.Lock()


def list_groups() -> list[str]:
    names = []
    if GROUPS_FILE.exists():
        names = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))["groups"]
    # Keep membership created by older versions available in group pickers.
    for path in PROFILES_DIR.glob("*/config.json"):
        try:
            name = json.loads(path.read_text(encoding="utf-8")).get("group", "")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        except (OSError, ValueError):
            continue
    return sorted(set(names), key=str.casefold)


def create_group(name: str) -> str:
    with _groups_lock:
        names = list_groups()
        if any(existing.casefold() == name.casefold() for existing in names):
            raise ValueError("A group with this name already exists")
        atomic_write_json(GROUPS_FILE, {"groups": sorted([*names, name], key=str.casefold)})
        return name
