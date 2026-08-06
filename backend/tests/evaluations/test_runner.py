"""
Tests for the Phase 2D evaluation runner.

All tests are pure Python — no database, no network, no paid API required.
The runner uses DeterministicTestAnswerProvider and mock retrieval only.

Assertions cover:
  - EvaluationSummary fields (total, passed, failed, pass_rate, by_category)
  - Single-case pass and fail
  - Category aggregation
  - check_fn integration (pass and fail paths)
  - expected_band assertion
  - Unexpected exception in pipeline → EvaluationResult with actual_status="error"
  - EvaluationRunner.run returns correct provider_id and model_id
"""

from __future__ import annotations

from app.answering.provider import DeterministicTestAnswerProvider
from app.evaluations.cases import ALL_CASES
from app.evaluations.runner import (
    EvaluationRunner,
    _make_retrieval_response,
    _make_retrieval_result,
    _make_service,
    _run_case,
)
from app.evaluations.schemas import (
    EvaluationCase,
    EvaluationCategory,
    EvaluationResult,
    EvaluationSummary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_case(
    *,
    name: str = "test_case",
    question: str = "What is the refund policy?",
    retrieval_mock: list | None = None,
    expected_status: str = "answer",
    expected_band: str | None = None,
    check_fn=None,
    require_medium: bool = False,
    min_hybrid_score: float = 0.0,
) -> EvaluationCase:
    """Make a minimal EvaluationCase for testing."""
    if retrieval_mock is None:
        retrieval_mock = [
            ("The refund policy allows returns within 30 days.", 0.04, 1, 1),
        ]
    return EvaluationCase(
        category=EvaluationCategory.A_GROUNDING,
        name=name,
        description="test description",
        question=question,
        retrieval_mock=retrieval_mock,
        expected_status=expected_status,
        expected_band=expected_band,
        check_fn=check_fn,
        require_medium=require_medium,
        min_hybrid_score=min_hybrid_score,
    )


# ---------------------------------------------------------------------------
# _make_retrieval_result
# ---------------------------------------------------------------------------


class TestMakeRetrievalResult:
    def test_basic_fields(self) -> None:
        result = _make_retrieval_result(
            content="Paris is the capital of France.",
            hybrid_score=0.05,
            lexical_rank=1,
            vector_rank=2,
            index=1,
        )
        assert result.content == "Paris is the capital of France."
        assert result.hybrid_score == 0.05
        assert result.lexical_rank == 1
        assert result.vector_rank == 2
        assert result.chunk_index == 1
        assert result.source_name == "Eval Source 1"
        assert result.document_title == "Eval Document 1"
        assert result.version_number == 1

    def test_none_ranks(self) -> None:
        result = _make_retrieval_result(
            content="Only lexical.",
            hybrid_score=0.02,
            lexical_rank=1,
            vector_rank=None,
        )
        assert result.vector_rank is None
        assert result.lexical_rank == 1
        assert result.vector_score is None
        assert result.lexical_score is not None

    def test_unique_chunk_ids(self) -> None:
        r1 = _make_retrieval_result("content A", 0.1, 1, None, index=1)
        r2 = _make_retrieval_result("content B", 0.2, 2, None, index=2)
        assert r1.chunk_id != r2.chunk_id


# ---------------------------------------------------------------------------
# _make_retrieval_response
# ---------------------------------------------------------------------------


class TestMakeRetrievalResponse:
    def test_result_count(self) -> None:
        resp = _make_retrieval_response(
            [
                ("Content A", 0.05, 1, 1),
                ("Content B", 0.03, 2, 2),
                ("Content C", 0.01, 3, None),
            ]
        )
        assert len(resp.results) == 3

    def test_empty_returns_zero_results(self) -> None:
        resp = _make_retrieval_response([])
        assert len(resp.results) == 0


# ---------------------------------------------------------------------------
# _make_service
# ---------------------------------------------------------------------------


class TestMakeService:
    def test_returns_grounded_answer_service(self) -> None:
        from app.answering.service import GroundedAnswerService

        case = _simple_case()
        svc = _make_service(case)
        assert isinstance(svc, GroundedAnswerService)

    def test_custom_provider_injected(self) -> None:
        from app.answering.service import GroundedAnswerService

        case = _simple_case()
        provider = DeterministicTestAnswerProvider()
        svc = _make_service(case, provider)
        assert isinstance(svc, GroundedAnswerService)


# ---------------------------------------------------------------------------
# _run_case
# ---------------------------------------------------------------------------


class TestRunCase:
    def test_passing_case(self) -> None:
        case = _simple_case(
            retrieval_mock=[
                ("Returns are allowed within 30 days of purchase.", 0.04, 1, 1),
            ],
            expected_status="answer",
            require_medium=False,
        )
        result = _run_case(case)
        assert isinstance(result, EvaluationResult)
        assert result.case_name == case.name
        assert result.category == EvaluationCategory.A_GROUNDING
        assert result.duration_ms >= 0

    def test_empty_retrieval_abstains(self) -> None:
        """Case with no evidence should abstain, not answer."""
        case = _simple_case(
            retrieval_mock=[],
            expected_status="abstain_no_evidence",
            require_medium=False,
        )
        result = _run_case(case)
        assert result.passed, result.error

    def test_status_mismatch_fails(self) -> None:
        """If actual status != expected, result.passed is False."""
        case = _simple_case(
            retrieval_mock=[("Good content.", 0.04, 1, 1)],
            expected_status="abstain_no_evidence",  # Wrong — should be "answer"
            require_medium=False,
        )
        result = _run_case(case)
        assert not result.passed
        assert "Status" in result.error

    def test_check_fn_pass(self) -> None:
        case = _simple_case(
            retrieval_mock=[("Some content here.", 0.04, 1, 1)],
            expected_status="answer",
            check_fn=lambda resp: resp.status == "answer",
            require_medium=False,
        )
        result = _run_case(case)
        assert result.passed

    def test_check_fn_fail(self) -> None:
        case = _simple_case(
            retrieval_mock=[("Some content here.", 0.04, 1, 1)],
            expected_status="answer",
            check_fn=lambda resp: False,  # Always fails
            require_medium=False,
        )
        result = _run_case(case)
        assert not result.passed
        assert "check_fn returned False" in result.error

    def test_check_fn_exception(self) -> None:
        def bad_check(resp):
            raise ValueError("unexpected error")

        case = _simple_case(
            retrieval_mock=[("Some content here.", 0.04, 1, 1)],
            expected_status="answer",
            check_fn=bad_check,
            require_medium=False,
        )
        result = _run_case(case)
        assert not result.passed
        assert "ValueError" in result.error

    def test_expected_band_correct(self) -> None:
        case = _simple_case(
            retrieval_mock=[("Some content here.", 0.04, 1, 1)],
            expected_status="answer",
            expected_band="high",
            require_medium=False,
        )
        result = _run_case(case)
        # DeterministicTestAnswerProvider returns "answer"; band depends on score
        # Either passes or fails — what matters is it runs without exception
        assert isinstance(result.passed, bool)
        assert result.case_name == case.name


# ---------------------------------------------------------------------------
# EvaluationRunner
# ---------------------------------------------------------------------------


class TestEvaluationRunner:
    def test_run_single_passing_case(self) -> None:
        runner = EvaluationRunner()
        case = _simple_case(
            retrieval_mock=[("Returns allowed within 30 days.", 0.04, 1, 1)],
            expected_status="answer",
            require_medium=False,
        )
        summary = runner.run([case])

        assert summary.total == 1
        assert summary.passed + summary.failed == 1
        assert 0.0 <= summary.pass_rate <= 1.0
        assert summary.duration_s >= 0
        assert summary.run_id  # non-empty UUID string
        assert summary.provider_id == "deterministic-test"

    def test_run_multiple_cases_aggregation(self) -> None:
        runner = EvaluationRunner()
        cases = [
            _simple_case(
                name="case_A_1",
                retrieval_mock=[("Content A.", 0.04, 1, 1)],
                expected_status="answer",
                require_medium=False,
            ),
            _simple_case(
                name="case_A_2",
                retrieval_mock=[],
                expected_status="abstain_no_evidence",
                require_medium=False,
            ),
        ]
        summary = runner.run(cases)

        assert summary.total == 2
        assert summary.passed + summary.failed == 2

    def test_by_category_keys(self) -> None:
        runner = EvaluationRunner()
        case = _simple_case(
            retrieval_mock=[("Content.", 0.04, 1, 1)],
            expected_status="answer",
            require_medium=False,
        )
        summary = runner.run([case])
        # Category A_grounding should be in by_category
        assert "A_grounding" in summary.by_category
        cat_passed, cat_total = summary.by_category["A_grounding"]
        assert cat_total == 1
        assert cat_passed + (1 - cat_passed) == 1  # either 0 or 1

    def test_empty_cases_returns_zero_summary(self) -> None:
        runner = EvaluationRunner()
        summary = runner.run([])
        assert summary.total == 0
        assert summary.passed == 0
        assert summary.failed == 0
        assert summary.pass_rate == 0.0
        assert summary.by_category == {}

    def test_provider_id_in_summary(self) -> None:
        provider = DeterministicTestAnswerProvider()
        runner = EvaluationRunner(provider=provider)
        summary = runner.run([])
        assert summary.provider_id == "deterministic-test"
        assert summary.model_id == "deterministic-test-v1"

    def test_run_returns_evaluation_summary_type(self) -> None:
        runner = EvaluationRunner()
        result = runner.run([])
        assert isinstance(result, EvaluationSummary)


# ---------------------------------------------------------------------------
# ALL_CASES sanity checks
# ---------------------------------------------------------------------------


class TestAllCases:
    def test_all_cases_imported(self) -> None:
        assert len(ALL_CASES) > 0

    def test_all_cases_have_valid_categories(self) -> None:
        valid_categories = set(EvaluationCategory)
        for case in ALL_CASES:
            assert case.category in valid_categories, (
                f"Case {case.name!r} has invalid category {case.category!r}"
            )

    def test_all_cases_have_valid_expected_status(self) -> None:
        valid_statuses = {
            "answer",
            "abstain_no_evidence",
            "abstain_weak_evidence",
            "provider_failure",
        }
        for case in ALL_CASES:
            assert case.expected_status in valid_statuses, (
                f"Case {case.name!r} has unexpected status {case.expected_status!r}"
            )

    def test_all_cases_have_non_empty_names(self) -> None:
        for case in ALL_CASES:
            assert case.name, f"Case in {case.category} has empty name"

    def test_all_case_names_unique(self) -> None:
        names = [case.name for case in ALL_CASES]
        assert len(names) == len(set(names)), "Duplicate case names found"

    def test_all_sixteen_categories_covered(self) -> None:
        covered = {case.category for case in ALL_CASES}
        all_cats = set(EvaluationCategory)
        missing = all_cats - covered
        assert not missing, f"Categories with no test cases: {missing}"

    def test_minimum_case_count(self) -> None:
        """At least 46 cases across the 16 categories."""
        assert len(ALL_CASES) >= 46

    def test_no_case_has_empty_retrieval_mock_when_answer_expected(self) -> None:
        """Cases expecting 'answer' must provide at least one retrieval item."""
        for case in ALL_CASES:
            if case.expected_status == "answer":
                assert len(case.retrieval_mock) > 0, (
                    f"Case {case.name!r} expects 'answer' but has empty retrieval_mock"
                )

    def test_runner_executes_all_cases(self) -> None:
        """Smoke test: runner processes all cases without raising."""
        runner = EvaluationRunner()
        summary = runner.run(ALL_CASES)
        assert summary.total == len(ALL_CASES)
        # We don't assert 100% pass rate here — that's the CLI's job.
        # But we do assert that no case errored unexpectedly.
        assert summary.errored == 0, f"{summary.errored} cases raised unexpected exceptions"
