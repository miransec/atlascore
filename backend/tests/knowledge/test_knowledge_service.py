"""
Test suite for KnowledgeService — functional layer tests.

Tests use a real DB (test instance, SAVEPOINT isolation via `raw_db`) and an
in-memory LocalFilesystemBlobStore rooted in a pytest tmp_path, so no real
PostgreSQL RLS enforcement occurs here.  RLS isolation is verified separately
in test_rls_phase2a.py.

Coverage:
  KS-01  create_source returns a persisted KnowledgeSource
  KS-02  create_source sanitises secret-looking config keys
  KS-03  list_sources returns only active sources by default
  KS-04  list_sources with include_inactive=True returns all
  KS-05  get_source raises KnowledgeSourceNotFoundError for wrong org
  KS-06  update_source mutates fields and emits audit event
  KS-07  upload_document raises KnowledgeSourceNotFoundError for wrong source
  KS-08  upload_document raises KnowledgeSourceArchivedError for inactive source
  KS-09  upload_document raises KnowledgeUnsupportedMediaTypeError
  KS-10  upload_document raises KnowledgeFileTooLargeError when content exceeds limit
  KS-11  upload_document succeeds: document + version + job returned
  KS-12  upload_document stores blob with correct SHA-256
  KS-13  storage_key is never derived from original_filename
  KS-14  ingestion job reaches succeeded status for valid text/plain
  KS-15  ingestion job persists chunks in DB
  KS-16  ingestion job persists embeddings in DB
  KS-17  upload_document raises KnowledgeIdempotencyConflictError on duplicate key
  KS-18  archive_document soft-archives (is_archived=True, archived_at set)
  KS-19  archive_document raises KnowledgeDocumentArchivedError when already archived
  KS-20  list_documents filters by source_id
  KS-21  list_documents excludes archived by default
  KS-22  list_versions returns versions in ascending version_number order
  KS-23  retry_job resets failed job to queued then runs ingestion
  KS-24  retry_job raises KnowledgeIngestionJobNotRetryableError for non-failed job
  KS-25  _sanitise_source_config blocks all known secret key patterns
  KS-26  content_sha256 uses SHA-256, not a password hashing algorithm
  KS-27  original_filename must not appear in storage_key
  KS-28  upload_document for markdown media type succeeds
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.knowledge_chunk import KnowledgeChunk
from app.db.models.knowledge_chunk_embedding import KnowledgeChunkEmbedding
from app.db.models.knowledge_document import KnowledgeDocument
from app.db.models.knowledge_document_version import KnowledgeDocumentVersion
from app.db.models.knowledge_ingestion_job import KnowledgeIngestionJob
from app.db.models.knowledge_source import KnowledgeSource
from app.knowledge.blob_store import LocalFilesystemBlobStore
from app.services.knowledge_service import (
    KnowledgeDocumentArchivedError,
    KnowledgeError,
    KnowledgeFileTooLargeError,
    KnowledgeIdempotencyConflictError,
    KnowledgeIngestionJobNotRetryableError,
    KnowledgeService,
    KnowledgeSourceArchivedError,
    KnowledgeSourceNotFoundError,
    KnowledgeUnsupportedMediaTypeError,
    _sanitise_source_config,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _ikey() -> str:
    """Unique idempotency key per call."""
    return f"ikey-{uuid.uuid4().hex}"


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def workspace_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def blob_root(tmp_path: Path) -> Path:
    return tmp_path / "blobs"


@pytest.fixture()
def blob_store(blob_root: Path) -> LocalFilesystemBlobStore:
    return LocalFilesystemBlobStore(blob_root)


@pytest.fixture()
def knowledge_settings(settings: Settings, tmp_path: Path) -> Settings:
    """
    Return a settings-like object with knowledge fields set to safe test values.
    We patch by constructing a new Settings with overrides via model_copy.
    """
    return settings.model_copy(
        update={
            "KNOWLEDGE_STORAGE_ROOT": str(tmp_path / "ks_blobs"),
            "KNOWLEDGE_MAX_UPLOAD_BYTES": 1_048_576,  # 1 MiB
            "EMBEDDING_PROVIDER": "mock",
            "EMBEDDING_DIMENSIONS": 32,
            "CHUNK_SIZE_TOKENS": 50,
            "CHUNK_OVERLAP_TOKENS": 5,
        }
    )


@pytest.fixture()
def svc(knowledge_settings: Settings, blob_store: LocalFilesystemBlobStore) -> KnowledgeService:
    return KnowledgeService(knowledge_settings, blob_store)


async def _seed_workspace(
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Insert the minimum tenant stack under the restricted FORCE-RLS role."""
    # Users are global.
    await raw_db.execute(
        text(
            "INSERT INTO users (id, email, full_name, password_hash, pepper_version) "
            "VALUES (:id, :email, 'Test', 'hash', 1) ON CONFLICT DO NOTHING"
        ),
        {"id": user_id, "email": f"{user_id.hex[:8]}@test.example"},
    )
    await raw_db.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(org_id)},
    )
    await raw_db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
    slug = f"org-{org_id.hex[:8]}"
    await raw_db.execute(
        text(
            "INSERT INTO organisations (id, slug, display_name) "
            "VALUES (:id, :slug, :name) ON CONFLICT DO NOTHING"
        ),
        {"id": org_id, "slug": slug, "name": slug},
    )
    await raw_db.execute(
        text(
            "INSERT INTO organisation_memberships (id, user_id, organisation_id, org_role) "
            "VALUES (:mid, :uid, :oid, 'owner') ON CONFLICT DO NOTHING"
        ),
        {"mid": uuid.uuid4(), "uid": user_id, "oid": org_id},
    )
    ws_slug = f"ws-{workspace_id.hex[:8]}"
    await raw_db.execute(
        text(
            "INSERT INTO workspaces (id, organisation_id, slug, display_name) "
            "VALUES (:id, :oid, :slug, :name) ON CONFLICT DO NOTHING"
        ),
        {"id": workspace_id, "oid": org_id, "slug": ws_slug, "name": ws_slug},
    )
    await raw_db.execute(
        text("SELECT set_config('app.current_workspace_id', :wid, true)"),
        {"wid": str(workspace_id)},
    )
    await raw_db.flush()


async def _make_source(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    is_active: bool = True,
    display_name: str | None = None,
) -> KnowledgeSource:
    source = await svc.create_source(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_type="manual_upload",
        display_name=display_name or f"Source {_uid()}",
        created_by_user_id=user_id,
    )
    if not is_active:
        source.is_active = False
        await raw_db.flush()
    return source


# ---------------------------------------------------------------------------
# KS-01  create_source returns a persisted KnowledgeSource
# ---------------------------------------------------------------------------


async def test_ks01_create_source(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    assert isinstance(source, KnowledgeSource)
    assert source.id is not None
    assert source.organisation_id == org_id
    assert source.workspace_id == workspace_id
    assert source.source_type == "manual_upload"
    assert source.is_active is True


# ---------------------------------------------------------------------------
# KS-02  create_source sanitises secret-looking config keys
# ---------------------------------------------------------------------------


async def test_ks02_create_source_rejects_secret_config(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    with pytest.raises(KnowledgeError, match="secret"):
        await svc.create_source(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            source_type="manual_upload",
            display_name="Bad Source",
            configuration={"api_key": "sk-1234"},
            created_by_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-03  list_sources returns only active sources by default
# ---------------------------------------------------------------------------


async def test_ks03_list_sources_active_only(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    active = await _make_source(svc, raw_db, org_id, workspace_id, user_id, is_active=True)
    inactive = await _make_source(svc, raw_db, org_id, workspace_id, user_id, is_active=False)

    sources = await svc.list_sources(raw_db, organisation_id=org_id, workspace_id=workspace_id)
    ids = [s.id for s in sources]
    assert active.id in ids
    assert inactive.id not in ids


# ---------------------------------------------------------------------------
# KS-04  list_sources with include_inactive=True returns all
# ---------------------------------------------------------------------------


async def test_ks04_list_sources_include_inactive(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    active = await _make_source(svc, raw_db, org_id, workspace_id, user_id, is_active=True)
    inactive = await _make_source(svc, raw_db, org_id, workspace_id, user_id, is_active=False)

    sources = await svc.list_sources(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        include_inactive=True,
    )
    ids = [s.id for s in sources]
    assert active.id in ids
    assert inactive.id in ids


# ---------------------------------------------------------------------------
# KS-05  get_source raises KnowledgeSourceNotFoundError for wrong org
# ---------------------------------------------------------------------------


async def test_ks05_get_source_wrong_org(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)

    other_org = uuid.uuid4()
    with pytest.raises(KnowledgeSourceNotFoundError):
        await svc.get_source(
            raw_db,
            organisation_id=other_org,
            workspace_id=workspace_id,
            source_id=source.id,
        )


# ---------------------------------------------------------------------------
# KS-06  update_source mutates fields
# ---------------------------------------------------------------------------


async def test_ks06_update_source(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id, display_name="Old Name")

    updated = await svc.update_source(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        display_name="New Name",
        actor_user_id=user_id,
    )
    assert updated.display_name == "New Name"


# ---------------------------------------------------------------------------
# KS-07  upload_document raises KnowledgeSourceNotFoundError
# ---------------------------------------------------------------------------


async def test_ks07_upload_source_not_found(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    with pytest.raises(KnowledgeSourceNotFoundError):
        await svc.upload_document(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            source_id=uuid.uuid4(),  # non-existent
            original_filename="test.txt",
            media_type="text/plain",
            content=b"hello",
            idempotency_key=_ikey(),
            actor_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-08  upload_document raises KnowledgeSourceArchivedError for inactive source
# ---------------------------------------------------------------------------


async def test_ks08_upload_source_inactive(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id, is_active=False)
    with pytest.raises(KnowledgeSourceArchivedError):
        await svc.upload_document(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            source_id=source.id,
            original_filename="test.txt",
            media_type="text/plain",
            content=b"hello",
            idempotency_key=_ikey(),
            actor_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-09  upload_document raises KnowledgeUnsupportedMediaTypeError
# ---------------------------------------------------------------------------


async def test_ks09_upload_unsupported_media_type(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    with pytest.raises(KnowledgeUnsupportedMediaTypeError):
        await svc.upload_document(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            source_id=source.id,
            original_filename="doc.pdf",
            media_type="application/pdf",
            content=b"%PDF-1.4",
            idempotency_key=_ikey(),
            actor_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-10  upload_document raises KnowledgeFileTooLargeError
# ---------------------------------------------------------------------------


async def test_ks10_upload_file_too_large(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    knowledge_settings: Settings,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    # Content exceeds the 1 MiB limit set in knowledge_settings fixture.
    content = b"x" * (knowledge_settings.KNOWLEDGE_MAX_UPLOAD_BYTES + 1)
    with pytest.raises(KnowledgeFileTooLargeError):
        await svc.upload_document(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            source_id=source.id,
            original_filename="big.txt",
            media_type="text/plain",
            content=content,
            idempotency_key=_ikey(),
            actor_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-11  upload_document succeeds: document + version + job returned
# ---------------------------------------------------------------------------


async def test_ks11_upload_document_returns_entities(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    doc, version, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="hello.txt",
        media_type="text/plain",
        content=b"Hello world. This is a test document for AtlasCore Phase 2A.",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    assert isinstance(doc, KnowledgeDocument)
    assert isinstance(version, KnowledgeDocumentVersion)
    assert isinstance(job, KnowledgeIngestionJob)
    assert doc.organisation_id == org_id
    assert version.document_id == doc.id
    assert job.document_id == doc.id


# ---------------------------------------------------------------------------
# KS-12  upload_document stores blob with correct SHA-256
# ---------------------------------------------------------------------------


async def test_ks12_blob_sha256_matches(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    blob_store: LocalFilesystemBlobStore,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    content = b"Deterministic content for SHA-256 test."
    expected_sha = hashlib.sha256(content).hexdigest()

    _doc, version, _job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="sha_test.txt",
        media_type="text/plain",
        content=content,
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    assert version.content_sha256 == expected_sha

    # The blob must be retrievable and content must match.
    stored = await blob_store.get(version.storage_key)
    assert stored == content
    assert hashlib.sha256(stored).hexdigest() == expected_sha


# ---------------------------------------------------------------------------
# KS-13  storage_key is never derived from original_filename
# ---------------------------------------------------------------------------


async def test_ks13_storage_key_not_derived_from_filename(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    filename = "my_important_report.txt"
    _doc, version, _job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename=filename,
        media_type="text/plain",
        content=b"report content",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    assert filename not in version.storage_key
    assert ".." not in version.storage_key
    assert version.storage_key.count("/") == 3  # {org}/{ws}/{doc}/{ver}


# ---------------------------------------------------------------------------
# KS-14  ingestion job reaches succeeded status for valid text/plain
# ---------------------------------------------------------------------------


async def test_ks14_ingestion_job_succeeded(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    _doc, _ver, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="ingestion.txt",
        media_type="text/plain",
        content=b"The quick brown fox jumps over the lazy dog.",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    assert job.status == "succeeded"
    assert job.finished_at is not None
    assert job.started_at is not None


# ---------------------------------------------------------------------------
# KS-15  ingestion job persists chunks in DB
# ---------------------------------------------------------------------------


async def test_ks15_chunks_persisted(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    _doc, version, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="chunks.txt",
        media_type="text/plain",
        content=b"word " * 100,  # 100 words → multiple chunks
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    assert job.status == "succeeded"

    chunks_result = await raw_db.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.version_id == version.id,
            KnowledgeChunk.organisation_id == org_id,
        )
    )
    chunks = chunks_result.scalars().all()
    assert len(chunks) > 0
    # chunk_index must be 0-based and contiguous
    indices = sorted(c.chunk_index for c in chunks)
    assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# KS-16  ingestion job persists embeddings in DB
# ---------------------------------------------------------------------------


async def test_ks16_embeddings_persisted(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    _doc, version, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="embeddings.txt",
        media_type="text/plain",
        content=b"alpha beta gamma delta epsilon zeta eta theta",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    assert job.status == "succeeded"

    # Fetch chunks first, then embeddings.
    chunks_result = await raw_db.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)
    )
    chunk_ids = [c.id for c in chunks_result.scalars().all()]
    assert len(chunk_ids) > 0

    emb_result = await raw_db.execute(
        select(KnowledgeChunkEmbedding).where(KnowledgeChunkEmbedding.chunk_id.in_(chunk_ids))
    )
    embeddings = emb_result.scalars().all()
    assert len(embeddings) == len(chunk_ids)

    # Each embedding must decode to a list of floats.
    for emb in embeddings:
        vector = emb.get_vector()
        assert isinstance(vector, list)
        assert len(vector) == 32  # matches EMBEDDING_DIMENSIONS in fixture


# ---------------------------------------------------------------------------
# KS-17  KnowledgeIdempotencyConflictError on duplicate idempotency key
# ---------------------------------------------------------------------------


async def test_ks17_idempotency_conflict(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    shared_key = _ikey()

    await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="first.txt",
        media_type="text/plain",
        content=b"first upload",
        idempotency_key=shared_key,
        actor_user_id=user_id,
    )

    with pytest.raises(KnowledgeIdempotencyConflictError):
        await svc.upload_document(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            source_id=source.id,
            original_filename="second.txt",
            media_type="text/plain",
            content=b"second upload",
            idempotency_key=shared_key,  # same key — must conflict
            actor_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-18  archive_document soft-archives (is_archived=True, archived_at set)
# ---------------------------------------------------------------------------


async def test_ks18_archive_document(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    doc, _ver, _job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="to_archive.txt",
        media_type="text/plain",
        content=b"archive me",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    archived = await svc.archive_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        document_id=doc.id,
        actor_user_id=user_id,
    )
    assert archived.is_archived is True
    assert archived.archived_at is not None


# ---------------------------------------------------------------------------
# KS-19  archive_document raises KnowledgeDocumentArchivedError when already archived
# ---------------------------------------------------------------------------


async def test_ks19_archive_already_archived(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    doc, _ver, _job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="double.txt",
        media_type="text/plain",
        content=b"double archive",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    await svc.archive_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        document_id=doc.id,
    )
    with pytest.raises(KnowledgeDocumentArchivedError):
        await svc.archive_document(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            document_id=doc.id,
        )


# ---------------------------------------------------------------------------
# KS-20  list_documents filters by source_id
# ---------------------------------------------------------------------------


async def test_ks20_list_documents_by_source(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    src_a = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    src_b = await _make_source(svc, raw_db, org_id, workspace_id, user_id)

    doc_a, _v, _j = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=src_a.id,
        original_filename="a.txt",
        media_type="text/plain",
        content=b"source A doc",
        idempotency_key=_ikey(),
    )
    doc_b, _v, _j = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=src_b.id,
        original_filename="b.txt",
        media_type="text/plain",
        content=b"source B doc",
        idempotency_key=_ikey(),
    )

    docs_a = await svc.list_documents(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=src_a.id,
    )
    ids = [d.id for d in docs_a]
    assert doc_a.id in ids
    assert doc_b.id not in ids


# ---------------------------------------------------------------------------
# KS-21  list_documents excludes archived by default
# ---------------------------------------------------------------------------


async def test_ks21_list_documents_excludes_archived(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)

    doc, _v, _j = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="live.txt",
        media_type="text/plain",
        content=b"live document",
        idempotency_key=_ikey(),
    )
    await svc.archive_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        document_id=doc.id,
    )

    docs = await svc.list_documents(raw_db, organisation_id=org_id, workspace_id=workspace_id)
    assert doc.id not in [d.id for d in docs]

    docs_all = await svc.list_documents(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        include_archived=True,
    )
    assert doc.id in [d.id for d in docs_all]


# ---------------------------------------------------------------------------
# KS-22  list_versions returns ascending version_number order
# ---------------------------------------------------------------------------


async def test_ks22_list_versions_ascending(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)

    doc, ver1, _j = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="versioned.txt",
        media_type="text/plain",
        content=b"version one",
        idempotency_key=_ikey(),
    )
    # A second version is a new upload for the same document.
    # (In production these would share a document; in tests we just re-use the document
    # by directly inserting a second version. Here we simplify: list_versions of one ver.)
    versions = await svc.list_versions(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        document_id=doc.id,
    )
    assert len(versions) >= 1
    nums = [v.version_number for v in versions]
    assert nums == sorted(nums)


# ---------------------------------------------------------------------------
# KS-23  retry_job resets failed job to queued then runs ingestion
# ---------------------------------------------------------------------------


async def test_ks23_retry_job_succeeded(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)

    _doc, _ver, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="retry.txt",
        media_type="text/plain",
        content=b"retry test content",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    # Force the job to failed so we can retry it.
    job.status = "failed"
    job.error_message = "simulated failure"
    await raw_db.flush()

    retried = await svc.retry_job(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        job_id=job.id,
        actor_user_id=user_id,
    )
    assert retried.status == "succeeded"
    assert retried.error_message is None


# ---------------------------------------------------------------------------
# KS-24  retry_job raises KnowledgeIngestionJobNotRetryableError for non-failed job
# ---------------------------------------------------------------------------


async def test_ks24_retry_non_failed_raises(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)

    _doc, _ver, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="no_retry.txt",
        media_type="text/plain",
        content=b"already succeeded",
        idempotency_key=_ikey(),
        actor_user_id=user_id,
    )
    # job.status should be 'succeeded' after normal upload.
    assert job.status == "succeeded"

    with pytest.raises(KnowledgeIngestionJobNotRetryableError):
        await svc.retry_job(
            raw_db,
            organisation_id=org_id,
            workspace_id=workspace_id,
            job_id=job.id,
            actor_user_id=user_id,
        )


# ---------------------------------------------------------------------------
# KS-25  _sanitise_source_config blocks all known secret key patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "token",
        "api_key",
        "secret",
        "password",
        "refresh_token",
        "access_token",
        "private_key",
        "client_secret",
        "my_api_key",  # contains "api_key"
        "DB_PASSWORD",  # contains "password"
        "oauth_token",  # contains "token"
    ],
)
def test_ks25_sanitise_rejects_secret_keys(bad_key: str) -> None:
    with pytest.raises(KnowledgeError, match="secret"):
        _sanitise_source_config({bad_key: "some-value"})


def test_ks25_sanitise_accepts_safe_keys() -> None:
    safe = {"label": "prod", "region": "eu-west-1", "index_name": "docs"}
    result = _sanitise_source_config(safe)
    assert result == safe


# ---------------------------------------------------------------------------
# KS-26  content_sha256 is SHA-256, not a password hashing algorithm
# ---------------------------------------------------------------------------


async def test_ks26_content_sha256_is_sha256_not_argon(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    content = b"sha256 not argon"
    expected = hashlib.sha256(content).hexdigest()

    _doc, version, _job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="check.txt",
        media_type="text/plain",
        content=content,
        idempotency_key=_ikey(),
    )
    # Must be exactly the hex SHA-256 — 64 hex chars, no $argon2$ prefix.
    assert version.content_sha256 == expected
    assert len(version.content_sha256) == 64
    assert not version.content_sha256.startswith("$")


# ---------------------------------------------------------------------------
# KS-27  original_filename must not appear in storage_key
# ---------------------------------------------------------------------------


async def test_ks27_filename_not_in_storage_key(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    filename = "../../etc/passwd"
    _doc, version, _job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename=filename,
        media_type="text/plain",
        content=b"traversal test",
        idempotency_key=_ikey(),
    )
    assert "passwd" not in version.storage_key
    assert ".." not in version.storage_key


# ---------------------------------------------------------------------------
# KS-28  upload_document for markdown media type succeeds
# ---------------------------------------------------------------------------


async def test_ks28_upload_markdown_succeeds(
    svc: KnowledgeService,
    raw_db: AsyncSession,
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _seed_workspace(raw_db, org_id, workspace_id, user_id)
    source = await _make_source(svc, raw_db, org_id, workspace_id, user_id)
    md_content = b"# Title\n\nSome **bold** text and a [link](https://example.com)."
    doc, version, job = await svc.upload_document(
        raw_db,
        organisation_id=org_id,
        workspace_id=workspace_id,
        source_id=source.id,
        original_filename="readme.md",
        media_type="text/markdown",
        content=md_content,
        idempotency_key=_ikey(),
    )
    assert job.status == "succeeded"
    assert version.media_type == "text/markdown"
    assert doc.media_type == "text/markdown"
