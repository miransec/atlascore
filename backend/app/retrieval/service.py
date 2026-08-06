"""
KnowledgeRetrievalService — Phase 2B hybrid retrieval engine.

Pipeline:
    user query
        → query normalisation            (query.py)
        → lexical retrieval              (lexical.py — PostgreSQL FTS)
        + vector retrieval               (vector.py — cosine similarity)
        → hybrid RRF fusion              (hybrid.py)
        → optional reranking             (ranking.py — IdentityReranker by default)
        → RetrievalResult list

SECURITY:
- workspace_id must be validated by ValidatedWorkspaceId before being passed here.
- organisation_id must come from payload.organisation_id (JWT claim).
- The session passed in must be an OrganisationScopedSession with active RLS.
- Retrieved content is UNTRUSTED DATA; this service does not execute it.
- No LLM calls are made. This is a retrieval-only service.
- Source/document filters are bound parameters; cross-workspace leakage is
  blocked by both RLS and explicit WHERE predicates.
- No answer generation, no chat, no agent loop.

Observability:
- Structured log fields emitted via Python standard logging (json-compatible).
- Full query text is NOT logged by default (may contain sensitive user data).
- Retrieved chunk bodies are NOT logged.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.embeddings import EmbeddingProvider
from app.retrieval.hybrid import reciprocal_rank_fusion
from app.retrieval.lexical import lexical_search
from app.retrieval.query import QueryNormalisationError, normalise_query
from app.retrieval.ranking import IdentityReranker, Reranker
from app.retrieval.schemas import RetrievalRequest, RetrievalResponse, RetrievalResult
from app.retrieval.vector import EmbeddingModelMismatchError, vector_search

logger = logging.getLogger(__name__)


class RetrievalError(Exception):
    """Base class for retrieval errors."""


class RetrievalQueryError(RetrievalError):
    """Raised for invalid queries (empty, too long, etc.)."""


class KnowledgeRetrievalService:
    """
    Central hybrid retrieval service for Phase 2B.

    This service does NOT generate answers.  It returns ranked evidence only.
    LLM synthesis begins in Phase 2C.

    Usage:
        svc = KnowledgeRetrievalService(embedding_provider=..., reranker=...)
        response = await svc.retrieve(
            session=<OrganisationScopedSession>,
            organisation_id=payload.organisation_id,
            workspace_id=validated_workspace_id,
            request=<RetrievalRequest>,
        )
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        reranker: Reranker | None = None,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._reranker = reranker or IdentityReranker()

    async def retrieve(
        self,
        session: AsyncSession,
        organisation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        request: RetrievalRequest,
    ) -> RetrievalResponse:
        """
        Execute hybrid retrieval for the given request.

        Parameters
        ----------
        session:         OrganisationScopedSession with active workspace RLS.
        organisation_id: Trusted org UUID from JWT payload.
        workspace_id:    Validated workspace UUID from ValidatedWorkspaceId dep.
        request:         Validated RetrievalRequest from the API layer.

        Returns
        -------
        RetrievalResponse with ranked RetrievalResult list.

        Raises
        ------
        RetrievalQueryError — if the query is invalid after normalisation.
        """
        t_start = time.monotonic()

        # --- 1. Query normalisation ---
        try:
            query = normalise_query(request.query)
        except QueryNormalisationError as exc:
            raise RetrievalQueryError(str(exc)) from exc

        # --- 2. Embed the query (for vector retrieval) ---
        # Model compatibility: only chunks stored with the same model_id are
        # eligible for vector retrieval.  Mismatched models return 0 vector candidates.
        query_embedding: list[float] | None = None
        embed_model_id: str = self._embedding_provider.model_id
        embed_dimensions: int = self._embedding_provider.dimensions

        try:
            embed_result = await self._embedding_provider.embed(query)
            query_embedding = embed_result.vector
        except Exception as exc:
            # Embedding failure is non-fatal: fall back to lexical-only retrieval.
            logger.warning(
                "query_embedding_failed",
                extra={"error": str(exc), "model_id": embed_model_id},
            )
            query_embedding = None

        # Fetch limit: request up to 3x the desired limit from each channel
        # to give the fusion step enough candidates before final truncation.
        fetch_limit = min(request.limit * 3, 150)

        # --- 3. Lexical retrieval ---
        lexical_candidates = await lexical_search(
            session=session,
            query=query,
            organisation_id=organisation_id,
            workspace_id=workspace_id,
            limit=fetch_limit,
            source_ids=request.source_ids or None,
            document_ids=request.document_ids or None,
            include_archived=request.include_archived,
        )

        # --- 4. Vector retrieval ---
        vector_candidates = []
        if query_embedding is not None:
            try:
                vector_candidates = await vector_search(
                    session=session,
                    query_embedding=query_embedding,
                    model_id=embed_model_id,
                    dimensions=embed_dimensions,
                    organisation_id=organisation_id,
                    workspace_id=workspace_id,
                    limit=fetch_limit,
                    source_ids=request.source_ids or None,
                    document_ids=request.document_ids or None,
                    include_archived=request.include_archived,
                )
            except EmbeddingModelMismatchError:
                # No stored embeddings for this model — fall back to lexical only.
                vector_candidates = []

        # --- 5. Hybrid RRF fusion ---
        fused: list[RetrievalResult] = reciprocal_rank_fusion(
            lexical_candidates=lexical_candidates,
            vector_candidates=vector_candidates,
            limit=request.limit,
        )

        # --- 6. Reranking (IdentityReranker by default in Phase 2B) ---
        ranked = await self._reranker.rerank(
            query=query,
            results=fused,
            top_k=request.limit,
        )

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        # Observability log (no full query text; no chunk bodies).
        logger.info(
            "retrieval_complete",
            extra={
                "workspace_id": str(workspace_id),
                "query_length": len(query),
                "lexical_candidate_count": len(lexical_candidates),
                "vector_candidate_count": len(vector_candidates),
                "result_count": len(ranked),
                "duration_ms": elapsed_ms,
            },
        )

        return RetrievalResponse(
            results=ranked,
            total=len(ranked),
            query_length=len(query),
        )
