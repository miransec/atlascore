"""
Hybrid retrieval fusion via Reciprocal Rank Fusion (RRF).

RRF formula (per document d across ranked lists):
    RRF(d) = Σ 1 / (k + rank_i(d))

where rank_i(d) is the 1-based rank of d in candidate list i, and
k is a smoothing constant (default 60, following Cormack et al. 2009).

Properties:
- k = 60 is well-established in the IR literature.  Lower k amplifies top-rank
  advantage; higher k flattens scores.  60 is a reasonable default.
- Documents appearing in both lists receive contributions from both terms and
  therefore score higher than single-list candidates — the desired behaviour.
- Documents appearing in only one list are not discarded; they receive their
  single-list RRF contribution.
- Deduplication: if the same chunk_id appears in both candidate lists, it is
  fused into ONE result (not returned twice).
- Ties are broken deterministically by (chunk_id asc) — consistent across runs.
- Higher hybrid_score is better.

SECURITY:
- This module performs pure Python computation.
- No SQL, no I/O, no external calls.
- chunk content from candidates is passed through unchanged (UNTRUSTED DATA).
  Callers must not execute it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.retrieval.lexical import LexicalCandidate
from app.retrieval.schemas import RetrievalResult
from app.retrieval.vector import VectorCandidate

# Default smoothing constant from Cormack et al. 2009.
RRF_K = 60


@dataclass
class _FusionSlot:
    """Accumulator for a single chunk during RRF fusion."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_id: uuid.UUID
    document_title: str
    source_name: str
    version_number: int
    chunk_index: int
    content: str
    lexical_score: float | None = None
    lexical_rank: int | None = None
    vector_score: float | None = None
    vector_rank: int | None = None
    hybrid_score: float = 0.0


def reciprocal_rank_fusion(
    lexical_candidates: list[LexicalCandidate],
    vector_candidates: list[VectorCandidate],
    k: int = RRF_K,
    limit: int = 50,
) -> list[RetrievalResult]:
    """
    Fuse lexical and vector candidate lists using RRF.

    Parameters
    ----------
    lexical_candidates: Ordered list from lexical search (best first).
    vector_candidates:  Ordered list from vector search (best first).
    k:                  RRF smoothing constant (default 60).
    limit:              Maximum results to return.

    Returns
    -------
    List of RetrievalResult sorted by hybrid_score descending.
    Ties broken by chunk_id ascending (deterministic).
    Duplicate chunk IDs are fused into one result.
    """
    slots: dict[uuid.UUID, _FusionSlot] = {}

    # Lexical contributions.
    for rank_0, cand in enumerate(lexical_candidates):
        rank_1 = rank_0 + 1  # 1-based
        contribution = 1.0 / (k + rank_1)

        if cand.chunk_id not in slots:
            slots[cand.chunk_id] = _FusionSlot(
                chunk_id=cand.chunk_id,
                document_id=cand.document_id,
                document_version_id=cand.document_version_id,
                source_id=cand.source_id,
                document_title=cand.document_title,
                source_name=cand.source_name,
                version_number=cand.version_number,
                chunk_index=cand.chunk_index,
                content=cand.content,
            )
        slot = slots[cand.chunk_id]
        slot.lexical_score = cand.lexical_score
        slot.lexical_rank = rank_1
        slot.hybrid_score += contribution

    # Vector contributions.
    for rank_0, vcand in enumerate(vector_candidates):
        rank_1 = rank_0 + 1  # 1-based
        contribution = 1.0 / (k + rank_1)

        if vcand.chunk_id not in slots:
            slots[vcand.chunk_id] = _FusionSlot(
                chunk_id=vcand.chunk_id,
                document_id=vcand.document_id,
                document_version_id=vcand.document_version_id,
                source_id=vcand.source_id,
                document_title=vcand.document_title,
                source_name=vcand.source_name,
                version_number=vcand.version_number,
                chunk_index=vcand.chunk_index,
                content=vcand.content,
            )
        slot = slots[vcand.chunk_id]
        slot.vector_score = vcand.vector_score
        slot.vector_rank = rank_1
        slot.hybrid_score += contribution

    # Sort: hybrid_score descending; chunk_id ascending as tie-breaker.
    ordered = sorted(
        slots.values(),
        key=lambda s: (-s.hybrid_score, str(s.chunk_id)),
    )

    results: list[RetrievalResult] = []
    for slot in ordered[:limit]:
        results.append(
            RetrievalResult(
                chunk_id=slot.chunk_id,
                document_id=slot.document_id,
                document_version_id=slot.document_version_id,
                source_id=slot.source_id,
                document_title=slot.document_title,
                source_name=slot.source_name,
                version_number=slot.version_number,
                chunk_index=slot.chunk_index,
                content=slot.content,
                lexical_score=slot.lexical_score,
                lexical_rank=slot.lexical_rank,
                vector_score=slot.vector_score,
                vector_rank=slot.vector_rank,
                hybrid_score=slot.hybrid_score,
                metadata={},
            )
        )
    return results
