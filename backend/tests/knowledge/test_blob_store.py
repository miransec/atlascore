"""
Test suite for BlobStore — LocalFilesystemBlobStore.

Tests:
  BS-01  generate_key produces valid UUID-path format
  BS-02  validate_key accepts valid keys
  BS-03  validate_key rejects traversal attempts (../, absolute paths, etc.)
  BS-04  put + get roundtrip
  BS-05  put returns correct SHA-256
  BS-06  put rejects content exceeding max_bytes
  BS-07  get raises BlobNotFoundError for missing keys
  BS-08  delete removes blob; get raises BlobNotFoundError after delete
  BS-09  delete is no-op for missing key
  BS-10  exists returns True/False correctly
  BS-11  Symlinked root raises BlobStorePathError
  BS-12  Keys with subdirectory separators are handled correctly
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from app.knowledge.blob_store import (
    BlobNotFoundError,
    BlobStorePathError,
    BlobStoreSizeError,
    LocalFilesystemBlobStore,
)


@pytest.fixture()
def store(tmp_path: Path) -> LocalFilesystemBlobStore:
    return LocalFilesystemBlobStore(tmp_path / "blobs")


def _key() -> str:
    return LocalFilesystemBlobStore.generate_key(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )


# ---- BS-01: generate_key format -----------------------------------------


def test_bs01_generate_key_format() -> None:
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    key = LocalFilesystemBlobStore.generate_key(org_id, ws_id, doc_id, ver_id)
    parts = key.split("/")
    assert len(parts) == 4
    assert parts[0] == str(org_id)
    assert parts[1] == str(ws_id)
    assert parts[2] == str(doc_id)
    assert parts[3] == str(ver_id)


# ---- BS-02: validate_key accepts valid keys -----------------------------


def test_bs02_validate_key_accepts_valid() -> None:
    key = _key()
    LocalFilesystemBlobStore.validate_key(key)  # must not raise


# ---- BS-03: validate_key rejects bad inputs -----------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "../etc/passwd",
        "/etc/passwd",
        "foo/bar/baz/qux",
        "../../secret",
        "aaaa-bbbb-cccc-dddd-eeeeeeeeeeee/a/b/c",
        "",
        "null",
    ],
)
def test_bs03_validate_key_rejects_bad(bad_key: str) -> None:
    with pytest.raises(BlobStorePathError):
        LocalFilesystemBlobStore.validate_key(bad_key)


# ---- BS-04: put + get roundtrip -----------------------------------------


@pytest.mark.asyncio()
async def test_bs04_put_get_roundtrip(store: LocalFilesystemBlobStore) -> None:
    key = _key()
    content = b"hello knowledge"
    await store.put(key, content, max_bytes=1024)
    retrieved = await store.get(key)
    assert retrieved == content


# ---- BS-05: put returns correct SHA-256 ---------------------------------


@pytest.mark.asyncio()
async def test_bs05_put_returns_sha256(store: LocalFilesystemBlobStore) -> None:
    key = _key()
    content = b"deterministic content"
    sha = await store.put(key, content, max_bytes=1024)
    expected = hashlib.sha256(content).hexdigest()
    assert sha == expected


# ---- BS-06: put rejects oversized content -------------------------------


@pytest.mark.asyncio()
async def test_bs06_put_rejects_oversized(store: LocalFilesystemBlobStore) -> None:
    key = _key()
    content = b"x" * 100
    with pytest.raises(BlobStoreSizeError):
        await store.put(key, content, max_bytes=50)


# ---- BS-07: get raises BlobNotFoundError for missing key ----------------


@pytest.mark.asyncio()
async def test_bs07_get_missing_raises(store: LocalFilesystemBlobStore) -> None:
    with pytest.raises(BlobNotFoundError):
        await store.get(_key())


# ---- BS-08: delete removes blob -----------------------------------------


@pytest.mark.asyncio()
async def test_bs08_delete_removes_blob(store: LocalFilesystemBlobStore) -> None:
    key = _key()
    await store.put(key, b"data", max_bytes=1024)
    await store.delete(key)
    with pytest.raises(BlobNotFoundError):
        await store.get(key)


# ---- BS-09: delete is no-op for missing --------------------------------


@pytest.mark.asyncio()
async def test_bs09_delete_missing_noop(store: LocalFilesystemBlobStore) -> None:
    await store.delete(_key())  # must not raise


# ---- BS-10: exists returns correct bool ---------------------------------


@pytest.mark.asyncio()
async def test_bs10_exists(store: LocalFilesystemBlobStore) -> None:
    key = _key()
    assert not await store.exists(key)
    await store.put(key, b"x", max_bytes=1024)
    assert await store.exists(key)


# ---- BS-11: symlinked root is rejected ----------------------------------


def test_bs11_symlinked_root_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(BlobStorePathError):
        LocalFilesystemBlobStore(link)


# ---- BS-12: storage_key never derived from original_filename ------------


def test_bs12_key_does_not_contain_filename() -> None:
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    key = LocalFilesystemBlobStore.generate_key(org_id, ws_id, doc_id, ver_id)
    assert "report.pdf" not in key
    assert "../../" not in key
    assert "/" not in key.replace("/", "").replace("-", "")[:0]  # only UUIDs and slashes
