"""Bounded file transfers to explicitly allowed storage hosts."""
import asyncio
import hashlib
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class Transfers:
    def __init__(self, config, media_dir: Path):
        self.config = config
        self.media_dir = media_dir
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15),
                                        follow_redirects=False, trust_env=False)

    async def close(self):
        await self.client.aclose()

    def validate_url(self, value):
        url = urlsplit(value)
        host = url.hostname or ""
        development = self.config.allow_loopback_development and host in ("localhost", "127.0.0.1", "::1")
        if url.username or url.password or url.fragment:
            raise ValueError("Invalid storage URL")
        if url.scheme != "https" and not (development and url.scheme == "http"):
            raise ValueError("File transfers require HTTPS")
        if host not in self.config.transfer_hosts:
            raise ValueError("Storage host is not allowed by local configuration")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global and not development:
            raise ValueError("Private storage IP is not allowed")
        return value

    async def download(self, attempt_id, media):
        url = self.validate_url(media.download_url)
        # Neither IDs nor server filenames are used as local paths.
        name = hashlib.sha256((attempt_id + ':' + media.asset_id).encode()).hexdigest() + media.extension
        destination = self.media_dir / name
        self.media_dir.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not destination.is_symlink():
            digest = await asyncio.to_thread(file_digest, destination)
            if destination.stat().st_size == media.size_bytes and digest == media.sha256:
                return name
        temporary = destination.with_suffix(destination.suffix + '.part')
        digest = hashlib.sha256()
        size = 0
        try:
            async with self.client.stream('GET', url) as response:
                response.raise_for_status()
                with temporary.open('wb') as stream:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        size += len(chunk)
                        if size > media.size_bytes:
                            raise ValueError("Media exceeds declared size")
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            if size != media.size_bytes or digest.hexdigest() != media.sha256:
                raise ValueError("Media size or checksum mismatch")
            os.replace(temporary, destination)
            return name
        finally:
            temporary.unlink(missing_ok=True)

    async def upload(self, url, file: Path):
        self.validate_url(url)
        async def chunks():
            with file.open('rb') as stream:
                while chunk := stream.read(256 * 1024):
                    yield chunk
        response = await self.client.put(url, content=chunks(), headers={
            'Content-Type': 'image/png', 'Content-Length': str(file.stat().st_size)})
        response.raise_for_status()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(256 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
