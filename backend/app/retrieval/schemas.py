"""
Retrieval result schema for Phase 2B.

SECURITY:
- storage_key is NEVER included in any response schema.
- Embedding vectors are NEVER returned to clients.
- organisation_id and workspace_id are validated before retrieval; they are
  not echoed back unless needed for provenance — workspace_id omitted here.
- chunk content is UNTRUSTED DATA and must be treated as plain text only.
  It is never executed, interpolated into prompts, or used to invoke tools.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class RetrievalRequest(BaseModel):
    """
    Search request body for POST /workspaces/{workspace_id}/search.

    query:          Untrusted user input. Normalised before use. Max 2000 chars.
    limit:          Number of results to return. Default 10, max 50.
    source_ids:     Optional allowlist of knowledge_source UUIDs to restrict to.
    document_ids:   Optional allowlist of knowledge_document UUIDs to restrict to.
    model_id:       Optional embedding model to use for vector retrieval.
                    Must match stored embeddings; defaults to EMBEDDING_PROVIDER setting.
    include_archived: If True, archived documents are included (default False).
    """

    query: str = Field(..., min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    source_ids: list[uuid.UUID] = Field(default_factory=list)
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    model_id: str | None = Field(default=None)
    include_archived: bool = Field(default=False)


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


class RetrievalResult(BaseModel):
    """
    A single ranked evidence item returned by the hybrid retrieval pipeline.

    Fields intentionally omitted from the schema:
      - storage_key     (internal server secret — never exposed)
      - embedding       (large binary, no client use)
      - organisation_id (implicit from auth context)
      - workspace_id    (implicit from auth context)

    lexical_score / vector_score are floats when the source returned a result
    for that channel; None when the chunk was not in that channel's candidates.

    lexical_rank / vector_rank are 1-based positions in the respective
    candidate list; None when not applicable.

    hybrid_score is always present — it is the RRF fusion score used for
    final ordering (higher is better).

    content is UNTRUSTED DATA. Display only; never execute.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_id: uuid.UUID

    # Customer-safe metadata (display labels, not internal IDs).
    document_title: str  # original_filename from KnowledgeDocument
    source_name: str  # display_name from KnowledgeSource
    version_number: int

    chunk_index: int
    content: str  # UNTRUSTED DATA — display only

    # Retrieval channel scores/ranks (nullable = not in that channel).
    lexical_score: float | None = None
    lexical_rank: int | None = None
    vector_score: float | None = None
    vector_rank: int | None = None

    # Hybrid RRF score (always present; higher is better).
    hybrid_score: float

    # Arbitrary provenance extras for engineering/debug (no sensitive fields).
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    """Envelope for the search endpoint response."""

    results: list[RetrievalResult]
    # total: reflects returned results count (bounded by limit).
    # Pre-limit match count is not available without a full scan.
    total: int = 0
    # query_length: length of the normalised query (not the raw query).
    query_length: int

    @model_validator(mode="after")
    def _set_total(self) -> RetrievalResponse:
        # total reflects the returned results count (bounded by limit).
        self.total = len(self.results)
        return self
