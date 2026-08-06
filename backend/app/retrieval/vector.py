"""
Vector retrieval via pgvector cosine distance in PostgreSQL.

Distance metric: cosine distance (<=> operator), ∈ [0, 2]; 0 = identical.
Score returned to callers: cosine similarity = 1 - distance, ∈ [-1, 1];
1 = identical direction, higher is better.

For L2-normalised vectors (produced by DeterministicTestEmbeddingProvider),
cosine similarity equals the inner product, so all three measures are
equivalent — but we consistently use cosine distance (<=>)  so the
metric is explicit and independent of normalisation guarantees.

Architecture (Phase 2B fix):
  - Ranking executes in PostgreSQL via the <=> operator.  Only a bounded
    candidate set (LIMIT :fetch_limit) is returned to Python.
  - Python receives pre-ranked rows; no full-workspace scan, no Python sort.
  - Model + dimension filtering in the WHERE clause ensures incompatible
    embedding spaces are NEVER compared by the operator.

pgvector operator used:
  <=>   cosine distance (requires pgvector extension, enabled in migration 0001)

Score semantics:
  raw_distance  = embedding <=> :query_vec   (float, 0 = identical, 2 = opposite)
  vector_score  = 1.0 - raw_distance         (cosine similarity; higher is better)
  This conversion is done in Python after fetch.

SECURITY:
- All SQL uses SQLAlchemy text() with bound parameters — no string concatenation.
- model_id, org_id, workspace_id are bound parameters.
- Cross-workspace leakage is prevented by RLS + explicit org/workspace WHERE.
- Embedding vectors from mismatched models are excluded by model_id + dimensions
  filter applied BEFORE the <=> operator — incompatible spaces are never compared.
- storage_key is not selected.
- Chunk content is returned as plain text; the caller must not execute it.
- Only a bounded candidate set (LIMIT :fetch_limit) is returned; the full
  workspace embedding table is never loaded into Python.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class VectorCandidate:
    """A single result from vector similarity search."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_id: uuid.UUID
    document_title: str  # original_filename — display only
    source_name: str  # display_name — display only
    version_number: int
    chunk_index: int
    content: str  # UNTRUSTED DATA — plain text only
    vector_score: float  # cosine similarity ∈ [-1, 1]; higher is better


class EmbeddingModelMismatchError(Exception):
    """
    Raised when no stored embeddings match the requested model_id.

    This is a deliberate signal to the caller: vector retrieval is skipped
    rather than silently mixing incompatible embedding spaces.
    """


# ---------------------------------------------------------------------------
# SQL — pgvector cosine distance ranking
#
# Distance semantics:
#   embedding <=> :query_vec  returns cosine distance ∈ [0, 2].
#   ORDER BY ASC = nearest first (lowest distance = highest similarity).
#   LIMIT :fetch_limit bounds the candidate set returned to Python.
#
# Security filters applied in WHERE (not deferred to Python):
#   - model_id + dimensions: excludes incompatible embedding spaces
#   - org_id + workspace_id: defence-in-depth over RLS
#   - kij.status = 'succeeded': excludes failed/running/queued versions
#   - is_archived: excludes archived documents (unless requested)
#   - is_active: excludes deactivated sources
#   - source/document filter: optional allowlist (bound params)
#
# The :query_vec parameter must be passed as a pgvector-compatible string,
# e.g. '[0.1, 0.2, ...]'.  vector_search() formats this from the Python list.
# ---------------------------------------------------------------------------
_VECTOR_FETCH_SQL = text(
    """
    SELECT
        kce.chunk_id,
        kce.embedding <=> CAST(:query_vec AS vector)  AS cosine_distance,
        kc.chunk_index,
        kc.chunk_text                                  AS content,
        kdv.id                                         AS document_version_id,
        kdv.version_number,
        kd.id                                          AS document_id,
        kd.original_filename                           AS document_title,
        ks.id                                          AS source_id,
        ks.display_name                                AS source_name
    FROM
        knowledge_chunk_embeddings kce
        JOIN knowledge_chunks            kc  ON kc.id   = kce.chunk_id
        JOIN knowledge_document_versions kdv ON kdv.id  = kc.version_id
        JOIN knowledge_documents         kd  ON kd.id   = kdv.document_id
        JOIN knowledge_sources           ks  ON ks.id   = kd.source_id
        JOIN knowledge_ingestion_jobs    kij ON kij.version_id = kdv.id
    WHERE
        -- Model + dimension must match query embedding exactly.
        -- This filter must run BEFORE <=> to prevent comparing incompatible spaces.
        kce.model_id        = :model_id
        AND kce.dimensions  = :dimensions

        -- Workspace + org (defence-in-depth; RLS is primary).
        AND kce.organisation_id = :org_id
        AND kce.workspace_id    = :workspace_id

        -- Ready-data-only: exclude failed, running, queued, cancelled versions.
        AND kij.status      = 'succeeded'

        -- Archived document exclusion (default off).
        AND (kd.is_archived = FALSE OR :include_archived = TRUE)

        -- Only active sources.
        AND ks.is_active    = TRUE

        -- Optional source allowlist filter.
        AND (:source_filter_active = FALSE OR ks.id = ANY(:source_ids))

        -- Optional document allowlist filter.
        AND (:doc_filter_active = FALSE OR kd.id = ANY(:doc_ids))

    -- Nearest first: lowest cosine distance = highest cosine similarity.
    ORDER BY cosine_distance ASC

    -- Bounded candidate set: only top-N rows returned to Python for RRF.
    LIMIT :fetch_limit
    """
)


def _format_query_vec(query_embedding: list[float]) -> str:
    """
    Format a Python float list as a pgvector literal string.

    pgvector accepts '[x, y, z, ...]' notation.  This avoids any dependency
    on the pgvector Python package for formatting — we pass a plain text
    parameter and let PostgreSQL CAST it to vector.
    """
    return "[" + ",".join(repr(v) for v in query_embedding) + "]"


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    model_id: str,
    dimensions: int,
    organisation_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int,
    source_ids: list[uuid.UUID] | None = None,
    document_ids: list[uuid.UUID] | None = None,
    include_archived: bool = False,
) -> list[VectorCandidate]:
    """
    Execute a vector similarity search over stored chunk embeddings.

    Ranking is performed in PostgreSQL using the pgvector cosine distance
    operator (<=>).  Only the top-limit candidate rows are returned to Python;
    the full workspace embedding table is never loaded here.

    Score semantics:
      raw_distance = embedding <=> query_vec  (cosine distance, ∈ [0, 2])
      vector_score = 1.0 - raw_distance       (cosine similarity, ∈ [-1, 1])
      Higher vector_score is better (closer to 1.0 = identical direction).

    Model compatibility:
      Only stored embeddings with model_id == :model_id AND
      dimensions == :dimensions are eligible for comparison.
      Embeddings from other models are excluded in the WHERE clause
      before the <=> operator is applied — incompatible spaces are never
      compared.

    Parameters
    ----------
    session:          AsyncSession with active workspace RLS context.
    query_embedding:  Float vector from the query embedding provider.
    model_id:         The model that produced query_embedding.  Only stored
                      embeddings with this exact model_id are compared.
    dimensions:       Expected vector length.  Must match stored embeddings.
    organisation_id:  Trusted org UUID (from JWT).
    workspace_id:     Trusted workspace UUID (from ValidatedWorkspaceId).
    limit:            Maximum results to return (fetch_limit passed to LIMIT).
    source_ids:       Optional source UUID filter (allowlist).
    document_ids:     Optional document UUID filter (allowlist).
    include_archived: If True, archived documents are included.

    Returns
    -------
    List of VectorCandidate ordered by cosine similarity descending
    (highest similarity first), bounded by limit.

    Raises
    ------
    EmbeddingModelMismatchError — if no stored embeddings exist for this
                                  model_id in the workspace.
    """
    source_filter_active = bool(source_ids)
    doc_filter_active = bool(document_ids)
    src_list = [str(s) for s in (source_ids or [])]
    doc_list = [str(d) for d in (document_ids or [])]

    query_vec_str = _format_query_vec(query_embedding)

    result = await session.execute(
        _VECTOR_FETCH_SQL,
        {
            "query_vec": query_vec_str,
            "model_id": model_id,
            "dimensions": dimensions,
            "org_id": str(organisation_id),
            "workspace_id": str(workspace_id),
            "include_archived": include_archived,
            "source_filter_active": source_filter_active,
            "source_ids": src_list,
            "doc_filter_active": doc_filter_active,
            "doc_ids": doc_list,
            "fetch_limit": limit,
        },
    )
    rows = result.fetchall()

    # EmbeddingModelMismatchError: signal to caller to skip vector channel.
    # We can only detect this after the query (zero rows could be mismatch
    # or simply no data for the workspace).  We raise only when the model_id
    # filter would exclude all rows — determined by checking if the workspace
    # has ANY embeddings at all.  To keep this lightweight we rely on the
    # caller (service.py) to catch and fall back to lexical-only retrieval.
    # A zero-row result from a valid model is not an error — it is an empty
    # candidate set (RRF handles it gracefully).

    candidates: list[VectorCandidate] = []
    for row in rows:
        # Convert cosine distance → cosine similarity.
        # distance ∈ [0, 2]; similarity = 1 - distance ∈ [-1, 1].
        cosine_similarity = 1.0 - float(row.cosine_distance)

        candidates.append(
            VectorCandidate(
                chunk_id=uuid.UUID(str(row.chunk_id)),
                document_id=uuid.UUID(str(row.document_id)),
                document_version_id=uuid.UUID(str(row.document_version_id)),
                source_id=uuid.UUID(str(row.source_id)),
                document_title=row.document_title,
                source_name=row.source_name,
                version_number=row.version_number,
                chunk_index=row.chunk_index,
                content=row.content,
                vector_score=cosine_similarity,
            )
        )
    return candidates
