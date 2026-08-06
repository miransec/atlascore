"""
Local conftest for answering unit tests.

All tests are pure Python — no database, no network, no async.
Evidence packets, retrieval results, and provider responses are built inline.
"""

from __future__ import annotations

import uuid

from app.answering.evidence import EvidenceBand, EvidenceItem, EvidencePacket
from app.retrieval.schemas import RetrievalResult

# ---------------------------------------------------------------------------
# Shared UUIDs (stable across test runs)
# ---------------------------------------------------------------------------

ORG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000000")
WS_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000000")
SRC_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000000")
DOC_ID = uuid.UUID("dddddddd-0000-0000-0000-000000000000")
VER_ID = uuid.UUID("eeeeeeee-0000-0000-0000-000000000000")
CHUNK_ID = uuid.UUID("ffffffff-0000-0000-0000-000000000000")


def make_retrieval_result(
    content: str = "The capital of France is Paris.",
    hybrid_score: float = 0.03,
    lexical_rank: int | None = 1,
    vector_rank: int | None = 1,
    chunk_index: int = 0,
    document_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=uuid.uuid4(),
        source_id=source_id or SRC_ID,
        document_id=document_id or DOC_ID,
        document_version_id=VER_ID,
        chunk_index=chunk_index,
        source_name="Test Source",
        document_title="Test Document",
        version_number=1,
        content=content,
        lexical_score=0.5,
        vector_score=0.9,
        hybrid_score=hybrid_score,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
        rerank_score=None,
        metadata={},
    )


def make_evidence_item(
    evidence_id: str = "E1",
    content: str = "The capital of France is Paris.",
    hybrid_score: float = 0.03,
    lexical_rank: int | None = 1,
    vector_rank: int | None = 1,
    injection_flags: list[str] | None = None,
    chunk_index: int = 0,
    document_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=uuid.uuid4(),
        source_id=source_id or SRC_ID,
        document_id=document_id or DOC_ID,
        document_version_id=VER_ID,
        chunk_index=chunk_index,
        source_name="Test Source",
        document_title="Test Document",
        version_number=1,
        content=content,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
        hybrid_score=hybrid_score,
        injection_flags=injection_flags or [],
        provenance={},
    )


def make_packet(
    items: list[EvidenceItem] | None = None,
    band: EvidenceBand = EvidenceBand.HIGH,
    score: float = 0.85,
    query: str = "What is the capital of France?",
) -> EvidencePacket:
    if items is None:
        items = [make_evidence_item()]
    return EvidencePacket(
        query=query,
        items=items,
        distinct_sources=len({i.source_id for i in items}),
        distinct_documents=len({i.document_id for i in items}),
        evidence_band=band,
        evidence_score_internal=score,
        limitations=[],
        suspicious_count=sum(1 for i in items if i.injection_flags),
    )
