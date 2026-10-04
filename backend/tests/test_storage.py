import hashlib

import pytest

from services.api.intake import RecordOnlyDispatcher
from services.storage import LocalFilesystemStore, UploadTooLarge


async def chunks(*parts: bytes):
    for part in parts:
        yield part


async def test_local_store_round_trip_and_limit(tmp_path):
    store = LocalFilesystemStore(tmp_path / "nested" / "uploads")
    key = "0" * 32
    assert await store.digest(key) is None
    assert await store.write(key, chunks(b"abc", b"def"), max_bytes=6) == 6
    assert await store.digest(key) == (6, hashlib.sha256(b"abcdef").hexdigest())
    with pytest.raises(UploadTooLarge):
        await store.write(key, chunks(b"abc", b"defg"), max_bytes=6)
    assert await store.digest(key) is None
    await store.delete(key)
    await store.delete(key)


async def test_store_rejects_unsafe_keys(tmp_path):
    store = LocalFilesystemStore(tmp_path)
    for key in ("../escape", "ABCDEF" + "0" * 26, "", "0" * 31):
        with pytest.raises(ValueError, match="32 lowercase hex"):
            await store.digest(key)
    assert list(tmp_path.iterdir()) == []


async def test_record_only_dispatcher_is_a_no_op():
    assert await RecordOnlyDispatcher().dispatch(None, None, None) is None
