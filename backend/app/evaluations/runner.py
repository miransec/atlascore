"""
EvaluationRunner — Phase 2D.

Runs EvaluationCase objects through the GroundedAnswerService pipeline
using mock retrieval (no database, no network, no paid API required).

Design:
  - Injects a mock retrieval service that returns controlled RetrievalResult lists.
  - Uses DeterministicTestAnswerProvider by default (no API key needed).
  - Supports a real provider via build_answer_provider() for end-to-end eval.
  - Collects EvaluationResult per case; aggregates into EvaluationSummary.
  - No LangChain, no LlamaIndex, no external evaluation framework.

IMPORTANT TERMINOLOGY:
  This measures deterministic SYSTEM BEHAVIOUR — pipeline correctness, abstention
  gates, citation integrity, security properties. Not "model accuracy".
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.answering.citation import CitationValidator
from app.answering.prompt import PromptBuilder
from app.answering.provider import AnswerProvider, DeterministicTestAnswerProvider
from app.answering.service import GroundedAnswerResponse, GroundedAnswerService
from app.answering.sufficiency import EvidenceSufficiencyPolicy
from app.evaluations.schemas import (
    EvaluationCase,
    EvaluationResult,
    EvaluationSummary,
)
from app.retrieval.schemas import RetrievalResponse, RetrievalResult

logger = logging.getLogger(__name__)

_EVAL_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_EVAL_WS_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

# Shared dummy UUIDs for retrieval result provenance.
_CHUNK_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SOURCE_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
_DOC_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000001")
_DOC_VER_ID = uuid.UUID("dddddddd-0000-0000-0000-000000000001")


def _make_retrieval_result(
    content: str,
    hybrid_score: float,
    lexical_rank: int | None,
    vector_rank: int | None,
    index: int = 1,
) -> RetrievalResult:
    """Convert a retrieval_mock tuple into a RetrievalResult for the runner."""
    return RetrievalResult(
        chunk_id=uuid.uuid5(_CHUNK_ID, f"chunk-{index}-{content[:20]}"),
        source_id=uuid.uuid5(_SOURCE_ID, f"source-{index}"),
        document_id=uuid.uuid5(_DOC_ID, f"doc-{index}"),
        document_version_id=uuid.uuid5(_DOC_VER_ID, f"ver-{index}"),
        chunk_index=index,
        source_name=f"Eval Source {index}",
        document_title=f"Eval Document {index}",
        version_number=1,
        content=content,
        hybrid_score=hybrid_score,
        lexical_rank=lexical_rank,
        vector_rank=vector_rank,
        lexical_score=hybrid_score * 0.6 if lexical_rank else None,
        vector_score=hybrid_score * 0.4 if vector_rank else None,
        metadata={},
    )


def _make_retrieval_response(
    mock_tuples: list[tuple[str, float, int | None, int | None]],
) -> RetrievalResponse:
    results = [
        _make_retrieval_result(content, score, lex, vec, idx)
        for idx, (content, score, lex, vec) in enumerate(mock_tuples, start=1)
    ]
    return RetrievalResponse(
        results=results,
        total=len(results),
        query_length=10,
    )


def _make_service(
    case: EvaluationCase,
    provider: AnswerProvider | None = None,
) -> GroundedAnswerService:
    """Build a GroundedAnswerService wired for evaluation (no real DB, no network)."""
    mock_retrieval = MagicMock()
    mock_retrieval.retrieve = AsyncMock(return_value=_make_retrieval_response(case.retrieval_mock))

    return GroundedAnswerService(
        retrieval_service=mock_retrieval,
        answer_provider=provider or DeterministicTestAnswerProvider(),
        sufficiency_policy=EvidenceSufficiencyPolicy(require_medium=case.require_medium),
        prompt_builder=PromptBuilder(max_evidence_items=10, max_chars_per_chunk=1500),
        citation_validator=CitationValidator(max_excerpt_chars=200),
        max_evidence_items=10,
        min_hybrid_score=case.min_hybrid_score,
    )


def _run_case(
    case: EvaluationCase,
    provider: AnswerProvider | None = None,
) -> EvaluationResult:
    """Run a single EvaluationCase. Returns EvaluationResult."""
    start = time.monotonic()
    svc = _make_service(case, provider)
    mock_session = MagicMock()

    try:
        response: GroundedAnswerResponse = asyncio.run(
            svc.answer(
                question=case.question,
                session=mock_session,
                workspace_id=_EVAL_WS_ID,
                organisation_id=_EVAL_ORG_ID,
            )
        )
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        logger.error("Case %s raised unexpected exception: %s", case.name, type(exc).__name__)
        return EvaluationResult(
            passed=False,
            case_name=case.name,
            category=case.category,
            actual_status="error",
            actual_band="none",
            error=f"Unexpected exception: {type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
        )

    duration_ms = (time.monotonic() - start) * 1000

    # ── Assertions ──────────────────────────────────────────────────────────
    errors: list[str] = []

    # Status check.
    if response.status != case.expected_status:
        errors.append(f"Status: expected={case.expected_status!r} actual={response.status!r}")

    # Evidence band check (if specified).
    if case.expected_band is not None and response.evidence_band != case.expected_band:
        errors.append(f"Band: expected={case.expected_band!r} actual={response.evidence_band!r}")

    # Custom check function.
    if case.check_fn is not None:
        try:
            check_passed = case.check_fn(response)
            if not check_passed:
                errors.append("check_fn returned False")
        except Exception as exc:
            errors.append(f"check_fn raised {type(exc).__name__}: {exc}")

    passed = len(errors) == 0
    return EvaluationResult(
        passed=passed,
        case_name=case.name,
        category=case.category,
        actual_status=response.status,
        actual_band=response.evidence_band,
        error="; ".join(errors) if errors else "",
        details=response.answer_text[:120] if response.answer_text else "",
        duration_ms=duration_ms,
    )


class EvaluationRunner:
    """
    Runs a list of EvaluationCase objects and produces an EvaluationSummary.

    Usage:
        runner = EvaluationRunner()
        summary = runner.run(ALL_CASES)
        print(summary.pass_rate)

    The runner uses DeterministicTestAnswerProvider by default.
    Pass a real provider to evaluate end-to-end behaviour (requires credentials).
    """

    def __init__(self, provider: AnswerProvider | None = None) -> None:
        self._provider = provider or DeterministicTestAnswerProvider()

    def run(self, cases: list[EvaluationCase]) -> EvaluationSummary:
        """Run all cases and return aggregated EvaluationSummary."""
        run_id = str(uuid.uuid4())
        start = time.monotonic()
        results: list[EvaluationResult] = []

        for case in cases:
            result = _run_case(case, self._provider)
            results.append(result)
            status_icon = "✓" if result.passed else "✗"
            logger.debug(
                "%s [%s] %s — %s (%.1fms)",
                status_icon,
                case.category,
                case.name,
                result.actual_status,
                result.duration_ms,
            )

        duration_s = time.monotonic() - start
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        errored = sum(1 for r in results if r.actual_status == "error")
        failed = total - passed

        # Per-category aggregation.
        by_category: dict[str, tuple[int, int]] = {}
        for result in results:
            cat = result.category.value
            cat_passed, cat_total = by_category.get(cat, (0, 0))
            by_category[cat] = (
                cat_passed + (1 if result.passed else 0),
                cat_total + 1,
            )

        return EvaluationSummary(
            total=total,
            passed=passed,
            failed=failed,
            errored=errored,
            pass_rate=passed / total if total > 0 else 0.0,
            by_category=by_category,
            results=results,
            run_id=run_id,
            duration_s=duration_s,
            provider_id=self._provider.provider_id,
            model_id=self._provider.model_id,
        )
