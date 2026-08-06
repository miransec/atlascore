"""
BlobStore abstraction — secure binary storage for knowledge documents.

SECURITY INVARIANTS (must never be violated):
  - storage_key is ALWAYS server-generated.  No user-supplied value may
    become part of a filesystem path.
  - Path traversal is impossible: keys are validated to contain only
    hex-safe UUID characters and directory separators.
  - The resolved absolute path must descend from the configured root;
    any path that escapes the root is rejected.
  - File size is enforced before writing; partial writes are cleaned up.
  - The original_filename from the upload is NEVER used as a storage key
    or any part of a path.  It is display metadata only.
  - No symbolic link following — the root must not be a symlink.

Usage:
    store = LocalFilesystemBlobStore("/data/knowledge")
    key = store.generate_key(org_id, workspace_id, doc_id, version_id)
    sha256_hex = await store.put(key, content_bytes, max_bytes=50_000_000)
    content = await store.get(key)
    await store.delete(key)
"""

from __future__ import annotations

import hashlib
import re
import uuid
from abc import ABC, abstractmethod
from contextlib import suppress
from pathlib import Path

# Allowlist for storage key characters.
# Keys are of the form: {uuid}/{uuid}/{uuid}/{uuid}
# Only hex digits and hyphens (UUID chars) and forward slashes are permitted.
_KEY_SAFE_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class BlobStoreError(Exception):
    """Base class for BlobStore errors."""


class BlobNotFoundError(BlobStoreError):
    """Raised when a requested blob does not exist."""


class BlobStoreSizeError(BlobStoreError):
    """Raised when content exceeds the configured size limit."""


class BlobStorePathError(BlobStoreError):
    """Raised when a storage key would escape the storage root (path traversal)."""


class BlobStore(ABC):
    """Abstract interface for binary blob storage."""

    @staticmethod
    def generate_key(
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
    ) -> str:
        """
        Generate a server-controlled storage key.

        Format: {org_id}/{workspace_id}/{document_id}/{version_id}

        All components are server-owned UUIDs — no user-supplied data participates.
        The resulting key is safe to use as a relative path component.
        """
        return f"{organisation_id}/{workspace_id}/{document_id}/{version_id}"

    @staticmethod
    def validate_key(key: str) -> None:
        """
        Validate that a storage key conforms to the expected UUID-path format.

        Raises BlobStorePathError if the key contains any unsafe character or
        does not match the expected four-UUID-segment pattern.
        """
        if not _KEY_SAFE_PATTERN.match(key):
            raise BlobStorePathError(
                f"Invalid storage key format: {key!r}. "
                "Keys must be four UUID segments separated by '/'."
            )

    @abstractmethod
    async def put(self, key: str, content: bytes, *, max_bytes: int) -> str:
        """
        Store content at the given key and return the hex SHA-256 of the content.

        Raises:
            BlobStoreSizeError   — if len(content) > max_bytes
            BlobStorePathError   — if the key is malformed or escapes the root
            BlobStoreError       — for other storage failures
        """

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """
        Retrieve the content stored at the given key.

        Raises:
            BlobNotFoundError    — if the key does not exist
            BlobStorePathError   — if the key is malformed or escapes the root
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """
        Delete the blob at the given key.  No-op if it does not exist.

        Raises:
            BlobStorePathError   — if the key is malformed or escapes the root
        """

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if a blob exists at the given key."""


class LocalFilesystemBlobStore(BlobStore):
    """
    Filesystem-backed blob store.

    All blobs are stored under a single root directory.  The root is
    created on construction if it does not exist.

    SECURITY:
      - The root path is resolved to an absolute canonical path at construction.
      - Every key is validated against the UUID-segment allowlist before use.
      - After joining the key to the root, the result is resolved and verified
        to descend from the root.  Any attempt to escape via '..' or absolute
        path components raises BlobStorePathError.
      - The root directory itself must not be a symbolic link.
    """

    def __init__(self, root: str | Path) -> None:
        raw = Path(root)
        # Reject symlinked roots — require a real directory.
        # IMPORTANT: must check is_symlink() BEFORE resolve() — resolve() follows
        # symlinks, so the resolved path is never a symlink and the check would
        # silently pass for a symlinked root.
        if raw.is_symlink():
            raise BlobStorePathError(f"KNOWLEDGE_STORAGE_ROOT must not be a symbolic link: {raw}")
        self._root = raw.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, key: str) -> Path:
        """
        Validate the key and resolve it to an absolute Path under the root.

        Raises BlobStorePathError if the key is malformed or would escape the root.
        """
        self.validate_key(key)
        # Join key to root.  Because key only contains UUIDs and '/', it cannot
        # contain '..' components — but we still verify the resolved path is
        # under the root as an additional defence-in-depth measure.
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise BlobStorePathError(
                f"Storage key {key!r} resolves outside the storage root."
            ) from exc
        return candidate

    async def put(self, key: str, content: bytes, *, max_bytes: int) -> str:
        """
        Write content to disk and return hex SHA-256 of the bytes.

        Size is checked before writing.  Partial writes are cleaned up.
        """
        if len(content) > max_bytes:
            raise BlobStoreSizeError(
                f"Content size {len(content)} bytes exceeds limit {max_bytes} bytes."
            )
        path = self._resolve_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically: write to temp, rename to final.
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_bytes(content)
            tmp_path.rename(path)
        except Exception as exc:
            # Clean up partial write without masking the original failure.
            with suppress(Exception):
                tmp_path.unlink(missing_ok=True)
            raise BlobStoreError(f"Failed to write blob at key {key!r}.") from exc
        return hashlib.sha256(content).hexdigest()

    async def get(self, key: str) -> bytes:
        """Read and return the blob content."""
        path = self._resolve_key(key)
        if not path.exists():
            raise BlobNotFoundError(f"Blob not found: {key!r}")
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        """Delete the blob.  No-op if it does not exist."""
        path = self._resolve_key(key)
        path.unlink(missing_ok=True)

    async def exists(self, key: str) -> bool:
        """Return True if the blob exists."""
        try:
            path = self._resolve_key(key)
        except BlobStorePathError:
            return False
        return path.exists()
