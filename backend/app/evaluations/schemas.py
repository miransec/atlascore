"""
Evaluation schemas — Phase 2D.

EvaluationCase:    Input for one evaluation scenario.
EvaluationResult:  Output from running one case through the pipeline.
EvaluationSummary: Aggregated metrics across a full evaluation run.

These are first-party types — no LangChain, no LlamaIndex, no external framework.

IMPORTANT TERMINOLOGY:
  These measure deterministic SYSTEM BEHAVIOUR — pipeline correctness, abstention
  accuracy, citation integrity, security property preservation.
  They do NOT measure "model accuracy" (we do not have oracle LLM judgements).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvaluationCategory(StrEnum):
    """
    Evaluation categories A-P.

    Each category groups related test scenarios.
    Categories are designed to be independent; failing one does not invalidate others.
    """

    A_GROUNDING = "A_grounding"
    B_ABSTENTION = "B_abstention"
    C_CITATION = "C_citation"
    D_INJECTION_RESISTANCE = "D_injection_resistance"
    E_QUESTION_NORMALISATION = "E_question_normalisation"
    F_EVIDENCE_BAND = "F_evidence_band"
    G_PROVIDER_FAILURE = "G_provider_failure"
    H_SUFFICIENCY_POLICY = "H_sufficiency_policy"
    I_EVIDENCE_CAPPING = "I_evidence_capping"
    J_CONFLICT_HANDLING = "J_conflict_handling"
    K_MULTI_SOURCE = "K_multi_source"
    L_SECURITY = "L_security"
    M_LIMITATIONS = "M_limitations"
    N_SUSPICIOUS_COUNT = "N_suspicious_count"
    O_EDGE_CASES = "O_edge_cases"
    P_SCORE_CALIBRATION = "P_score_calibration"


@dataclass
class EvaluationCase:
    """
    A single evaluation scenario.

    category:       EvaluationCategory this case belongs to.
    name:           Human-readable identifier (unique within a category).
    description:    What property this case is testing.
    question:       The question to ask.
    retrieval_mock: List of (content, hybrid_score, lexical_rank, vector_rank) tuples.
                    These are fed directly to build_evidence_packet() — no real DB.
    expected_status: Expected GroundedAnswerResponse.status (e.g. "answer", "abstain_no_evidence").
    expected_band:  Expected evidence_band value, or None if not asserted.
    check_fn:       Optional function(response) -> bool for additional assertions.
    require_medium: Whether to use require_medium=True for this case (default True).
    min_hybrid_score: Minimum hybrid score filter for this case (default 0.0).
    tags:           Free-form tags for filtering/reporting.
    """

    category: EvaluationCategory
    name: str
    description: str
    question: str
    retrieval_mock: list[tuple[str, float, int | None, int | None]]
    expected_status: str
    expected_band: str | None = None
    check_fn: Callable[[Any], bool] | None = None
    require_medium: bool = True
    min_hybrid_score: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class EvaluationResult:
    """
    Result of running one EvaluationCase.

    passed:      True if all assertions held.
    case_name:   EvaluationCase.name.
    category:    EvaluationCase.category.
    actual_status: The status returned by the pipeline.
    actual_band:   The evidence_band returned.
    error:       Description of what failed, or empty string.
    details:     Any extra context (e.g. answer text excerpt).
    duration_ms: How long the case took in milliseconds.
    """

    passed: bool
    case_name: str
    category: EvaluationCategory
    actual_status: str
    actual_band: str
    error: str = ""
    details: str = ""
    duration_ms: float = 0.0


@dataclass
class EvaluationSummary:
    """
    Aggregated metrics across a full evaluation run.

    total:       Total cases run.
    passed:      Cases that passed all assertions.
    failed:      Cases that failed at least one assertion.
    errored:     Cases that raised unexpected exceptions.
    pass_rate:   passed / total (0.0-1.0).
    by_category: Dict[category_name, (passed, total)].
    results:     All individual EvaluationResult objects.
    run_id:      Unique ID for this run (UUID4).
    duration_s:  Total wall-clock seconds for the run.
    """

    total: int
    passed: int
    failed: int
    errored: int
    pass_rate: float
    by_category: dict[str, tuple[int, int]]
    results: list[EvaluationResult]
    run_id: str
    duration_s: float
    provider_id: str
    model_id: str
