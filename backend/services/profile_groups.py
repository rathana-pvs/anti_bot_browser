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


def _registered_groups() -> list[str]:
    if not GROUPS_FILE.exists():
        return []
    return json.loads(GROUPS_FILE.read_text(encoding="utf-8"))["groups"]


def _reassign_members(old: str, new: str) -> int:
    """Point every account in folder `old` at `new` ("" means no folder). Returns how many moved."""
    moved = 0
    for path in PROFILES_DIR.glob("*/config.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = config.get("group", "")
        if isinstance(name, str) and name.strip().casefold() == old.casefold():
            config["group"] = new
            atomic_write_json(path, config)
            moved += 1
    return moved


def rename_group(old: str, new: str) -> dict:
    with _groups_lock:
        names = list_groups()
        current = next((name for name in names if name.casefold() == old.casefold()), None)
        if current is None:
            raise LookupError("Folder not found")
        if any(name.casefold() == new.casefold() and name != current for name in names):
            raise ValueError("A folder with this name already exists")
        registered = [name for name in _registered_groups() if name.casefold() != current.casefold()]
        atomic_write_json(GROUPS_FILE, {"groups": sorted([*registered, new], key=str.casefold)})
        return {"name": new, "moved": _reassign_members(current, new)}


def delete_group(name: str) -> dict:
    """Remove the folder. Its accounts are kept and become unfiled."""
    with _groups_lock:
        current = next((existing for existing in list_groups() if existing.casefold() == name.casefold()), None)
        if current is None:
            raise LookupError("Folder not found")
        registered = [existing for existing in _registered_groups() if existing.casefold() != current.casefold()]
        atomic_write_json(GROUPS_FILE, {"groups": registered})
        return {"name": current, "unfiled": _reassign_members(current, "")}
