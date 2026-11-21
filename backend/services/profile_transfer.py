"""Move a PC account to another PC as one `.abprofile` file.

The file is a ZIP holding `manifest.json`, the account's `config.json`, its
`facebook_page.json` when present, and the browser's user data (`chrome_data/`,
so the Facebook login moves with it). Caches, crash dumps, Chrome's lock links
and automation evidence stay behind. The manifest carries the pool proxy the
account used, credentials included, so the other PC can give it the same IP.

With a password the ZIP is encrypted: scrypt derives a key, and AES-GCM seals the
file in 1 MiB chunks whose order and end are authenticated, so a cut or reordered
file is refused rather than imported half.

Import always gives the account a new id and new ports on the receiving PC, and
the proxy joins that PC's pool under its "Accounts per proxy" limit. The account
keeps its fingerprint, timezone, name, folder and warming state.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import struct
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import CPU_THREADS, PROFILES_DIR, TOTAL_MEMORY_GB
from backend.services.profile_service import (
    PROFILE_ID_RE, PROFILE_SCHEMA_VERSION, allocate_profile_identity, atomic_write_json, creation_lock, utc_now,
)

FORMAT = "anti-bot-browser-profile"
FORMAT_VERSION = 1
EXTENSION = ".abprofile"
MANIFEST = "manifest.json"
CONFIG = "config.json"
FACEBOOK_PAGE = "facebook_page.json"
CHROME_DIR = "chrome_data"

# Rebuilt by Chrome on first start; moving them only makes the file larger.
SKIPPED_CHROME_DIRS = {
    "Default/Cache", "Default/Code Cache", "Default/GPUCache", "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache", "Default/Service Worker/CacheStorage", "Default/Service Worker/ScriptCache",
    "GrShaderCache", "GraphiteDawnCache", "ShaderCache", "Crashpad", "Crash Reports", "BrowserMetrics",
}
SKIPPED_CHROME_FILES = {"SingletonLock", "SingletonSocket", "SingletonCookie", "BrowserMetrics-spare.pma"}

ENCRYPTED_MAGIC = b"ABPROFX1"
SALT_BYTES = 16
NONCE_PREFIX_BYTES = 4
CHUNK_BYTES = 1024 * 1024
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
MIN_PASSWORD = 8

MAX_FILES = 200_000
DISK_HEADROOM = 1024 * 1024 * 1024


class TransferError(ValueError):
    """A problem the user can fix, shown as it is."""


# ---------- encryption ----------

def _key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **SCRYPT)


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + struct.pack(">QB", index, 1 if final else 0)


def encrypt_file(source: Path, destination: Path, password: str) -> None:
    salt = secrets.token_bytes(SALT_BYTES)
    prefix = secrets.token_bytes(NONCE_PREFIX_BYTES)
    header = ENCRYPTED_MAGIC + salt + prefix
    cipher = AESGCM(_key(password, salt))
    with source.open("rb") as src, destination.open("wb") as dst:
        dst.write(header)
        index = 0
        chunk = src.read(CHUNK_BYTES)
        while True:
            following = src.read(CHUNK_BYTES)
            final = not following
            sealed = cipher.encrypt(prefix + struct.pack(">Q", index), chunk, _aad(header, index, final))
            dst.write(struct.pack(">I", len(sealed)))
            dst.write(sealed)
            if final:
                return
            chunk, index = following, index + 1


def is_encrypted(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise TransferError("The file is incomplete. Copy it again from the other PC.")
    return data


def decrypt_file(source: Path, destination: Path, password: str) -> None:
    with source.open("rb") as src, destination.open("wb") as dst:
        header = _read_exact(src, len(ENCRYPTED_MAGIC) + SALT_BYTES + NONCE_PREFIX_BYTES)
        salt = header[len(ENCRYPTED_MAGIC):len(ENCRYPTED_MAGIC) + SALT_BYTES]
        prefix = header[-NONCE_PREFIX_BYTES:]
        cipher = AESGCM(_key(password, salt))
        index = 0
        length = src.read(4)
        if len(length) != 4:
            raise TransferError("The file is incomplete. Copy it again from the other PC.")
        while True:
            size = struct.unpack(">I", length)[0]
            if size > CHUNK_BYTES + 16:
                raise TransferError("This is not an account export file.")
            sealed = _read_exact(src, size)
            length = src.read(4)
            final = not length
            try:
                dst.write(cipher.decrypt(prefix + struct.pack(">Q", index), sealed, _aad(header, index, final)))
            except InvalidTag:
                if index == 0:
                    raise TransferError("Wrong password, or the file is damaged.") from None
                raise TransferError("The file is damaged or incomplete. Copy it again from the other PC.") from None
            if final:
                return
            if len(length) != 4:
                raise TransferError("The file is incomplete. Copy it again from the other PC.")
            index += 1


# ---------- export ----------

def _chrome_files(chrome_dir: Path) -> Iterator[tuple[Path, str]]:
    """Each file to move with its path inside chrome_data, skipping caches and links."""
    for root, dirs, files in os.walk(chrome_dir, followlinks=False):
        root_path = Path(root)
        rel_root = root_path.relative_to(chrome_dir).as_posix()
        rel_root = "" if rel_root == "." else rel_root
        dirs[:] = sorted(d for d in dirs
                         if not (root_path / d).is_symlink()
                         and f"{rel_root}/{d}".lstrip("/") not in SKIPPED_CHROME_DIRS)
        for name in sorted(files):
            path = root_path / name
            if path.is_symlink() or name in SKIPPED_CHROME_FILES or not path.is_file():
                continue
            yield path, f"{rel_root}/{name}".lstrip("/")


def _proxy_for_export(config: dict) -> dict | None:
    network = config.get("network") or {}
    if network.get("mode") != "pool":
        return None
    from backend.services.proxy_service import GEO_KEYS, pool_by_id
    entry = pool_by_id().get(network.get("proxy_id")) or {}
    proxy = {
        "host": network.get("proxy_host") or entry.get("host"),
        "port": network.get("proxy_port") or entry.get("port"),
        "username": network.get("proxy_user") or entry.get("username") or "",
        "password": network.get("proxy_pass") or entry.get("password") or "",
        "type": network.get("proxy_type") or entry.get("type") or "socks5",
        "geo_source": entry.get("geo_source") or "unknown",
    }
    proxy.update({key: entry.get(key) for key in GEO_KEYS if entry.get(key)})
    return proxy


def export_filename(config: dict) -> str:
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(config.get("name") or ""))[:40].strip("_")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{name or config.get('id', 'account')}-{stamp}{EXTENSION}"


def build_export(profile_id: str, work_dir: Path, password: str | None = None) -> tuple[Path, str]:
    """Writes the export into `work_dir` and returns (file, suggested name). The account must be stopped."""
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise TransferError("Invalid profile id")
    profile_dir = PROFILES_DIR / profile_id
    config_path = profile_dir / CONFIG
    if not config_path.is_file():
        raise TransferError("Account not found")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("runtime") or {}).get("kind") == "android":
        raise TransferError("Phone accounts live on their phone and cannot be exported. Only PC accounts move this way.")
    if password is not None and len(password) < MIN_PASSWORD:
        raise TransferError(f"Use a password of at least {MIN_PASSWORD} characters, or none.")

    work_dir.mkdir(parents=True, exist_ok=True)
    plain = work_dir / f"{uuid.uuid4().hex}.zip"
    chrome_dir = profile_dir / CHROME_DIR
    files = 0
    try:
        with zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
            zf.writestr(CONFIG, json.dumps(config, indent=2))
            page = profile_dir / FACEBOOK_PAGE
            if page.is_file():
                zf.write(page, FACEBOOK_PAGE)
            if chrome_dir.is_dir():
                for path, rel in _chrome_files(chrome_dir):
                    try:
                        zf.write(path, f"{CHROME_DIR}/{rel}")
                    except PermissionError:
                        raise TransferError(f"This app cannot read {rel} in the account's browser data. "
                                            "Start and stop the account once, then export again.") from None
                    files += 1
            zf.writestr(MANIFEST, json.dumps({
                "format": FORMAT, "format_version": FORMAT_VERSION,
                "schema_version": config.get("schema_version"),
                "exported_at": utc_now(), "profile_id": profile_id, "name": config.get("name") or "",
                "browser_files": files, "proxy": _proxy_for_export(config),
            }, indent=2))
        if not password:
            return plain, export_filename(config)
        sealed = work_dir / f"{uuid.uuid4().hex}{EXTENSION}"
        encrypt_file(plain, sealed, password)
        plain.unlink(missing_ok=True)
        return sealed, export_filename(config)
    except BaseException:
        plain.unlink(missing_ok=True)
        raise


def downloads_dir() -> Path:
    """The user's Downloads folder; the desktop app's backend runs as that user."""
    if os.name != "nt":
        try:
            out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3).stdout.strip()
            if out and Path(out).is_dir() and Path(out) != Path.home():
                return Path(out)
        except (OSError, subprocess.SubprocessError):
            pass
    return Path.home() / "Downloads"


def free_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({index}){suffix}"
        index += 1
    return candidate


# ---------- import ----------

def _safe_member(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if name.startswith("/") or "\\" in name or ":" in name.split("/")[0] or any(p in ("..", "") for p in path.parts):
        return None
    return path


def _read_package(zf: zipfile.ZipFile) -> tuple[dict, dict]:
    names = set(zf.namelist())
    if MANIFEST not in names or CONFIG not in names:
        raise TransferError("This is not an account export file.")
    try:
        manifest = json.loads(zf.read(MANIFEST))
        config = json.loads(zf.read(CONFIG))
    except (ValueError, KeyError):
        raise TransferError("This is not an account export file.") from None
    if manifest.get("format") != FORMAT or not isinstance(config, dict):
        raise TransferError("This is not an account export file.")
    if int(manifest.get("format_version") or 0) > FORMAT_VERSION:
        raise TransferError("This file was made by a newer version of the app. Update this PC first.")
    if int(config.get("schema_version") or 0) > PROFILE_SCHEMA_VERSION:
        raise TransferError("This account was saved by a newer version of the app. Update this PC first.")
    if (config.get("runtime") or {}).get("kind") == "android":
        raise TransferError("Phone accounts cannot be imported on a PC.")
    return manifest, config


def open_package(path: Path, work_dir: Path, password: str | None) -> Path:
    """The plain ZIP for an uploaded file, decrypting it into `work_dir` when it is encrypted."""
    if is_encrypted(path):
        if not password:
            raise TransferError("This file is protected. Enter the password it was exported with.")
        plain = work_dir / f"{uuid.uuid4().hex}.zip"
        decrypt_file(path, plain, password)
        return plain
    if not zipfile.is_zipfile(path):
        raise TransferError("This is not an account export file.")
    return path


def _imported_config(config: dict, profile_id: str, vnc_port: int, ws_port: int, manifest: dict) -> dict:
    value = json.loads(json.dumps(config))
    value.update(id=profile_id, status="stopped", updated_at=utc_now(), effective_environment=None,
                 observed_environment=None, restart_required=False, effective_resources=None)
    value["container"] = {"id": None, "vnc_port": vnc_port, "ws_port": ws_port,
                          "volume_path": f"profiles/{profile_id}/{CHROME_DIR}"}
    resources = dict(value.get("resources") or {})
    if resources:
        # The other PC may be bigger; keep within this one.
        resources["cpu_limit"] = min(float(resources.get("cpu_limit") or 1), float(CPU_THREADS))
        resources["memory_mb"] = min(int(resources.get("memory_mb") or 1024), int(TOTAL_MEMORY_GB * 1024))
        value["resources"] = resources
    value["imported_from"] = {"profile_id": manifest.get("profile_id"), "exported_at": manifest.get("exported_at"),
                              "imported_at": utc_now()}
    return value


def import_package(plain_zip: Path) -> dict:
    """Creates a new account from a plain export ZIP and returns its config."""
    with zipfile.ZipFile(plain_zip) as zf:
        manifest, config = _read_package(zf)
        members = []
        total = 0
        for info in zf.infolist():
            if info.is_dir() or info.filename in (MANIFEST, CONFIG):
                continue
            rel = _safe_member(info.filename)
            if rel is None or not (info.filename == FACEBOOK_PAGE or rel.parts[0] == CHROME_DIR):
                raise TransferError("The file contains unexpected entries and was not imported.")
            members.append((info, rel))
            total += info.file_size
        if len(members) > MAX_FILES:
            raise TransferError("The file holds too many entries to be an account export.")
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(PROFILES_DIR).free < total + DISK_HEADROOM:
            raise TransferError("Not enough free disk space to import this account.")

        # Two levels down, so nothing that scans profiles/*/config.json sees a half-written account.
        staging = PROFILES_DIR / ".transfer" / uuid.uuid4().hex
        target_dir = staging / "account"
        try:
            (target_dir / CHROME_DIR).mkdir(parents=True)
            for info, rel in members:
                destination = target_dir.joinpath(*rel.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK_BYTES)

            from backend.services.proxy_service import adopt_proxy, release_proxy
            with creation_lock():
                profile_id, vnc_port, ws_port = allocate_profile_identity()
                new_config = _imported_config(config, profile_id, vnc_port, ws_port, manifest)
                network = new_config.get("network") or {}
                reserved = False
                try:
                    if network.get("mode") == "pool":
                        exported = manifest.get("proxy") or {
                            "host": network.get("proxy_host"), "port": network.get("proxy_port"),
                            "username": network.get("proxy_user"), "password": network.get("proxy_pass"),
                            "type": network.get("proxy_type")}
                        entry = adopt_proxy(profile_id, exported, surface="pc")
                        reserved = True
                        network.update(proxy_id=entry["id"], proxy_host=entry["host"], proxy_port=int(entry["port"]),
                                       proxy_user=entry.get("username") or "",
                                       proxy_pass=entry.get("password") or network.get("proxy_pass") or "",
                                       proxy_type=entry.get("type") or network.get("proxy_type") or "socks5")
                        new_config["network"] = network
                    atomic_write_json(target_dir / CONFIG, new_config)
                    final_dir = PROFILES_DIR / profile_id
                    if final_dir.exists():
                        raise TransferError("Allocated account id already exists. Try again.")
                    target_dir.rename(final_dir)
                except BaseException:
                    if reserved:
                        release_proxy(profile_id)
                    raise
            return new_config
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            try:
                staging.parent.rmdir()
            except OSError:
                pass

