"""Upload byte storage behind a small interface so object storage can replace it (BC-D03)."""

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO, Protocol

_KEY = re.compile(r"[0-9a-f]{32}")


class UploadTooLarge(Exception):
    """The streamed body exceeded the permitted byte limit; partial content was discarded."""


class UploadStore(Protocol):
    async def write(self, key: str, chunks: AsyncIterator[bytes], max_bytes: int) -> int:
        """Store the body under ``key``; return its size or raise ``UploadTooLarge``."""

    async def digest(self, key: str) -> tuple[int, str] | None:
        """Return (size, SHA-256 hex) of stored content, or ``None`` when nothing is stored."""

    async def delete(self, key: str) -> None: ...


def _check_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise ValueError("Storage keys must be 32 lowercase hex characters")
    return key


class LocalFilesystemStore:
    """Development store writing each upload to one file under a configured directory."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    async def write(self, key: str, chunks: AsyncIterator[bytes], max_bytes: int) -> int:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        handle: BinaryIO = await asyncio.to_thread(path.open, "wb")
        written = 0
        try:
            async for chunk in chunks:
                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge
                await asyncio.to_thread(handle.write, chunk)
        except BaseException:
            await asyncio.to_thread(handle.close)
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise
        await asyncio.to_thread(handle.close)
        return written

    async def digest(self, key: str) -> tuple[int, str] | None:
        return await asyncio.to_thread(_digest_file, self._path(key))

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)


def _digest_file(path: Path) -> tuple[int, str] | None:
    try:
        with path.open("rb") as handle:
            size = 0
            sha256 = hashlib.sha256()
            while block := handle.read(1024 * 1024):
                size += len(block)
                sha256.update(block)
    except FileNotFoundError:
        return None
    return size, sha256.hexdigest()
