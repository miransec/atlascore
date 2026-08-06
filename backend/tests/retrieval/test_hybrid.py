"""
Unit tests for hybrid RRF fusion (app.retrieval.hybrid).

Pure Python — no database, no async.
"""

import uuid

import pytest

from app.retrieval.hybrid import RRF_K, reciprocal_rank_fusion
from app.retrieval.lexical import LexicalCandidate
from app.retrieval.schemas import RetrievalResult
from app.retrieval.vector import VectorCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lexical(chunk_id: uuid.UUID, score: float = 0.5) -> LexicalCandidate:
    return LexicalCandidate(
        chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        document_title="Doc",
        source_name="Source",
        version_number=1,
        chunk_index=0,
        content="lexical content",
        lexical_score=score,
    )


def _make_vector(chunk_id: uuid.UUID, score: float = 0.9) -> VectorCandidate:
    return VectorCandidate(
        chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        document_title="Doc",
        source_name="Source",
        version_number=1,
        chunk_index=0,
        content="vector content",
        vector_score=score,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRRFConstant:
    def test_rrf_k_is_60(self) -> None:
        assert RRF_K == 60


class TestEmptyInputs:
    def test_both_empty_returns_empty(self) -> None:
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_empty_vector_returns_lexical_only(self) -> None:
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([_make_lexical(cid)], [])
        assert len(result) == 1
        assert result[0].chunk_id == cid
        assert result[0].lexical_rank == 1
        assert result[0].vector_rank is None

    def test_empty_lexical_returns_vector_only(self) -> None:
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([], [_make_vector(cid)])
        assert len(result) == 1
        assert result[0].chunk_id == cid
        assert result[0].vector_rank == 1
        assert result[0].lexical_rank is None


class TestRRFScoreFormula:
    def test_single_lexical_rank1_score(self) -> None:
        """RRF(d) = 1/(60+1) for rank 1 from one list."""
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([_make_lexical(cid)], [])
        expected = 1.0 / (60 + 1)
        assert abs(result[0].hybrid_score - expected) < 1e-12

    def test_single_vector_rank1_score(self) -> None:
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([], [_make_vector(cid)])
        expected = 1.0 / (60 + 1)
        assert abs(result[0].hybrid_score - expected) < 1e-12

    def test_both_lists_rank1_score_double(self) -> None:
        """Chunk in both lists at rank 1 gets 2*(1/(60+1))."""
        cid = uuid.uuid4()
        l_cand = _make_lexical(cid)
        v_cand = _make_vector(cid)
        result = reciprocal_rank_fusion([l_cand], [v_cand])
        expected = 2.0 / (60 + 1)
        assert abs(result[0].hybrid_score - expected) < 1e-12


class TestDuplicateFusion:
    def test_same_chunk_fused_into_one_result(self) -> None:
        cid = uuid.uuid4()
        l_cand = _make_lexical(cid)
        v_cand = _make_vector(cid)
        result = reciprocal_rank_fusion([l_cand], [v_cand])
        # Must appear exactly once.
        assert len(result) == 1

    def test_fused_result_has_both_scores(self) -> None:
        cid = uuid.uuid4()
        l_cand = _make_lexical(cid, score=0.4)
        v_cand = _make_vector(cid, score=0.8)
        result = reciprocal_rank_fusion([l_cand], [v_cand])
        assert result[0].lexical_score == pytest.approx(0.4)
        assert result[0].vector_score == pytest.approx(0.8)
        assert result[0].lexical_rank == 1
        assert result[0].vector_rank == 1


class TestBothListBoost:
    def test_both_list_chunk_ranks_above_single_list(self) -> None:
        """
        Chunk A appears in both lists at rank 1.
        Chunk B appears only in lexical at rank 1 (same rank as A in lexical).
        A.hybrid > B.hybrid because A gets vector contribution too.
        """
        cid_a = uuid.uuid4()
        cid_b = uuid.uuid4()
        # A is rank 1 in both lists.
        l_a = _make_lexical(cid_a)
        v_a = _make_vector(cid_a)
        # B is rank 1 in lexical only.
        l_b = _make_lexical(cid_b)
        result = reciprocal_rank_fusion([l_a, l_b], [v_a])
        score_a = next(r for r in result if r.chunk_id == cid_a).hybrid_score
        score_b = next(r for r in result if r.chunk_id == cid_b).hybrid_score
        assert score_a > score_b


class TestOrdering:
    def test_results_sorted_by_hybrid_score_descending(self) -> None:
        ids = [uuid.uuid4() for _ in range(5)]
        # Give them to lexical in reverse order of what we expect from RRF.
        lexical = [_make_lexical(cid) for cid in ids]
        result = reciprocal_rank_fusion(lexical, [])
        scores = [r.hybrid_score for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_tie_broken_by_chunk_id_ascending(self) -> None:
        """Two chunks each appearing in one list at rank 1 must tie on score."""
        cid_a = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        cid_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        l_a = _make_lexical(cid_a)
        v_b = _make_vector(cid_b)
        result = reciprocal_rank_fusion([l_a], [v_b])
        assert len(result) == 2
        assert abs(result[0].hybrid_score - result[1].hybrid_score) < 1e-12
        # Tie broken by chunk_id ascending.
        assert str(result[0].chunk_id) < str(result[1].chunk_id)

    def test_tie_break_deterministic_across_calls(self) -> None:
        """Same input must produce identical output on repeated calls."""
        ids = [uuid.uuid4() for _ in range(4)]
        # All four appear in one list — all tied at their respective ranks.
        lexical = [_make_lexical(cid) for cid in ids]
        r1 = reciprocal_rank_fusion(lexical, [])
        r2 = reciprocal_rank_fusion(lexical, [])
        assert [r.chunk_id for r in r1] == [r.chunk_id for r in r2]


class TestLimit:
    def test_limit_respected(self) -> None:
        ids = [uuid.uuid4() for _ in range(10)]
        lexical = [_make_lexical(cid) for cid in ids]
        result = reciprocal_rank_fusion(lexical, [], limit=3)
        assert len(result) == 3

    def test_limit_zero_returns_empty(self) -> None:
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([_make_lexical(cid)], [], limit=0)
        assert result == []

    def test_result_never_exceeds_limit(self) -> None:
        ids = [uuid.uuid4() for _ in range(20)]
        lexical = [_make_lexical(cid) for cid in ids]
        vectors = [_make_vector(cid) for cid in ids]
        result = reciprocal_rank_fusion(lexical, vectors, limit=5)
        assert len(result) <= 5


class TestReturnType:
    def test_returns_list_of_retrieval_result(self) -> None:
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([_make_lexical(cid)], [])
        assert isinstance(result, list)
        assert all(isinstance(r, RetrievalResult) for r in result)

    def test_metadata_field_is_dict(self) -> None:
        cid = uuid.uuid4()
        result = reciprocal_rank_fusion([_make_lexical(cid)], [])
        assert isinstance(result[0].metadata, dict)


class TestRankRecording:
    def test_lexical_rank_recorded(self) -> None:
        ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        lexical = [_make_lexical(cid) for cid in ids]
        result = reciprocal_rank_fusion(lexical, [])
        rank_map = {r.chunk_id: r.lexical_rank for r in result}
        for expected_rank, cid in enumerate(ids, start=1):
            assert rank_map[cid] == expected_rank

    def test_vector_rank_recorded(self) -> None:
        ids = [uuid.uuid4(), uuid.uuid4()]
        vectors = [_make_vector(cid) for cid in ids]
        result = reciprocal_rank_fusion([], vectors)
        rank_map = {r.chunk_id: r.vector_rank for r in result}
        for expected_rank, cid in enumerate(ids, start=1):
            assert rank_map[cid] == expected_rank
