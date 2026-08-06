"""
Pydantic schemas for Phase 2C grounded answering API.

SECURITY:
  - storage_key is NEVER included in any response schema.
  - Embedding vectors are NEVER returned to clients.
  - Evidence scores are NEVER exposed directly (only evidence_band is public).
  - Citation metadata comes entirely from server-side provenance.
  - Provider-supplied source names, document titles, or URLs are NEVER used.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AnswerRequest(BaseModel):
    """
    Request body for POST /workspaces/{workspace_id}/answer.

    question: Raw user question (normalised server-side).
    top_k:    Number of retrieval candidates (default 10, max 50).
    """

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User question to be answered from workspace knowledge.",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of retrieval candidates to consider (default 10).",
    )


class CitationResponse(BaseModel):
    """
    A validated citation derived from server-controlled evidence provenance.

    All metadata comes from server-side EvidenceItem.
    Provider-supplied source names or document titles are NEVER included.
    """

    citation_id: str = Field(description="Server-assigned evidence ID (E1, E2, …).")
    label: int = Field(description="Numeric label as used in answer_text ([1], [2], …).")
    source_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_id: uuid.UUID
    source_name: str = Field(description="Source name from server-controlled provenance.")
    document_title: str = Field(description="Document title from server-controlled provenance.")
    version_number: int
    chunk_index: int
    excerpt: str | None = Field(
        default=None,
        description="Short bounded excerpt for display (server-truncated).",
    )


class GroundedAnswerResponse(BaseModel):
    """
    Response body for POST /workspaces/{workspace_id}/answer.

    status:        Outcome of the grounded answering pipeline.
    answer_text:   Grounded answer with numeric citation labels, or a safe
                   abstention message. Citations appear as [1], [2], etc.
    citations:     Validated citations with server-controlled provenance.
    evidence_band: Deterministic confidence band (high/medium/low/none).
                   Derived from retrieval signals — NOT model/LLM confidence.
    provider:      Provider identifier (observability; empty on abstention).
    model:         Model identifier (observability; empty on abstention).
    limitations:   Non-fatal warnings about the evidence set.
    suspicious_count: Number of evidence items that carried injection flags.
    """

    status: str = Field(
        description=(
            "Pipeline outcome: 'answer', 'abstain_no_evidence', "
            "'abstain_weak_evidence', or 'provider_failure'."
        )
    )
    answer_text: str = Field(description="Grounded answer or abstention message.")
    citations: list[CitationResponse] = Field(
        default_factory=list,
        description="Validated citations (empty when status != 'answer').",
    )
    evidence_band: str = Field(
        description=(
            "Deterministic evidence confidence: 'high', 'medium', 'low', or 'none'. "
            "Derived from retrieval signals only — not model self-assessment."
        )
    )
    provider: str = Field(
        default="",
        description="Provider identifier (observability).",
    )
    model: str = Field(
        default="",
        description="Model identifier (observability).",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings about the evidence set.",
    )
    suspicious_count: int = Field(
        default=0,
        description="Number of evidence items that carried prompt-injection flags.",
    )
