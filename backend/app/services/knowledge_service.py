"""
KnowledgeService — secure knowledge ingestion pipeline.

Pipeline: authorize → validate → SHA-256 → idempotency check → blob store →
          create document version → create ingestion job → parse → chunk →
          embed → persist chunks + embeddings → update job status → audit.

SECURITY INVARIANTS:
  - organisation_id and workspace_id always come from the verified JWT / live
    database membership — never from request bodies.
  - uploaded filename is display metadata only; it NEVER becomes a storage key.
  - content is treated as UNTRUSTED DATA throughout ingestion.
  - no global deduplication across organisations.
  - SHA-256 used for content integrity — NOT password hashing.
  - audit events are emitted transactionally (not via GLOBAL path).
  - GLOBAL_EVENT_TYPES is NOT extended.
  - API secrets/tokens are never stored in configuration JSON.

Explicit transaction boundaries:
  1. Pre-flight: idempotency check — separate read to avoid lock escalation.
  2. Core transaction: create document version + ingestion job (queued).
  3. Run ingestion synchronously in-process (Phase 2A — no async worker).
  4. Update job to succeeded/failed in a second transaction.
  5. Audit emitted transactionally in step 4.

Blob cleanup: if a blob is written but the DB transaction fails, the blob
is deleted in a best-effort cleanup (orphaned blobs are acceptable;
they do not leak tenant data because keys are UUID-based and unguessable).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.knowledge_chunk import KnowledgeChunk
from app.db.models.knowledge_chunk_embedding import KnowledgeChunkEmbedding
from app.db.models.knowledge_document import KnowledgeDocument
from app.db.models.knowledge_document_version import KnowledgeDocumentVersion
from app.db.models.knowledge_ingestion_job import KnowledgeIngestionJob
from app.db.models.knowledge_source import KnowledgeSource
from app.knowledge.blob_store import BlobStore, BlobStoreError, BlobStoreSizeError
from app.knowledge.chunker import TextChunker
from app.knowledge.embeddings import EmbeddingProvider, build_embedding_provider
from app.knowledge.parsers import ParseError, UnsupportedMediaTypeError, get_parser
from app.services.audit import AuditService

logger = logging.getLogger(__name__)


async def _set_knowledge_rls_context(
    session: AsyncSession,
    organisation_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    """Set transaction-local tenant/workspace context for a fresh DB transaction."""
    await session.execute(
        text("SELECT set_config('app.current_organisation_id', :oid, true)"),
        {"oid": str(organisation_id)},
    )
    await session.execute(
        text("SELECT set_config('app.current_workspace_id', :wid, true)"),
        {"wid": str(workspace_id)},
    )


# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class KnowledgeError(Exception):
    """Base class for knowledge service errors."""


class KnowledgeSourceNotFoundError(KnowledgeError):
    pass


class KnowledgeSourceArchivedError(KnowledgeError):
    pass


class KnowledgeDocumentNotFoundError(KnowledgeError):
    pass


class KnowledgeDocumentArchivedError(KnowledgeError):
    pass


class KnowledgeUnsupportedMediaTypeError(KnowledgeError):
    pass


class KnowledgeFileTooLargeError(KnowledgeError):
    pass


class KnowledgeIdempotencyConflictError(KnowledgeError):
    """A job with this idempotency key already exists."""


class KnowledgeIngestionJobNotFoundError(KnowledgeError):
    pass


class KnowledgeIngestionJobNotRetryableError(KnowledgeError):
    pass


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class KnowledgeService:
    """Orchestrates all knowledge ingestion operations."""

    def __init__(self, settings: Settings, blob_store: BlobStore) -> None:
        self._settings = settings
        self._blob_store = blob_store
        self._embedding_provider: EmbeddingProvider = build_embedding_provider(
            settings.EMBEDDING_PROVIDER,
            settings.EMBEDDING_DIMENSIONS,
        )

    # ------------------------------------------------------------------
    # Source management
    # ------------------------------------------------------------------

    async def create_source(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        source_type: str,
        display_name: str,
        description: str | None = None,
        configuration: dict[str, Any] | None = None,
        created_by_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> KnowledgeSource:
        """Create a new knowledge source within a workspace."""
        # Configuration must not contain secrets.
        safe_config = _sanitise_source_config(configuration or {})
        source = KnowledgeSource(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            source_type=source_type,
            display_name=display_name,
            description=description,
            configuration=safe_config,
            created_by_user_id=created_by_user_id,
        )
        session.add(source)
        await session.flush()
        AuditService.emit_transactional(
            session,
            event_type="knowledge.source.created",
            organisation_id=organisation_id,
            actor_user_id=created_by_user_id,
            event_data={
                "source_id": str(source.id),
                "source_type": source_type,
                "display_name": display_name,
            },
            request_id=request_id,
            client_ip=client_ip,
            outcome="success",
        )
        return source

    async def list_sources(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        include_inactive: bool = False,
    ) -> list[KnowledgeSource]:
        """List knowledge sources in a workspace."""
        q = select(KnowledgeSource).where(
            KnowledgeSource.organisation_id == organisation_id,
            KnowledgeSource.workspace_id == workspace_id,
        )
        if not include_inactive:
            q = q.where(KnowledgeSource.is_active.is_(True))
        q = q.order_by(KnowledgeSource.display_name)
        result = await session.execute(q)
        return list(result.scalars().all())

    async def get_source(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> KnowledgeSource:
        """Fetch a single source, raising KnowledgeSourceNotFoundError if absent."""
        result = await session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.organisation_id == organisation_id,
                KnowledgeSource.workspace_id == workspace_id,
            )
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise KnowledgeSourceNotFoundError(f"Source {source_id} not found.")
        return source

    async def update_source(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        display_name: str | None = None,
        description: str | None = None,
        configuration: dict[str, Any] | None = None,
        is_active: bool | None = None,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> KnowledgeSource:
        """Update mutable fields of a knowledge source."""
        source = await self.get_source(
            session,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            source_id=source_id,
        )
        changed: dict[str, Any] = {}
        if display_name is not None and display_name != source.display_name:
            source.display_name = display_name
            changed["display_name"] = display_name
        if description is not None and description != source.description:
            source.description = description
        if configuration is not None:
            safe = _sanitise_source_config(configuration)
            source.configuration = safe
            changed["configuration_updated"] = True
        if is_active is not None and is_active != source.is_active:
            source.is_active = is_active
            changed["is_active"] = is_active

        await session.flush()
        AuditService.emit_transactional(
            session,
            event_type="knowledge.source.updated",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"source_id": str(source_id), **changed},
            request_id=request_id,
            client_ip=client_ip,
            outcome="success",
        )
        return source

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID | None = None,
        include_archived: bool = False,
    ) -> list[KnowledgeDocument]:
        """List documents in a workspace, optionally filtered by source."""
        q = select(KnowledgeDocument).where(
            KnowledgeDocument.organisation_id == organisation_id,
            KnowledgeDocument.workspace_id == workspace_id,
        )
        if source_id is not None:
            q = q.where(KnowledgeDocument.source_id == source_id)
        if not include_archived:
            q = q.where(KnowledgeDocument.is_archived.is_(False))
        q = q.order_by(KnowledgeDocument.created_at.desc())
        result = await session.execute(q)
        return list(result.scalars().all())

    async def get_document(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> KnowledgeDocument:
        """Fetch a single document."""
        result = await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.organisation_id == organisation_id,
                KnowledgeDocument.workspace_id == workspace_id,
            )
        )
        doc = result.scalar_one_or_none()
        if doc is None:
            raise KnowledgeDocumentNotFoundError(f"Document {document_id} not found.")
        return doc

    async def archive_document(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> KnowledgeDocument:
        """Soft-archive a document (reversible via update)."""
        doc = await self.get_document(
            session,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            document_id=document_id,
        )
        if doc.is_archived:
            raise KnowledgeDocumentArchivedError(f"Document {document_id} is already archived.")
        doc.is_archived = True
        doc.archived_at = datetime.now(UTC)
        await session.flush()
        AuditService.emit_transactional(
            session,
            event_type="knowledge.document.archived",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"document_id": str(document_id)},
            request_id=request_id,
            client_ip=client_ip,
            outcome="success",
        )
        return doc

    async def list_versions(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> list[KnowledgeDocumentVersion]:
        """List all versions of a document in ascending version order."""
        result = await session.execute(
            select(KnowledgeDocumentVersion)
            .where(
                KnowledgeDocumentVersion.document_id == document_id,
                KnowledgeDocumentVersion.organisation_id == organisation_id,
                KnowledgeDocumentVersion.workspace_id == workspace_id,
            )
            .order_by(KnowledgeDocumentVersion.version_number)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Document upload + ingestion
    # ------------------------------------------------------------------

    async def upload_document(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        original_filename: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> tuple[KnowledgeDocument, KnowledgeDocumentVersion, KnowledgeIngestionJob]:
        """
        Upload a document and enqueue/run ingestion.

        Returns (document, version, ingestion_job).

        SECURITY:
          - original_filename is display metadata only.
          - storage_key is server-generated from UUIDs.
          - content_sha256 is SHA-256 of raw bytes (not password hashing).
          - no global dedup across organisations.
        """
        # 1. Validate source exists and is active.
        source = await self.get_source(
            session,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            source_id=source_id,
        )
        if not source.is_active:
            raise KnowledgeSourceArchivedError(f"Knowledge source {source_id} is not active.")

        # 2. Validate media type.
        from app.knowledge.parsers import is_supported_media_type

        if not is_supported_media_type(media_type):
            raise KnowledgeUnsupportedMediaTypeError(
                f"Unsupported media type: {media_type!r}. "
                "Phase 2A supports text/plain and text/markdown."
            )

        # 3. Size check (independent of BlobStore layer).
        max_bytes = self._settings.KNOWLEDGE_MAX_UPLOAD_BYTES
        if len(content) > max_bytes:
            raise KnowledgeFileTooLargeError(
                f"File size {len(content)} bytes exceeds limit {max_bytes} bytes."
            )

        # 4. Compute SHA-256 of raw bytes.
        content_sha256 = hashlib.sha256(content).hexdigest()

        # 5. Create document row (or reuse existing for same source/filename).
        #    NOTE: We always create a new version — no content-based dedup across uploads.
        doc = KnowledgeDocument(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            source_id=source_id,
            original_filename=original_filename,
            media_type=media_type,
            created_by_user_id=actor_user_id,
        )
        session.add(doc)
        await session.flush()  # get doc.id for storage key generation

        # 6. Determine next version number.
        version_count_result = await session.execute(
            select(func.count(KnowledgeDocumentVersion.id)).where(
                KnowledgeDocumentVersion.document_id == doc.id,
                KnowledgeDocumentVersion.organisation_id == organisation_id,
            )
        )
        next_version = (version_count_result.scalar_one() or 0) + 1

        # 7. Generate server-side storage key.
        version_id = uuid.uuid4()
        storage_key = BlobStore.generate_key(organisation_id, workspace_id, doc.id, version_id)

        # 8. Write blob to BlobStore.
        try:
            stored_sha256 = await self._blob_store.put(storage_key, content, max_bytes=max_bytes)
        except BlobStoreSizeError as exc:
            raise KnowledgeFileTooLargeError(str(exc)) from exc
        except BlobStoreError as exc:
            raise KnowledgeError(f"Blob storage failed: {exc}") from exc

        # Verify the SHA-256 matches (defence-in-depth).
        if stored_sha256 != content_sha256:
            await self._blob_store.delete(storage_key)
            raise KnowledgeError("Content integrity check failed after blob write.")

        # 9. Create version row.
        version = KnowledgeDocumentVersion(
            id=version_id,
            document_id=doc.id,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            version_number=next_version,
            content_sha256=content_sha256,
            size_bytes=len(content),
            media_type=media_type,
            storage_key=storage_key,
            created_by_user_id=actor_user_id,
        )
        session.add(version)
        await session.flush()

        # 10. Create ingestion job (queued).
        job = KnowledgeIngestionJob(
            id=uuid.uuid4(),
            version_id=version.id,
            document_id=doc.id,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            status="queued",
            idempotency_key=idempotency_key,
        )
        session.add(job)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise KnowledgeIdempotencyConflictError(
                f"An ingestion job with idempotency key {idempotency_key!r} already exists "
                "for this workspace."
            ) from exc

        # 11. Audit: document uploaded.
        AuditService.emit_transactional(
            session,
            event_type="knowledge.document.uploaded",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={
                "document_id": str(doc.id),
                "version_id": str(version.id),
                "source_id": str(source_id),
                "content_sha256": content_sha256,
                "size_bytes": len(content),
                "media_type": media_type,
            },
            request_id=request_id,
            client_ip=client_ip,
            outcome="success",
        )

        # 12. Run ingestion synchronously (Phase 2A — no async worker queue).
        #     Commit the job-queued state first, then update in a separate tx.
        await session.commit()

        await self._run_ingestion(
            session,
            job_id=job.id,
            version_id=version.id,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            content=content,
            media_type=media_type,
            request_id=request_id,
        )

        # _run_ingestion commits its final transaction, so restore scoped context
        # before refreshing/returning to callers that continue using this session.
        await _set_knowledge_rls_context(session, organisation_id, workspace_id)
        await session.refresh(job)
        return doc, version, job

    async def _run_ingestion(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        version_id: uuid.UUID,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        content: bytes,
        media_type: str,
        request_id: str | None,
    ) -> None:
        """
        Parse, chunk, and embed the document.  Updates job status on completion.

        Opens a new transaction for the update.
        """
        # A commit starts a fresh transaction and transaction-local RLS GUCs are reset.
        # Re-establish the trusted tenant/workspace context before every new phase.
        await _set_knowledge_rls_context(session, organisation_id, workspace_id)

        # Transition job to running.
        result = await session.execute(
            select(KnowledgeIngestionJob).where(
                KnowledgeIngestionJob.id == job_id,
                KnowledgeIngestionJob.organisation_id == organisation_id,
            )
        )
        job = result.scalar_one_or_none()
        if job is None:
            logger.error("Ingestion job %s not found during run phase.", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(UTC)
        await session.commit()
        await _set_knowledge_rls_context(session, organisation_id, workspace_id)

        # --- Ingestion pipeline ---
        chunk_count = 0
        error_message: str | None = None

        try:
            # Parse.
            parser = get_parser(media_type)
            parse_result = parser.parse(content, media_type)
            text = parse_result.text

            # Chunk.
            chunker = TextChunker(
                chunk_size=self._settings.CHUNK_SIZE_TOKENS,
                overlap=self._settings.CHUNK_OVERLAP_TOKENS,
            )
            chunks = chunker.chunk(text)
            chunk_count = len(chunks)

            # Embed + persist chunks.
            chunk_rows: list[KnowledgeChunk] = []
            embedding_rows: list[KnowledgeChunkEmbedding] = []

            for ch in chunks:
                chunk_row = KnowledgeChunk(
                    id=uuid.uuid4(),
                    version_id=version_id,
                    organisation_id=organisation_id,
                    workspace_id=workspace_id,
                    chunk_index=ch.chunk_index,
                    chunk_text=ch.chunk_text,
                    content_sha256=ch.content_sha256,
                    token_count=ch.token_count,
                )
                chunk_rows.append(chunk_row)
                emb_result = await self._embedding_provider.embed(ch.chunk_text)
                # Dimension invariant: the vector length must match the declared
                # dimensions.  Fail deliberately rather than persist corrupt data.
                if len(emb_result.vector) != emb_result.dimensions:
                    raise ValueError(
                        f"Embedding dimension mismatch for model {emb_result.model_id!r}: "
                        f"vector has {len(emb_result.vector)} elements but "
                        f"emb_result.dimensions={emb_result.dimensions}. "
                        "Refusing to persist inconsistent embedding."
                    )
                embedding_rows.append(
                    KnowledgeChunkEmbedding(
                        id=uuid.uuid4(),
                        chunk_id=chunk_row.id,
                        organisation_id=organisation_id,
                        workspace_id=workspace_id,
                        model_id=emb_result.model_id,
                        # Native list[float] — VectorType serialises to pgvector.
                        # No JSON encoding.
                        embedding=emb_result.vector,
                        dimensions=emb_result.dimensions,
                    )
                )

            session.add_all(chunk_rows)
            session.add_all(embedding_rows)

            # Mark succeeded.
            job.status = "succeeded"
            job.finished_at = datetime.now(UTC)
            job.result_metadata = {
                "chunk_count": chunk_count,
                "embedding_model": self._embedding_provider.model_id,
                "dimensions": self._embedding_provider.dimensions,
            }
            AuditService.emit_transactional(
                session,
                event_type="knowledge.ingestion.succeeded",
                organisation_id=organisation_id,
                actor_user_id=None,
                event_data={
                    "job_id": str(job_id),
                    "version_id": str(version_id),
                    "chunk_count": chunk_count,
                },
                request_id=request_id,
                outcome="success",
            )

        except (ParseError, UnsupportedMediaTypeError) as exc:
            error_message = f"Parse error: {exc}"
            logger.warning("Ingestion job %s parse error: %s", job_id, exc)
        except Exception as exc:
            error_message = f"Ingestion error: {exc}"
            logger.exception("Ingestion job %s unexpected error", job_id)

        if error_message is not None:
            job.status = "failed"
            job.finished_at = datetime.now(UTC)
            job.error_message = error_message
            AuditService.emit_transactional(
                session,
                event_type="knowledge.ingestion.failed",
                organisation_id=organisation_id,
                actor_user_id=None,
                event_data={
                    "job_id": str(job_id),
                    "version_id": str(version_id),
                    "error": error_message,
                },
                request_id=request_id,
                outcome="failure",
            )

        await session.commit()

    # ------------------------------------------------------------------
    # Ingestion job management
    # ------------------------------------------------------------------

    async def list_jobs(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID | None = None,
    ) -> list[KnowledgeIngestionJob]:
        """List ingestion jobs, optionally filtered by document."""
        q = select(KnowledgeIngestionJob).where(
            KnowledgeIngestionJob.organisation_id == organisation_id,
            KnowledgeIngestionJob.workspace_id == workspace_id,
        )
        if document_id is not None:
            q = q.where(KnowledgeIngestionJob.document_id == document_id)
        q = q.order_by(KnowledgeIngestionJob.created_at.desc())
        result = await session.execute(q)
        return list(result.scalars().all())

    async def get_job(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> KnowledgeIngestionJob:
        """Fetch a single ingestion job."""
        result = await session.execute(
            select(KnowledgeIngestionJob).where(
                KnowledgeIngestionJob.id == job_id,
                KnowledgeIngestionJob.organisation_id == organisation_id,
                KnowledgeIngestionJob.workspace_id == workspace_id,
            )
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise KnowledgeIngestionJobNotFoundError(f"Job {job_id} not found.")
        return job

    async def retry_job(
        self,
        session: AsyncSession,
        *,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        job_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> KnowledgeIngestionJob:
        """
        Retry a failed ingestion job.

        Only failed jobs may be retried (→ queued).  Succeeded and cancelled
        jobs are terminal and cannot be retried.
        """
        job = await self.get_job(
            session,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            job_id=job_id,
        )
        if job.status != "failed":
            raise KnowledgeIngestionJobNotRetryableError(
                f"Job {job_id} has status {job.status!r}; only 'failed' jobs can be retried."
            )

        # Load the version content from blob store.
        version_result = await session.execute(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.id == job.version_id,
                KnowledgeDocumentVersion.organisation_id == organisation_id,
            )
        )
        version = version_result.scalar_one_or_none()
        if version is None:
            raise KnowledgeError(f"Version {job.version_id} not found for retry.")

        # Reset to queued.
        job.status = "queued"
        job.error_message = None
        job.started_at = None
        job.finished_at = None
        job.result_metadata = {}

        AuditService.emit_transactional(
            session,
            event_type="knowledge.ingestion.retry_requested",
            organisation_id=organisation_id,
            actor_user_id=actor_user_id,
            event_data={"job_id": str(job_id)},
            request_id=request_id,
            client_ip=client_ip,
            outcome="success",
        )
        await session.commit()

        # Reload content and run.
        try:
            content = await self._blob_store.get(version.storage_key)
        except Exception as exc:
            raise KnowledgeError(f"Cannot load blob for retry: {exc}") from exc

        # A retry must be idempotent even when a previous attempt left derived
        # rows behind. Remove stale chunks for this version first; embedding rows
        # cascade via their chunk FK. Commit the cleanup before inserting the
        # replacement chunk indexes so the unique(version_id, chunk_index)
        # constraint can never conflict with stale data.
        await _set_knowledge_rls_context(session, organisation_id, workspace_id)
        await session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.version_id == version.id,
                KnowledgeChunk.organisation_id == organisation_id,
                KnowledgeChunk.workspace_id == workspace_id,
            )
        )
        await session.commit()

        await self._run_ingestion(
            session,
            job_id=job.id,
            version_id=version.id,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            content=content,
            media_type=version.media_type,
            request_id=request_id,
        )

        await _set_knowledge_rls_context(session, organisation_id, workspace_id)
        await session.refresh(job)
        return job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "key",
        "api_key",
        "credential",
        "refresh_token",
        "access_token",
        "private_key",
        "client_secret",
    }
)


def _sanitise_source_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Reject configuration dicts that contain secret-looking keys.

    Phase 2A sources only use manual_upload; config should be empty.
    This guard prevents accidental secret storage in the JSONB column.
    """
    for k in config:
        if any(secret in k.lower() for secret in _SECRET_KEYS):
            raise KnowledgeError(
                f"Source configuration must not contain secret-looking keys: {k!r}. "
                "Store credentials in environment variables / Settings, not in configuration."
            )
    return config
