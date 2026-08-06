"""
Knowledge endpoints — /api/v1/knowledge.

Phase 2A: sources CRUD, documents list/archive, versions, jobs.
Phase 2B: hybrid retrieval search endpoint (ranked evidence only).
NO answer generation, NO chat, NO RAG inference, NO agent loops.

SECURITY:
  - organisation_id and user_id are sourced from the verified JWT — never
    from request bodies or URL parameters.
  - workspace_id is validated through ValidatedWorkspaceId (deps.py) before
    it is used: the URL path workspace_id must match the JWT workspace_id
    claim AND have an active WorkspaceMembership row.  The raw path parameter
    is NEVER passed to OrganisationScopedSession or service methods — only
    the dependency-validated value is used.
  - The PostgreSQL workspace GUC (app.current_workspace_id) is therefore only
    ever set from an authenticated, live-membership-verified value.
  - uploaded filename is display metadata only; it never becomes a storage key.
  - file upload bytes are validated by the BlobStore size limit independently
    of the Content-Length header.
  - Permission checks enforce KNOWLEDGE_* permissions from the Phase 2A matrix.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.answering.schemas import (
    AnswerRequest,
    CitationResponse,
)
from app.answering.schemas import (
    GroundedAnswerResponse as GroundedAnswerAPIResponse,
)
from app.answering.service import GroundedAnswerService
from app.api.deps import (
    AppSettings,
    CurrentMembership,
    CurrentPayload,
    RequirePermission,
    ValidatedWorkspaceId,
)
from app.auth.permissions import Permission
from app.db.engine import OrganisationScopedSession
from app.knowledge.blob_store import LocalFilesystemBlobStore
from app.retrieval.schemas import RetrievalRequest, RetrievalResponse
from app.retrieval.service import (
    KnowledgeRetrievalService,
    RetrievalQueryError,
)
from app.schemas.knowledge import (
    KnowledgeDocumentResponse,
    KnowledgeDocumentVersionResponse,
    KnowledgeIngestionJobResponse,
    KnowledgeSourceCreateRequest,
    KnowledgeSourceResponse,
    KnowledgeSourceUpdateRequest,
    KnowledgeUploadResponse,
)
from app.services.knowledge_service import (
    KnowledgeDocumentArchivedError,
    KnowledgeDocumentNotFoundError,
    KnowledgeError,
    KnowledgeFileTooLargeError,
    KnowledgeIdempotencyConflictError,
    KnowledgeIngestionJobNotFoundError,
    KnowledgeIngestionJobNotRetryableError,
    KnowledgeService,
    KnowledgeSourceArchivedError,
    KnowledgeSourceNotFoundError,
    KnowledgeUnsupportedMediaTypeError,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ---------------------------------------------------------------------------
# Dependency: KnowledgeService instance (constructed per request)
# ---------------------------------------------------------------------------


def _get_knowledge_service(settings: AppSettings) -> KnowledgeService:
    blob_store = LocalFilesystemBlobStore(settings.KNOWLEDGE_STORAGE_ROOT)
    return KnowledgeService(settings, blob_store)


KnowledgeSvc = Annotated[KnowledgeService, Depends(_get_knowledge_service)]


def _get_retrieval_service(settings: AppSettings) -> KnowledgeRetrievalService:
    from app.knowledge.embeddings import build_embedding_provider

    provider = build_embedding_provider(
        model_id=settings.EMBEDDING_PROVIDER,
        dimensions=settings.EMBEDDING_DIMENSIONS,
    )
    return KnowledgeRetrievalService(embedding_provider=provider)


RetrievalSvc = Annotated[KnowledgeRetrievalService, Depends(_get_retrieval_service)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_to_response(source: object) -> KnowledgeSourceResponse:
    from app.db.models.knowledge_source import KnowledgeSource

    assert isinstance(source, KnowledgeSource)
    return KnowledgeSourceResponse(
        id=source.id,
        organisation_id=source.organisation_id,
        workspace_id=source.workspace_id,
        source_type=source.source_type,
        display_name=source.display_name,
        description=source.description,
        is_active=source.is_active,
        configuration=source.configuration,
        created_by_user_id=source.created_by_user_id,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _document_to_response(doc: object) -> KnowledgeDocumentResponse:
    from app.db.models.knowledge_document import KnowledgeDocument

    assert isinstance(doc, KnowledgeDocument)
    return KnowledgeDocumentResponse(
        id=doc.id,
        organisation_id=doc.organisation_id,
        workspace_id=doc.workspace_id,
        source_id=doc.source_id,
        original_filename=doc.original_filename,
        media_type=doc.media_type,
        is_archived=doc.is_archived,
        archived_at=doc.archived_at,
        created_by_user_id=doc.created_by_user_id,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _version_to_response(v: object) -> KnowledgeDocumentVersionResponse:
    from app.db.models.knowledge_document_version import KnowledgeDocumentVersion

    assert isinstance(v, KnowledgeDocumentVersion)
    return KnowledgeDocumentVersionResponse(
        id=v.id,
        document_id=v.document_id,
        organisation_id=v.organisation_id,
        workspace_id=v.workspace_id,
        version_number=v.version_number,
        content_sha256=v.content_sha256,
        size_bytes=v.size_bytes,
        media_type=v.media_type,
        # storage_key intentionally omitted — never returned to clients.
        created_by_user_id=v.created_by_user_id,
        created_at=v.created_at,
    )


def _job_to_response(job: object) -> KnowledgeIngestionJobResponse:
    from app.db.models.knowledge_ingestion_job import KnowledgeIngestionJob

    assert isinstance(job, KnowledgeIngestionJob)
    return KnowledgeIngestionJobResponse(
        id=job.id,
        version_id=job.version_id,
        document_id=job.document_id,
        organisation_id=job.organisation_id,
        workspace_id=job.workspace_id,
        status=job.status,
        idempotency_key=job.idempotency_key,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
        result_metadata=job.result_metadata,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _get_session_factory(settings: AppSettings) -> async_sessionmaker[AsyncSession]:
    from app.api.deps import get_session_factory

    return get_session_factory(settings)


# ---------------------------------------------------------------------------
# Sources — CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/sources",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeSourceResponse,
)
async def create_source(
    workspace_id: ValidatedWorkspaceId,
    body: KnowledgeSourceCreateRequest,
    request: Request,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_SOURCE_CREATE))],
) -> KnowledgeSourceResponse:
    """Create a new knowledge source within a workspace."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            source = await svc.create_source(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                source_type=body.source_type,
                display_name=body.display_name,
                description=body.description,
                configuration=body.configuration,
                created_by_user_id=payload.user_id,
                request_id=request.headers.get("X-Request-ID"),
                client_ip=request.client.host if request.client else None,
            )
        except KnowledgeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return _source_to_response(source)


@router.get(
    "/workspaces/{workspace_id}/sources",
    response_model=list[KnowledgeSourceResponse],
)
async def list_sources(
    workspace_id: ValidatedWorkspaceId,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
    include_inactive: bool = False,
) -> list[KnowledgeSourceResponse]:
    """List knowledge sources in a workspace."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        sources = await svc.list_sources(
            session,
            organisation_id=payload.organisation_id,
            workspace_id=workspace_id,
            include_inactive=include_inactive,
        )
        return [_source_to_response(s) for s in sources]


@router.get(
    "/workspaces/{workspace_id}/sources/{source_id}",
    response_model=KnowledgeSourceResponse,
)
async def get_source(
    workspace_id: ValidatedWorkspaceId,
    source_id: uuid.UUID,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
) -> KnowledgeSourceResponse:
    """Get a single knowledge source."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            source = await svc.get_source(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                source_id=source_id,
            )
        except KnowledgeSourceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _source_to_response(source)


@router.patch(
    "/workspaces/{workspace_id}/sources/{source_id}",
    response_model=KnowledgeSourceResponse,
)
async def update_source(
    workspace_id: ValidatedWorkspaceId,
    source_id: uuid.UUID,
    body: KnowledgeSourceUpdateRequest,
    request: Request,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_SOURCE_UPDATE))],
) -> KnowledgeSourceResponse:
    """Update a knowledge source."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            source = await svc.update_source(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                source_id=source_id,
                display_name=body.display_name,
                description=body.description,
                configuration=body.configuration,
                is_active=body.is_active,
                actor_user_id=payload.user_id,
                request_id=request.headers.get("X-Request-ID"),
                client_ip=request.client.host if request.client else None,
            )
        except KnowledgeSourceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _source_to_response(source)


# ---------------------------------------------------------------------------
# Documents — list, upload, archive
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/documents",
    response_model=list[KnowledgeDocumentResponse],
)
async def list_documents(
    workspace_id: ValidatedWorkspaceId,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
    source_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> list[KnowledgeDocumentResponse]:
    """List documents in a workspace."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        docs = await svc.list_documents(
            session,
            organisation_id=payload.organisation_id,
            workspace_id=workspace_id,
            source_id=source_id,
            include_archived=include_archived,
        )
        return [_document_to_response(d) for d in docs]


@router.post(
    "/workspaces/{workspace_id}/sources/{source_id}/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeUploadResponse,
)
async def upload_document(
    workspace_id: ValidatedWorkspaceId,
    source_id: uuid.UUID,
    request: Request,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    file: UploadFile,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_DOCUMENT_UPLOAD))],
    idempotency_key: str | None = None,
) -> KnowledgeUploadResponse:
    """
    Upload a document file and trigger ingestion.

    The original filename is stored as display metadata only.
    The server generates the storage key independently.
    """
    session_factory = _get_session_factory(settings)

    # Read upload into memory (bounded by max upload size).
    max_bytes = settings.KNOWLEDGE_MAX_UPLOAD_BYTES
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {max_bytes} bytes.",
        )

    media_type = file.content_type or "text/plain"
    original_filename = file.filename or "upload"
    key = idempotency_key or str(uuid.uuid4())

    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            doc, version, job = await svc.upload_document(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                source_id=source_id,
                original_filename=original_filename,
                media_type=media_type,
                content=content,
                idempotency_key=key,
                actor_user_id=payload.user_id,
                request_id=request.headers.get("X-Request-ID"),
                client_ip=request.client.host if request.client else None,
            )
        except KnowledgeSourceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KnowledgeSourceArchivedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeUnsupportedMediaTypeError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
            ) from exc
        except KnowledgeFileTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
            ) from exc
        except KnowledgeIdempotencyConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return KnowledgeUploadResponse(
            document=_document_to_response(doc),
            version=_version_to_response(version),
            ingestion_job=_job_to_response(job),
        )


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/archive",
    response_model=KnowledgeDocumentResponse,
)
async def archive_document(
    workspace_id: ValidatedWorkspaceId,
    document_id: uuid.UUID,
    request: Request,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_DOCUMENT_ARCHIVE))],
) -> KnowledgeDocumentResponse:
    """Soft-archive a knowledge document."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            doc = await svc.archive_document(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                document_id=document_id,
                actor_user_id=payload.user_id,
                request_id=request.headers.get("X-Request-ID"),
                client_ip=request.client.host if request.client else None,
            )
        except KnowledgeDocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KnowledgeDocumentArchivedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _document_to_response(doc)


# ---------------------------------------------------------------------------
# Document versions
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/versions",
    response_model=list[KnowledgeDocumentVersionResponse],
)
async def list_versions(
    workspace_id: ValidatedWorkspaceId,
    document_id: uuid.UUID,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
) -> list[KnowledgeDocumentVersionResponse]:
    """List versions of a document."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            versions = await svc.list_versions(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                document_id=document_id,
            )
        except KnowledgeDocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [_version_to_response(v) for v in versions]


# ---------------------------------------------------------------------------
# Ingestion jobs
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces/{workspace_id}/jobs",
    response_model=list[KnowledgeIngestionJobResponse],
)
async def list_jobs(
    workspace_id: ValidatedWorkspaceId,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
    document_id: uuid.UUID | None = None,
) -> list[KnowledgeIngestionJobResponse]:
    """List ingestion jobs for a workspace."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        jobs = await svc.list_jobs(
            session,
            organisation_id=payload.organisation_id,
            workspace_id=workspace_id,
            document_id=document_id,
        )
        return [_job_to_response(j) for j in jobs]


@router.get(
    "/workspaces/{workspace_id}/jobs/{job_id}",
    response_model=KnowledgeIngestionJobResponse,
)
async def get_job(
    workspace_id: ValidatedWorkspaceId,
    job_id: uuid.UUID,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
) -> KnowledgeIngestionJobResponse:
    """Get a single ingestion job."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            job = await svc.get_job(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                job_id=job_id,
            )
        except KnowledgeIngestionJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _job_to_response(job)


@router.post(
    "/workspaces/{workspace_id}/jobs/{job_id}/retry",
    response_model=KnowledgeIngestionJobResponse,
)
async def retry_job(
    workspace_id: ValidatedWorkspaceId,
    job_id: uuid.UUID,
    request: Request,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: KnowledgeSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_INGESTION_RETRY))],
) -> KnowledgeIngestionJobResponse:
    """Retry a failed ingestion job."""
    session_factory = _get_session_factory(settings)
    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            job = await svc.retry_job(
                session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                job_id=job_id,
                actor_user_id=payload.user_id,
                request_id=request.headers.get("X-Request-ID"),
                client_ip=request.client.host if request.client else None,
            )
        except KnowledgeIngestionJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KnowledgeIngestionJobNotRetryableError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _job_to_response(job)


# ---------------------------------------------------------------------------
# Phase 2B: hybrid retrieval search
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/search",
    response_model=RetrievalResponse,
    summary="Hybrid knowledge search (Phase 2B)",
    description=(
        "Execute a hybrid lexical + vector search over the workspace's "
        "ingested knowledge.  Returns ranked evidence chunks only.  "
        "No answer generation; no LLM inference.  "
        "workspace_id is validated via ValidatedWorkspaceId — a W1 token "
        "cannot retrieve W2 chunks."
    ),
)
async def search_knowledge(
    workspace_id: ValidatedWorkspaceId,
    body: RetrievalRequest,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: RetrievalSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
) -> RetrievalResponse:
    """
    Hybrid retrieval search endpoint.

    SECURITY:
    - workspace_id is ValidatedWorkspaceId (path == JWT == live membership).
    - organisation_id is sourced exclusively from the JWT payload.
    - source_ids / document_ids are bound SQL parameters; they cannot inject SQL.
    - Unknown filter IDs produce empty results — they do not disclose other tenants.
    - Retrieved content is UNTRUSTED DATA; it is never executed.
    - No LLM calls are made in this endpoint.
    """
    session_factory = _get_session_factory(settings)

    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        try:
            response = await svc.retrieve(
                session=session,
                organisation_id=payload.organisation_id,
                workspace_id=workspace_id,
                request=body,
            )
        except RetrievalQueryError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return response


# ---------------------------------------------------------------------------
# Phase 2C: grounded answering
# ---------------------------------------------------------------------------


def _get_answer_service(settings: AppSettings) -> GroundedAnswerService:
    """Construct GroundedAnswerService from current settings (per-request)."""
    from app.answering.citation import CitationValidator
    from app.answering.prompt import PromptBuilder
    from app.answering.provider import build_answer_provider
    from app.answering.service import GroundedAnswerService
    from app.answering.sufficiency import EvidenceSufficiencyPolicy
    from app.knowledge.embeddings import build_embedding_provider

    embedding_provider = build_embedding_provider(
        model_id=settings.EMBEDDING_PROVIDER,
        dimensions=settings.EMBEDDING_DIMENSIONS,
    )
    retrieval_svc = KnowledgeRetrievalService(embedding_provider=embedding_provider)
    # Demo mode forces deterministic provider regardless of ANSWER_PROVIDER config.
    # This prevents accidental real-LLM usage in environments without credentials.
    effective_provider = (
        "deterministic-test" if settings.ANSWER_DEMO_MODE else settings.ANSWER_PROVIDER
    )
    answer_provider = build_answer_provider(
        provider_id=effective_provider,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=settings.OPENAI_DEFAULT_CHAT_MODEL,
        openai_timeout=settings.OPENAI_TIMEOUT,
        openai_max_retries=settings.OPENAI_MAX_RETRIES,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        anthropic_model=settings.ANTHROPIC_DEFAULT_CHAT_MODEL,
        anthropic_timeout=settings.ANTHROPIC_TIMEOUT,
        anthropic_max_retries=settings.ANTHROPIC_MAX_RETRIES,
    )
    policy = EvidenceSufficiencyPolicy(
        require_medium=settings.ANSWER_REQUIRE_MEDIUM_BAND,
    )
    prompt_builder = PromptBuilder(
        max_evidence_items=settings.ANSWER_MAX_EVIDENCE_ITEMS,
        max_chars_per_chunk=settings.ANSWER_MAX_CHARS_PER_CHUNK,
    )
    citation_validator = CitationValidator(
        max_excerpt_chars=settings.ANSWER_MAX_EXCERPT_CHARS,
    )
    return GroundedAnswerService(
        retrieval_service=retrieval_svc,
        answer_provider=answer_provider,
        sufficiency_policy=policy,
        prompt_builder=prompt_builder,
        citation_validator=citation_validator,
        max_evidence_items=settings.ANSWER_MAX_EVIDENCE_ITEMS,
        min_hybrid_score=settings.ANSWER_MIN_HYBRID_SCORE,
    )


AnswerSvc = Annotated[GroundedAnswerService, Depends(_get_answer_service)]


@router.post(
    "/workspaces/{workspace_id}/answer",
    summary="Grounded Q&A (Phase 2C)",
    description=(
        "Ask a question grounded entirely in the workspace's knowledge base. "
        "Returns a cited answer, abstention message, or safe failure response. "
        "Provider is NEVER called with zero evidence. "
        "General LLM knowledge is NEVER used as a fallback. "
        "workspace_id is validated via ValidatedWorkspaceId."
    ),
)
async def answer_question(
    workspace_id: ValidatedWorkspaceId,
    body: AnswerRequest,
    membership: CurrentMembership,
    payload: CurrentPayload,
    svc: AnswerSvc,
    settings: AppSettings,
    _: Annotated[None, Depends(RequirePermission(Permission.KNOWLEDGE_READ))],
) -> GroundedAnswerAPIResponse:
    """
    Grounded answering endpoint.

    SECURITY:
    - workspace_id is ValidatedWorkspaceId (path == JWT == live membership).
    - organisation_id is sourced exclusively from the JWT payload.
    - question is normalised server-side; it is NEVER placed in system instructions.
    - Evidence is UNTRUSTED DATA — never executed, never followed as instructions.
    - Citation provenance comes entirely from server-controlled EvidenceItems.
    - Provider failures return a safe generic message; no API keys or stack
      traces are exposed.
    - Embedding vectors are never returned to clients.
    - storage_key is never included in any response.
    """
    from app.api.deps import get_session_factory

    session_factory = get_session_factory(settings)

    async with OrganisationScopedSession(
        session_factory,
        organisation_id=payload.organisation_id,
        user_id=payload.user_id,
        workspace_id=workspace_id,
    ) as session:
        result = await svc.answer(
            question=body.question,
            session=session,
            workspace_id=workspace_id,
            organisation_id=payload.organisation_id,
            top_k=body.top_k,
        )

    return GroundedAnswerAPIResponse(
        status=result.status,
        answer_text=result.answer_text,
        citations=[
            CitationResponse(
                citation_id=c.citation_id,
                label=idx + 1,
                source_id=c.source_id,
                document_id=c.document_id,
                document_version_id=c.document_version_id,
                chunk_id=c.chunk_id,
                source_name=c.source_name,
                document_title=c.document_title,
                version_number=c.version_number,
                chunk_index=c.chunk_index,
                excerpt=c.excerpt,
            )
            for idx, c in enumerate(result.citations)
        ],
        evidence_band=result.evidence_band,
        provider=result.provider,
        model=result.model,
        limitations=result.limitations,
        suspicious_count=result.suspicious_count,
    )
