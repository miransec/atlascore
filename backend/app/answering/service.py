"""
GroundedAnswerService — Phase 2C orchestration.

Central service that runs the full grounded answering pipeline:
    normalise question
    → Phase 2B retrieve (session-scoped, RLS-enforced)
    → build EvidencePacket (server-assigned E1/E2/... IDs)
    → assess sufficiency (deterministic — no LLM)
    → if insufficient → abstain WITHOUT calling AnswerProvider
    → build safe prompt (trusted system instructions + untrusted evidence blocks)
    → AnswerProvider.generate()
    → validate citation IDs (server-controlled provenance, fabricated IDs rejected)
    → construct Citations from trusted EvidenceItems
    → rewrite [En] markers → [n] in answer text
    → return GroundedAnswerResponse

SECURITY GUARANTEES:
  - The AnswerProvider is NEVER called with zero evidence.
  - Provider exceptions are caught; no API keys, stack traces, or system
    prompt are exposed to callers.
  - All citation metadata comes from server-controlled EvidenceItems, never
    from provider output.
  - The question is normalised before embedding and prompting — it is placed
    only in the QUESTION section, never in trusted system instructions.
  - Session is always the OrganisationScopedSession passed in — all retrieval
    runs under the same RLS context as the request.

Design:
  - GroundedAnswerService accepts injected dependencies for retrieval,
    embedding, provider, and policy components.
  - This makes it testable without network calls or API credentials.
  - The HTTP layer instantiates components from config and calls
    GroundedAnswerService.answer().
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.answering.citation import (
    Citation,
    CitationValidationError,
    CitationValidator,
    rewrite_citations_in_answer,
)
from app.answering.evidence import EvidencePacket, build_evidence_packet
from app.answering.prompt import PromptBuilder
from app.answering.provider import AnswerProvider, AnswerProviderError
from app.answering.sufficiency import EvidenceSufficiencyPolicy, SufficiencyOutcome
from app.core.observability import (
    log_answer_abstained,
    log_answer_completed,
    log_answer_provider_failure,
    log_answer_started,
    make_query_fingerprint,
)
from app.retrieval.schemas import RetrievalRequest
from app.retrieval.service import KnowledgeRetrievalService, RetrievalQueryError

logger = logging.getLogger(__name__)

_MAX_QUESTION_CHARS = 2000  # hard cap to prevent prompt padding attacks


@dataclass
class GroundedAnswerResponse:
    """
    Final output of the GroundedAnswerService pipeline.

    status:           Outcome category — see AnswerStatus.
    answer_text:      Grounded answer with numeric citation labels, or
                      a safe abstention/failure message.
    citations:        Validated, server-provenance citations (empty if abstained).
    evidence_band:    Deterministic confidence band from retrieval signals.
    evidence_score:   Internal score ∈ [0, 1] (for observability/debugging only).
    provider:         Provider identifier (observability).
    model:            Model identifier (observability).
    limitations:      Non-fatal warnings (e.g., evidence capped, suspicious items).
    suspicious_count: How many evidence items carried injection flags.
    """

    status: str  # AnswerStatus value
    answer_text: str
    citations: list[Citation] = field(default_factory=list)
    evidence_band: str = "none"
    evidence_score: float = 0.0
    provider: str = ""
    model: str = ""
    limitations: list[str] = field(default_factory=list)
    suspicious_count: int = 0


class AnswerStatus:
    """String constants for GroundedAnswerResponse.status."""

    ANSWER = "answer"
    ABSTAIN_NO_EVIDENCE = "abstain_no_evidence"
    ABSTAIN_WEAK_EVIDENCE = "abstain_weak_evidence"
    PROVIDER_FAILURE = "provider_failure"


def _normalise_question(raw: str) -> str:
    """
    Normalise a user-supplied question before embedding and prompting.

    - Strip leading/trailing whitespace.
    - Collapse internal whitespace runs.
    - Enforce hard character cap (prevent prompt padding).

    The result is still untrusted user input — it is placed in the
    prompt as the QUESTION, never in the trusted system instruction block.
    """
    normalised = re.sub(r"\s+", " ", raw.strip())
    if len(normalised) > _MAX_QUESTION_CHARS:
        normalised = normalised[:_MAX_QUESTION_CHARS]
    return normalised


class GroundedAnswerService:
    """
    Orchestrates the Phase 2C grounded answering pipeline.

    Parameters
    ----------
    retrieval_service:  Phase 2B KnowledgeRetrievalService.
    answer_provider:    AnswerProvider implementation (test or real).
    sufficiency_policy: EvidenceSufficiencyPolicy (configurable thresholds).
    prompt_builder:     PromptBuilder (configurable context budget).
    citation_validator: CitationValidator (configurable excerpt length).
    max_evidence_items: Maximum evidence items to include in the packet.
    min_hybrid_score:   Minimum retrieval score to accept as evidence.
    """

    def __init__(
        self,
        retrieval_service: KnowledgeRetrievalService,
        answer_provider: AnswerProvider,
        sufficiency_policy: EvidenceSufficiencyPolicy | None = None,
        prompt_builder: PromptBuilder | None = None,
        citation_validator: CitationValidator | None = None,
        max_evidence_items: int = 10,
        min_hybrid_score: float = 0.0,
    ) -> None:
        self._retrieval = retrieval_service
        self._provider = answer_provider
        self._policy = sufficiency_policy or EvidenceSufficiencyPolicy()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._citation_validator = citation_validator or CitationValidator()
        self._max_evidence_items = max_evidence_items
        self._min_hybrid_score = min_hybrid_score

    async def answer(
        self,
        *,
        question: str,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        organisation_id: uuid.UUID,
        top_k: int = 10,
    ) -> GroundedAnswerResponse:
        """
        Run the full grounded answering pipeline.

        Parameters
        ----------
        question:         Raw user question (normalised internally).
        session:          OrganisationScopedSession with active RLS (passed from HTTP layer).
        workspace_id:     Scopes retrieval to a single workspace.
        organisation_id:  Scopes retrieval to a single organisation.
        top_k:            Number of retrieval candidates to retrieve.

        Returns
        -------
        GroundedAnswerResponse — never raises; provider failures are caught.
        """
        # ── Step 1: Normalise question ──────────────────────────────────────
        import time as _time

        _t0 = _time.monotonic()
        question_norm = _normalise_question(question)

        # Observability: emit answer.started (question text NOT logged).
        log_answer_started(
            workspace_id=workspace_id,
            organisation_id=organisation_id,
            question_length=len(question_norm),
            question_fingerprint=make_query_fingerprint(question_norm) if question_norm else "",
            top_k=top_k,
        )

        if not question_norm:
            log_answer_abstained(
                workspace_id=workspace_id,
                organisation_id=organisation_id,
                reason=AnswerStatus.ABSTAIN_NO_EVIDENCE,
                evidence_band="none",
                duration_ms=(_time.monotonic() - _t0) * 1000,
            )
            return GroundedAnswerResponse(
                status=AnswerStatus.ABSTAIN_NO_EVIDENCE,
                answer_text=(
                    "No relevant information was found in the available knowledge base "
                    "to answer this question."
                ),
            )

        # ── Step 2: Phase 2B retrieval (session-scoped, RLS-enforced) ───────
        try:
            retrieval_response = await self._retrieval.retrieve(
                session=session,
                organisation_id=organisation_id,
                workspace_id=workspace_id,
                request=RetrievalRequest(query=question_norm, limit=top_k),
            )
            retrieval_results = retrieval_response.results
        except RetrievalQueryError as exc:
            logger.warning(
                "Retrieval query error for workspace=%s: %s",
                workspace_id,
                exc,
            )
            return GroundedAnswerResponse(
                status=AnswerStatus.ABSTAIN_NO_EVIDENCE,
                answer_text=(
                    "No relevant information was found in the available knowledge base "
                    "to answer this question."
                ),
            )

        # ── Step 3: Build EvidencePacket ────────────────────────────────────
        packet: EvidencePacket = build_evidence_packet(
            query=question_norm,
            results=retrieval_results,
            max_items=self._max_evidence_items,
            min_hybrid_score=self._min_hybrid_score,
        )

        # ── Step 4: Assess sufficiency (deterministic — no LLM) ────────────
        outcome: SufficiencyOutcome = self._policy.assess(packet)

        if outcome != SufficiencyOutcome.ANSWER:
            # Abstain: do NOT call AnswerProvider.
            log_answer_abstained(
                workspace_id=workspace_id,
                organisation_id=organisation_id,
                reason=outcome.value,
                evidence_band=packet.evidence_band.value,
                duration_ms=(_time.monotonic() - _t0) * 1000,
            )
            return GroundedAnswerResponse(
                status=outcome.value,
                answer_text=EvidenceSufficiencyPolicy.abstention_message(outcome),
                evidence_band=packet.evidence_band.value,
                evidence_score=packet.evidence_score_internal,
                limitations=packet.limitations,
                suspicious_count=packet.suspicious_count,
            )

        # ── Step 5: Build safe prompt ────────────────────────────────────────
        prompt = self._prompt_builder.build(question_norm, packet)

        # ── Step 6: Call AnswerProvider ─────────────────────────────────────
        try:
            provider_answer = await self._provider.generate(
                question=question_norm,
                evidence_packet=packet,
                prompt=prompt,
            )
        except AnswerProviderError as exc:
            # Never expose exception detail, API keys, or system prompt to caller.
            logger.error(
                "AnswerProvider failure for workspace=%s: %s",
                workspace_id,
                type(exc).__name__,
            )
            log_answer_provider_failure(
                workspace_id=workspace_id,
                organisation_id=organisation_id,
                error_type=type(exc).__name__,
                provider_id=self._provider.provider_id,
                model_id=self._provider.model_id,
                attempt_count=1,
                duration_ms=(_time.monotonic() - _t0) * 1000,
            )
            return GroundedAnswerResponse(
                status=AnswerStatus.PROVIDER_FAILURE,
                answer_text="Unable to generate a grounded answer at this time.",
                evidence_band=packet.evidence_band.value,
                evidence_score=packet.evidence_score_internal,
                limitations=packet.limitations,
                suspicious_count=packet.suspicious_count,
            )
        except Exception as exc:
            # Broad catch — never let unexpected provider errors leak internals.
            logger.error(
                "Unexpected AnswerProvider error for workspace=%s: %s",
                workspace_id,
                type(exc).__name__,
            )
            log_answer_provider_failure(
                workspace_id=workspace_id,
                organisation_id=organisation_id,
                error_type=type(exc).__name__,
                provider_id=self._provider.provider_id,
                model_id=self._provider.model_id,
                attempt_count=1,
                duration_ms=(_time.monotonic() - _t0) * 1000,
            )
            return GroundedAnswerResponse(
                status=AnswerStatus.PROVIDER_FAILURE,
                answer_text="Unable to generate a grounded answer at this time.",
                evidence_band=packet.evidence_band.value,
                evidence_score=packet.evidence_score_internal,
                limitations=packet.limitations,
                suspicious_count=packet.suspicious_count,
            )

        # ── Step 7: Validate citation IDs ────────────────────────────────────
        try:
            citations = self._citation_validator.validate(
                provider_citation_ids=provider_answer.citation_ids,
                evidence_packet=packet,
            )
        except CitationValidationError as exc:
            # Fabricated or invalid citation IDs — safe failure, no crash.
            logger.warning(
                "Citation validation failed for workspace=%s: %s",
                workspace_id,
                exc,
            )
            citations = []

        # ── Step 8: Rewrite citation markers in answer text ──────────────────
        answer_text = rewrite_citations_in_answer(
            answer_text=provider_answer.answer_text,
            citations=citations,
        )

        # ── Step 9: Return grounded answer ────────────────────────────────────
        log_answer_completed(
            workspace_id=workspace_id,
            organisation_id=organisation_id,
            evidence_band=packet.evidence_band.value,
            citation_count=len(citations),
            suspicious_count=packet.suspicious_count,
            provider_id=provider_answer.provider,
            model_id=provider_answer.model,
            duration_ms=(_time.monotonic() - _t0) * 1000,
        )
        return GroundedAnswerResponse(
            status=AnswerStatus.ANSWER,
            answer_text=answer_text,
            citations=citations,
            evidence_band=packet.evidence_band.value,
            evidence_score=packet.evidence_score_internal,
            provider=provider_answer.provider,
            model=provider_answer.model,
            limitations=packet.limitations,
            suspicious_count=packet.suspicious_count,
        )
