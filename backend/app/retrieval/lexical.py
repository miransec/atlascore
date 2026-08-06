"""
Lexical retrieval via PostgreSQL full-text search.

Uses PostgreSQL tsvector/tsquery (built-in, no extension required).
Searches knowledge_chunks.chunk_text using the 'english' text search
configuration.  A GIN index on to_tsvector('english', chunk_text) is
created by migration 0005.

SECURITY:
- All SQL uses SQLAlchemy text() with bound parameters — no string concatenation.
- source_id / document_id filters are UUID parameters; they cannot inject SQL.
- RLS is active on the session: the query only sees rows the current workspace
  context allows.  The WHERE predicates add defence-in-depth but RLS is primary.
- chunk content is returned as plain text; the caller must not execute it.
- storage_key is not selected.

SQL injection test:
  query "' OR 1=1 --" is passed as a plainto_tsquery() argument which
  is treated as a text search phrase.  plainto_tsquery normalises and
  escapes it; it cannot modify the SQL query structure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class LexicalCandidate:
    """A single result from lexical full-text search."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_id: uuid.UUID
    document_title: str  # original_filename — display only
    source_name: str  # display_name — display only
    version_number: int
    chunk_index: int
    content: str  # UNTRUSTED DATA — plain text only
    lexical_score: float  # ts_rank_cd value (higher is better)


_LEXICAL_SQL = text(
    """
    SELECT
        kc.id                   AS chunk_id,
        kd.id                   AS document_id,
        kdv.id                  AS document_version_id,
        ks.id                   AS source_id,
        kd.original_filename    AS document_title,
        ks.display_name         AS source_name,
        kdv.version_number      AS version_number,
        kc.chunk_index          AS chunk_index,
        kc.chunk_text           AS content,
        ts_rank_cd(
            to_tsvector('english', kc.chunk_text),
            plainto_tsquery('english', :query)
        )                       AS lexical_score
    FROM
        knowledge_chunks kc
        JOIN knowledge_document_versions kdv ON kdv.id = kc.version_id
        JOIN knowledge_documents         kd  ON kd.id  = kdv.document_id
        JOIN knowledge_sources           ks  ON ks.id  = kd.source_id
        JOIN knowledge_ingestion_jobs    kij ON kij.version_id = kdv.id
    WHERE
        -- Full-text match (plainto_tsquery prevents SQL injection; bound param).
        to_tsvector('english', kc.chunk_text) @@ plainto_tsquery('english', :query)

        -- Workspace + org enforced by RLS; repeated here as defence-in-depth.
        AND kc.organisation_id  = :org_id
        AND kc.workspace_id     = :workspace_id

        -- Ready-data-only: only successfully ingested versions.
        AND kij.status          = 'succeeded'

        -- Archived document exclusion (default: exclude).
        AND (kd.is_archived = FALSE OR :include_archived = TRUE)

        -- Active source only.
        AND ks.is_active        = TRUE

        -- Optional source filter (no filter applied when list is empty).
        AND (:source_filter_active = FALSE OR ks.id = ANY(:source_ids))

        -- Optional document filter.
        AND (:doc_filter_active = FALSE OR kd.id = ANY(:doc_ids))

    ORDER BY
        lexical_score DESC
    LIMIT :limit
    """
)


async def lexical_search(
    session: AsyncSession,
    query: str,
    organisation_id: uuid.UUID,
    workspace_id: uuid.UUID,
    limit: int,
    source_ids: list[uuid.UUID] | None = None,
    document_ids: list[uuid.UUID] | None = None,
    include_archived: bool = False,
) -> list[LexicalCandidate]:
    """
    Execute a PostgreSQL full-text search for chunk content.

    Parameters
    ----------
    session:        An AsyncSession scoped to the current org/workspace context.
                    RLS is the primary security boundary; the WHERE clause adds
                    defence-in-depth.
    query:          Normalised query string.  Passed as a bound parameter to
                    plainto_tsquery — SQL injection is not possible.
    organisation_id: Trusted org UUID (from JWT).
    workspace_id:   Trusted workspace UUID (from ValidatedWorkspaceId).
    limit:          Maximum candidates to return (bounded by API layer).
    source_ids:     Optional list of source UUIDs to restrict results to.
                    These UUIDs are bound parameters; they cannot inject SQL.
                    Cross-workspace leakage is prevented by RLS + org/ws WHERE.
    document_ids:   Optional list of document UUIDs to restrict to.
    include_archived: If True, archived documents are included.

    Returns
    -------
    List of LexicalCandidate, ordered by ts_rank_cd descending.
    """
    source_filter_active = bool(source_ids)
    doc_filter_active = bool(document_ids)

    # PostgreSQL ANY() requires a list; provide empty list as safe default.
    src_list = [str(s) for s in (source_ids or [])]
    doc_list = [str(d) for d in (document_ids or [])]

    result = await session.execute(
        _LEXICAL_SQL,
        {
            "query": query,
            "org_id": str(organisation_id),
            "workspace_id": str(workspace_id),
            "include_archived": include_archived,
            "source_filter_active": source_filter_active,
            "source_ids": src_list,
            "doc_filter_active": doc_filter_active,
            "doc_ids": doc_list,
            "limit": limit,
        },
    )
    rows = result.fetchall()

    candidates: list[LexicalCandidate] = []
    for row in rows:
        candidates.append(
            LexicalCandidate(
                chunk_id=uuid.UUID(str(row.chunk_id)),
                document_id=uuid.UUID(str(row.document_id)),
                document_version_id=uuid.UUID(str(row.document_version_id)),
                source_id=uuid.UUID(str(row.source_id)),
                document_title=row.document_title,
                source_name=row.source_name,
                version_number=row.version_number,
                chunk_index=row.chunk_index,
                content=row.content,
                lexical_score=float(row.lexical_score),
            )
        )
    return candidates
