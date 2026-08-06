"""
EvidencePacket and EvidenceItem — Phase 2C grounded answering.

Evidence items are derived from Phase 2B retrieval results.
Evidence IDs (E1, E2, …) are server-generated for each response,
mapping deterministically to retrieved chunks.

SECURITY — HARD BOUNDARY:
  - Retrieved chunks are UNTRUSTED DATA.
  - Evidence IDs are assigned by AtlasCore, not by the provider.
  - The content field is quoted evidence text; it is NEVER executed.
  - injection_flags records suspicious patterns detected in content.
  - Nothing in this module executes, interprets, or forwards chunk text
    as instructions.

Evidence confidence is DETERMINISTIC — derived from retrieval signals only.
It is NOT model confidence or LLM self-assessment.

Evidence bands:
  HIGH    — top score ≥ 0.8, ≥ 3 distinct documents, lexical+vector agree
  MEDIUM  — top score ≥ 0.5 or ≥ 2 distinct documents
  LOW     — anything below the MEDIUM threshold but above zero
  NONE    — no results, or all results below minimum threshold

Formula (internal, subject to calibration):
  evidence_score_internal = (
      0.40 * normalised_top_hybrid_score        # best single result quality
    + 0.25 * min(distinct_doc_count / 3, 1.0)  # source diversity (caps at 3)
    + 0.20 * channel_agreement_bonus            # both lexical+vector agree
    + 0.15 * min(useful_result_count / 5, 1.0) # breadth (caps at 5)
  )
  Reduced by 0.20 x fraction_suspicious        # injection penalty
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.retrieval.schemas import RetrievalResult


class EvidenceBand(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


@dataclass
class EvidenceItem:
    """
    A single evidence item derived from a Phase 2B retrieval result.

    evidence_id:   Server-assigned ID for this response (E1, E2, …).
                   The provider may reference these IDs in citations.
                   The ID only has meaning within the current response.
    content:       UNTRUSTED DATA.  Never executed.  Used as quoted evidence.
    injection_flags: List of suspicious pattern names detected in content.
                     Does not mean content is malicious — flagging reduces trust.
    """

    evidence_id: str  # "E1", "E2", …
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_index: int
    source_name: str
    document_title: str
    version_number: int
    content: str  # UNTRUSTED DATA
    lexical_rank: int | None
    vector_rank: int | None
    hybrid_score: float
    injection_flags: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidencePacket:
    """
    Structured evidence package for a single grounded answer request.

    Contains all retrieval evidence, de-duplication metadata, and the
    deterministic evidence confidence band.

    evidence_score_internal is retained for observability and debugging.
    Public API only exposes evidence_band.
    """

    query: str
    items: list[EvidenceItem]
    distinct_sources: int
    distinct_documents: int
    evidence_band: EvidenceBand
    evidence_score_internal: float  # ∈ [0, 1]; deterministic — not model confidence
    limitations: list[str] = field(default_factory=list)
    suspicious_count: int = 0


def build_evidence_packet(
    query: str,
    results: list[RetrievalResult],
    max_items: int = 10,
    min_hybrid_score: float = 0.0,
) -> EvidencePacket:
    """
    Convert Phase 2B retrieval results into an EvidencePacket.

    Parameters
    ----------
    query:           The normalised user question.
    results:         Ranked RetrievalResult list from KnowledgeRetrievalService.
    max_items:       Maximum evidence items to include.
    min_hybrid_score: Minimum hybrid_score to include (default: 0.0 = all).

    Returns
    -------
    EvidencePacket with server-assigned evidence IDs and evidence band.
    """
    # Filter below threshold.
    eligible = [r for r in results if r.hybrid_score >= min_hybrid_score]
    eligible = eligible[:max_items]

    items: list[EvidenceItem] = []
    for idx, result in enumerate(eligible, start=1):
        flags = _detect_injection_flags(result.content)
        items.append(
            EvidenceItem(
                evidence_id=f"E{idx}",
                chunk_id=result.chunk_id,
                source_id=result.source_id,
                document_id=result.document_id,
                document_version_id=result.document_version_id,
                chunk_index=result.chunk_index,
                source_name=result.source_name,
                document_title=result.document_title,
                version_number=result.version_number,
                content=result.content,
                lexical_rank=result.lexical_rank,
                vector_rank=result.vector_rank,
                hybrid_score=result.hybrid_score,
                injection_flags=flags,
                provenance={
                    "lexical_score": result.lexical_score,
                    "vector_score": result.vector_score,
                    "metadata": result.metadata,
                },
            )
        )

    distinct_docs = len({item.document_id for item in items})
    distinct_sources = len({item.source_id for item in items})
    suspicious_count = sum(1 for item in items if item.injection_flags)

    band, score = _calculate_evidence_band(
        items=items,
        distinct_docs=distinct_docs,
        distinct_sources=distinct_sources,
        suspicious_count=suspicious_count,
    )

    limitations: list[str] = []
    if suspicious_count > 0:
        limitations.append(
            f"{suspicious_count} evidence item(s) contain suspicious patterns "
            "and were flagged for review."
        )
    if len(results) > max_items:
        limitations.append(
            f"Evidence capped at {max_items} items; "
            f"{len(results) - max_items} additional result(s) not included."
        )

    return EvidencePacket(
        query=query,
        items=items,
        distinct_sources=distinct_sources,
        distinct_documents=distinct_docs,
        evidence_band=band,
        evidence_score_internal=score,
        limitations=limitations,
        suspicious_count=suspicious_count,
    )


# ---------------------------------------------------------------------------
# Injection heuristics
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("ignore_previous_instructions", "ignore previous instructions"),
    ("ignore_system_prompt", "ignore system prompt"),
    ("reveal_system_prompt", "reveal system prompt"),
    ("reveal_system_prompt", "reveal the system prompt"),
    ("send_data_to", "send data to"),
    ("send_secrets_to", "send secrets to"),
    ("execute_command", "execute command"),
    ("call_tool", "call tool"),
    ("call_external", "call external"),
    ("you_are_now", "you are now"),
    ("developer_message", "developer message"),
    ("system_message", "system message"),
    ("override_instructions", "override instructions"),
    ("disregard_instructions", "disregard all"),
    ("make_http_request", "http request"),
    ("change_workspace", "change workspace"),
    ("change_tenant", "change tenant"),
    ("alter_permissions", "alter permissions"),
]


def _detect_injection_flags(content: str) -> list[str]:
    """
    Lightweight deterministic heuristic for suspicious content patterns.

    Does NOT claim to detect all prompt injection.
    Returns a list of matched pattern names (strings), empty if none found.
    The content is evidence text — detection flags it, does NOT execute it.
    """
    content_lower = content.lower()
    found: list[str] = []
    seen: set[str] = set()
    for name, pattern in _INJECTION_PATTERNS:
        if name not in seen and pattern in content_lower:
            found.append(name)
            seen.add(name)
    return found


# ---------------------------------------------------------------------------
# Evidence confidence calculation (deterministic)
# ---------------------------------------------------------------------------


def _calculate_evidence_band(
    items: list[EvidenceItem],
    distinct_docs: int,
    distinct_sources: int,
    suspicious_count: int,
) -> tuple[EvidenceBand, float]:
    """
    Deterministic evidence confidence calculation.

    Returns (EvidenceBand, internal_score ∈ [0, 1]).

    Formula (documented in module docstring):
      score = (
          0.40 * normalised_top_hybrid_score
        + 0.25 * min(distinct_doc_count / 3, 1.0)
        + 0.20 * channel_agreement_bonus
        + 0.15 * min(useful_result_count / 5, 1.0)
      )
      Reduced by 0.20 x fraction_suspicious

    Bands:
      NONE   → score == 0 or no items
      LOW    → score < 0.45
      MEDIUM → score < 0.70
      HIGH   → score ≥ 0.70
    """
    if not items:
        return EvidenceBand.NONE, 0.0

    # Component 1: top hybrid score (normalised; hybrid_score already in [0,∞) via RRF).
    # RRF scores are typically small fractions; we normalise by capping at 0.1 → 1.0.
    top_score = items[0].hybrid_score
    # RRF k=60 gives max score ~1/61 ≈ 0.016 per channel; two channels → ~0.033.
    # Normalise to [0, 1] by dividing by 0.033 (empirical upper bound).
    normalised_top = min(top_score / 0.033, 1.0)

    # Component 2: distinct document diversity (caps at 3).
    diversity = min(distinct_docs / 3, 1.0)

    # Component 3: channel agreement (lexical AND vector both contributed).
    items_with_both = sum(
        1 for item in items if item.lexical_rank is not None and item.vector_rank is not None
    )
    agreement_bonus = min(items_with_both / max(len(items), 1), 1.0)

    # Component 4: breadth (number of useful results, capped at 5).
    breadth = min(len(items) / 5, 1.0)

    score = 0.40 * normalised_top + 0.25 * diversity + 0.20 * agreement_bonus + 0.15 * breadth

    # Injection penalty: reduce by 20% of the suspicious fraction.
    if items:
        suspicious_fraction = suspicious_count / len(items)
        score = score * (1.0 - 0.20 * suspicious_fraction)

    score = max(0.0, min(1.0, score))

    if score == 0.0:
        band = EvidenceBand.NONE
    elif score < 0.45:
        band = EvidenceBand.LOW
    elif score < 0.70:
        band = EvidenceBand.MEDIUM
    else:
        band = EvidenceBand.HIGH

    return band, score
