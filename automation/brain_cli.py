"""Small JSON CLI used by the Manager backend to inspect local Brain packages."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import Any
import zipfile

AUTOMATION_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(AUTOMATION_ROOT))

from engine.brain_runtime import (  # noqa: E402
    BrainRegistry,
    BrainResolutionError,
    BrainValidationError,
    ENGINE_VERSION,
    load_brain_package,
)


DEFAULT_BRAINS_ROOT = AUTOMATION_ROOT / "brains"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 250
ALLOWED_PACKAGE_SUFFIXES = {
    ".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".webp",
}


def _read_catalog(root: Path) -> dict[str, Any]:
    path = root / "catalog.json"
    if not path.is_file():
        return {"catalog_version": 1, "brains": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainValidationError(f"Invalid Brain catalog: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("brains", {}), dict):
        raise BrainValidationError("Invalid Brain catalog structure")
    return value


def _write_catalog(root: Path, catalog: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "catalog.json"
    temporary = root / f".catalog.{os.getpid()}.tmp"
    try:
        temporary.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def list_brains(root: Path) -> dict[str, Any]:
    catalog = _read_catalog(root)
    families: list[dict[str, Any]] = []
    if not root.is_dir():
        return {"engine_version": ENGINE_VERSION, "brains": families}

    for family_dir in sorted(path for path in root.iterdir() if path.is_dir() and path.name != "staging"):
        versions: list[dict[str, Any]] = []
        for version_dir in sorted(path for path in family_dir.iterdir() if path.is_dir()):
            try:
                package = load_brain_package(version_dir, expected_id=family_dir.name)
                installation = {}
                installation_path = version_dir / ".installation.json"
                if installation_path.is_file():
                    installation = _read_json_file(installation_path)
                versions.append({
                    **package.metadata(),
                    "directory": version_dir.name,
                    "name": package.manifest.get("name", package.brain_id),
                    "description": package.manifest.get("description", ""),
                    "released_at": package.manifest.get("released_at"),
                    "change_log": package.manifest.get("change_log", ""),
                    "supported_locales": package.manifest.get("supported_locales", []),
                    "supported_themes": package.manifest.get("supported_themes", []),
                    "install_source": installation.get(
                        "source",
                        "bundled" if version_dir.name == "bundled_default" else "local",
                    ),
                    "installed_at": installation.get("installed_at"),
                    "archive_sha256": installation.get("archive_sha256"),
                    "status": "valid",
                })
            except (BrainValidationError, OSError) as exc:
                versions.append({
                    "directory": version_dir.name,
                    "status": "invalid",
                    "error": str(exc),
                })

        entry = catalog.get("brains", {}).get(family_dir.name, {})
        if not isinstance(entry, dict):
            entry = {}
        active = entry.get("active_version") or "bundled_default"
        families.append({
            "id": family_dir.name,
            "active_version": active,
            "previous_version": entry.get("previous_version"),
            "channel": entry.get("channel", "stable"),
            "updated_at": entry.get("updated_at"),
            "versions": versions,
        })
    return {"engine_version": ENGINE_VERSION, "brains": families}


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_archive_path(name: str) -> PurePosixPath:
    if not name or "\0" in name or "\\" in name:
        raise BrainValidationError(f"Archive contains an unsafe path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BrainValidationError(f"Archive contains an unsafe path: {name!r}")
    return path


def _validate_package_filename(relative: PurePosixPath, *, is_directory: bool) -> None:
    if is_directory:
        return
    if relative.name == ".installation.json":
        raise BrainValidationError("Archive cannot provide reserved installation metadata")
    if relative.suffix.casefold() not in ALLOWED_PACKAGE_SUFFIXES:
        raise BrainValidationError(f"Archive contains an unsupported file type: {relative}")


def _copy_bounded(source, destination: Path, expected_size: int) -> None:
    written = 0
    with destination.open("wb") as output:
        while True:
            chunk = source.read(min(1024 * 1024, expected_size - written + 1))
            if not chunk:
                break
            written += len(chunk)
            if written > expected_size or written > MAX_EXTRACTED_BYTES:
                raise BrainValidationError("Archive entry expanded beyond its declared size")
            output.write(chunk)
    if written != expected_size:
        raise BrainValidationError("Archive entry size did not match its declaration")


def _extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, mode="r:*") as handle:
        members = handle.getmembers()
        files = [member for member in members if member.isfile()]
        if len(files) > MAX_ARCHIVE_FILES:
            raise BrainValidationError("Archive contains too many files")
        if sum(member.size for member in files) > MAX_EXTRACTED_BYTES:
            raise BrainValidationError("Archive expands beyond the allowed size")
        seen_paths: set[str] = set()
        for member in members:
            relative = _safe_archive_path(member.name.rstrip("/"))
            collision_key = relative.as_posix().casefold()
            if collision_key in seen_paths:
                raise BrainValidationError(f"Archive contains a duplicate or case-colliding path: {relative}")
            seen_paths.add(collision_key)
            if not (member.isfile() or member.isdir()):
                raise BrainValidationError(f"Archive contains a link or special file: {member.name}")
            _validate_package_filename(relative, is_directory=member.isdir())
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = handle.extractfile(member)
            if source is None:
                raise BrainValidationError(f"Could not read archive entry: {member.name}")
            with source:
                _copy_bounded(source, target, member.size)


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as handle:
        entries = handle.infolist()
        files = [entry for entry in entries if not entry.is_dir()]
        if len(files) > MAX_ARCHIVE_FILES:
            raise BrainValidationError("Archive contains too many files")
        if sum(entry.file_size for entry in files) > MAX_EXTRACTED_BYTES:
            raise BrainValidationError("Archive expands beyond the allowed size")
        seen_paths: set[str] = set()
        for entry in entries:
            relative = _safe_archive_path(entry.filename.rstrip("/"))
            collision_key = relative.as_posix().casefold()
            if collision_key in seen_paths:
                raise BrainValidationError(f"Archive contains a duplicate or case-colliding path: {relative}")
            seen_paths.add(collision_key)
            mode = entry.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode):
                raise BrainValidationError(f"Archive contains a symbolic link: {entry.filename}")
            if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise BrainValidationError(f"Archive contains a special file: {entry.filename}")
            _validate_package_filename(relative, is_directory=entry.is_dir())
            target = destination.joinpath(*relative.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(entry, "r") as source:
                _copy_bounded(source, target, entry.file_size)


def _package_root(extracted: Path) -> Path:
    if (extracted / "manifest.json").is_file() and (extracted / "workflow.yaml").is_file():
        return extracted
    children = [path for path in extracted.iterdir() if path.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        candidate = children[0]
        if (candidate / "manifest.json").is_file() and (candidate / "workflow.yaml").is_file():
            return candidate
    raise BrainValidationError(
        "Archive must contain manifest.json and workflow.yaml at its root or in one top-level directory"
    )


def install_archive(root: Path, archive: Path) -> dict[str, Any]:
    archive = archive.resolve()
    if not archive.is_file():
        raise BrainValidationError("Uploaded archive does not exist")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BrainValidationError("Uploaded archive exceeds the 20 MB limit")

    archive_digest = sha256(archive.read_bytes()).hexdigest()
    staging_root = root / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="brain-upload-", dir=staging_root) as temporary:
        extracted = Path(temporary) / "extracted"
        extracted.mkdir()
        if zipfile.is_zipfile(archive):
            _extract_zip(archive, extracted)
        elif tarfile.is_tarfile(archive):
            _extract_tar(archive, extracted)
        else:
            raise BrainValidationError("Only .zip, .tar.gz, and .tgz Brain packages are supported")

        package_root = _package_root(extracted)
        package = load_brain_package(package_root)
        if package.root.name == "bundled_default":
            raise BrainValidationError("Uploaded packages cannot use the reserved bundled_default directory")
        target = root / package.brain_id / package.version
        if target.exists():
            raise BrainValidationError(
                f"Brain {package.brain_id} version {package.version} is already installed; versions are immutable"
            )

        installation = {
            "source": "local_upload",
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "archive_sha256": archive_digest,
            "archive_size": archive.stat().st_size,
        }
        (package_root / ".installation.json").write_text(
            json.dumps(installation, indent=2) + "\n",
            encoding="utf-8",
        )
        # Revalidate after adding host-owned metadata, then atomically publish.
        package = load_brain_package(package_root, expected_id=package.brain_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(package_root, target)
        installed = load_brain_package(target, expected_id=package.brain_id)
        return {
            "success": True,
            "installed": {
                **installed.metadata(),
                "directory": installed.version,
                "install_source": "local_upload",
                "archive_sha256": archive_digest,
            },
        }


def activate_brain(root: Path, brain_id: str, version: str) -> dict[str, Any]:
    # Resolution performs identifier, traversal, compatibility, and schema checks.
    package = BrainRegistry(root).resolve(brain_id, version)
    directory_version = package.root.name
    catalog = _read_catalog(root)
    entries = catalog.setdefault("brains", {})
    current = entries.get(brain_id, {})
    if not isinstance(current, dict):
        current = {}
    previous = current.get("active_version")
    entries[brain_id] = {
        **current,
        "active_version": directory_version,
        "previous_version": previous if previous != directory_version else current.get("previous_version"),
        "channel": "stable",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    catalog["catalog_version"] = 1
    _write_catalog(root, catalog)
    return {
        "success": True,
        "active_version": directory_version,
        "previous_version": entries[brain_id].get("previous_version"),
        "brain": package.metadata(),
    }


def validate_brain(root: Path, brain_id: str, version: str | None) -> dict[str, Any]:
    package = BrainRegistry(root).resolve(brain_id, version)
    return {"success": True, "brain": package.metadata()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage validated local workflow Brain packages")
    parser.add_argument("--root", default=str(DEFAULT_BRAINS_ROOT))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    validate = subparsers.add_parser("validate")
    validate.add_argument("brain_id")
    validate.add_argument("--version")
    activate = subparsers.add_parser("activate")
    activate.add_argument("brain_id")
    activate.add_argument("version")
    install = subparsers.add_parser("install")
    install.add_argument("--archive", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    try:
        if args.command == "list":
            result = list_brains(root)
        elif args.command == "validate":
            result = validate_brain(root, args.brain_id, args.version)
        elif args.command == "activate":
            result = activate_brain(root, args.brain_id, args.version)
        else:
            result = install_archive(root, Path(args.archive))
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (BrainValidationError, BrainResolutionError, OSError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
