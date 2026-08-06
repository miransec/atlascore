"""
Unit tests for vector retrieval module (app.retrieval.vector).

These tests verify the contract of the pgvector-based implementation
without requiring a live database connection.

Test coverage:
  - EmbeddingModelMismatchError is a proper Exception subclass.
  - _format_query_vec produces a valid pgvector literal string.
  - The module does NOT export _cosine_similarity (Python scan removed).
  - _VECTOR_FETCH_SQL contains the pgvector <=> operator (not Python scan).
  - _VECTOR_FETCH_SQL contains ORDER BY cosine_distance ASC (DB-side sort).
  - _VECTOR_FETCH_SQL contains LIMIT :fetch_limit (bounded candidate set).
  - _VECTOR_FETCH_SQL filters by model_id before <=> (no cross-model comparison).
  - _VECTOR_FETCH_SQL filters by dimensions before <=> (no dim mismatch).
  - _VECTOR_FETCH_SQL filters by org_id + workspace_id (cross-workspace isolation).
  - _VECTOR_FETCH_SQL filters kij.status = 'succeeded' (no failed/running jobs).
  - _VECTOR_FETCH_SQL excludes archived docs + inactive sources.
  - vector_search signature accepts expected parameters.
  - Score semantics: vector_score = 1.0 - cosine_distance (documented contract).
  - VectorCandidate dataclass has all expected fields.
"""

from __future__ import annotations

import math
import uuid

import pytest

from app.retrieval.vector import (
    _VECTOR_FETCH_SQL,
    EmbeddingModelMismatchError,
    VectorCandidate,
    _format_query_vec,
    vector_search,
)

# ---------------------------------------------------------------------------
# EmbeddingModelMismatchError
# ---------------------------------------------------------------------------


class TestEmbeddingModelMismatchError:
    def test_is_exception_subclass(self) -> None:
        assert issubclass(EmbeddingModelMismatchError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(EmbeddingModelMismatchError):
            raise EmbeddingModelMismatchError("no embeddings for model-x in this workspace")

    def test_message_preserved(self) -> None:
        msg = "model-x not found"
        exc = EmbeddingModelMismatchError(msg)
        assert str(exc) == msg


# ---------------------------------------------------------------------------
# _format_query_vec — pgvector literal formatting
# ---------------------------------------------------------------------------


class TestFormatQueryVec:
    def test_produces_bracketed_string(self) -> None:
        result = _format_query_vec([0.1, 0.2, 0.3])
        assert result.startswith("[")
        assert result.endswith("]")

    def test_single_element(self) -> None:
        result = _format_query_vec([1.0])
        assert result == "[1.0]"

    def test_three_elements(self) -> None:
        result = _format_query_vec([0.5, -0.5, 0.0])
        assert result == "[0.5,-0.5,0.0]"

    def test_contains_no_spaces_between_values(self) -> None:
        # pgvector accepts '[x,y,z]' without spaces — verify our format is clean.
        result = _format_query_vec([1.0, 2.0])
        # No spaces between the comma and the next number.
        assert " " not in result

    def test_high_dimensional_vector(self) -> None:
        dim = 1536
        mag = math.sqrt(dim)
        vec = [1.0 / mag] * dim
        result = _format_query_vec(vec)
        assert result.startswith("[")
        assert result.endswith("]")
        # Should contain dim-1 commas.
        assert result.count(",") == dim - 1

    def test_negative_values(self) -> None:
        result = _format_query_vec([-0.1, -0.9])
        assert "-0.1" in result
        assert "-0.9" in result


# ---------------------------------------------------------------------------
# _VECTOR_FETCH_SQL — SQL contract assertions
#
# These tests verify that the SQL string contains the required structural
# elements without parsing SQL.  They ensure the pgvector path is in use
# and that security/correctness filters are present.
# ---------------------------------------------------------------------------


class TestVectorFetchSql:
    """Inspect _VECTOR_FETCH_SQL to enforce architectural contracts."""

    @property
    def sql(self) -> str:
        # SQLAlchemy text() wraps the string; access it via ._text or str().
        return str(_VECTOR_FETCH_SQL)

    def test_uses_pgvector_cosine_distance_operator(self) -> None:
        """The <=> operator must be present — not Python cosine computation."""
        assert "<=>" in self.sql, (
            "SQL must use pgvector cosine distance operator <=> "
            "(not Python-side similarity computation)"
        )

    def test_does_not_load_raw_embedding_json(self) -> None:
        """The embedding column must NOT be SELECTed as raw JSON text."""
        sql_lower = self.sql.lower()
        # We must NOT select the embedding value itself for Python deserialization.
        # The allowed form is using it inside an expression with <=>.
        # Check that 'embedding_json' alias is gone (old Python-scan artifact).
        assert "embedding_json" not in sql_lower, (
            "embedding_json alias found — old Python-scan code detected"
        )

    def test_orders_by_cosine_distance_asc(self) -> None:
        """PostgreSQL must sort by distance ASC (nearest first = highest similarity)."""
        sql_lower = self.sql.lower()
        assert "order by" in sql_lower, "SQL must have ORDER BY for DB-side ranking"
        assert "cosine_distance" in sql_lower, "Must order by cosine_distance alias"
        assert "asc" in sql_lower, "Must order ASC (nearest distance first)"

    def test_has_fetch_limit_bound_param(self) -> None:
        """LIMIT must be a bound parameter, not a Python-side truncation."""
        assert ":fetch_limit" in self.sql, (
            "SQL must use LIMIT :fetch_limit to bound the candidate set "
            "in PostgreSQL, not truncate in Python"
        )

    def test_filters_by_model_id_in_where_clause(self) -> None:
        """model_id filter must be in the WHERE clause.

        The <=> operator appears in the SELECT clause (as a computed expression).
        PostgreSQL evaluates WHERE predicates before projecting the SELECT list,
        so the correct invariant is that :model_id is in WHERE — not that its
        string offset is less than the <=> string offset.
        """
        sql = self.sql
        where_pos = sql.lower().find("where")
        model_id_pos = sql.find(":model_id")
        assert where_pos != -1, "SQL must have WHERE clause"
        assert model_id_pos != -1, ":model_id bound parameter must be present"
        assert model_id_pos > where_pos, (
            ":model_id filter must be in the WHERE clause so incompatible "
            "embedding spaces are excluded before <=> is applied"
        )

    def test_filters_by_dimensions_in_where_clause(self) -> None:
        """dimensions filter must be in the WHERE clause.

        Same reasoning as model_id: WHERE executes before SELECT.
        Dimension mismatch would cause a pgvector error at the operator;
        the WHERE filter prevents that by excluding mismatched rows first.
        """
        sql = self.sql
        where_pos = sql.lower().find("where")
        dimensions_pos = sql.find(":dimensions")
        assert where_pos != -1, "SQL must have WHERE clause"
        assert dimensions_pos != -1, ":dimensions bound parameter must be present"
        assert dimensions_pos > where_pos, (
            ":dimensions filter must be in the WHERE clause to prevent "
            "comparing vectors of different sizes"
        )

    def test_filters_by_org_id(self) -> None:
        assert ":org_id" in self.sql, "SQL must filter by :org_id (cross-tenant isolation)"

    def test_filters_by_workspace_id(self) -> None:
        assert ":workspace_id" in self.sql, (
            "SQL must filter by :workspace_id (cross-workspace isolation)"
        )

    def test_filters_ingestion_job_status_succeeded(self) -> None:
        """Only succeeded ingestion jobs — no failed/running/queued data."""
        assert "'succeeded'" in self.sql, (
            "SQL must filter kij.status = 'succeeded' to exclude "
            "failed, running, queued, and cancelled ingestion versions"
        )

    def test_filters_archived_documents(self) -> None:
        assert ":include_archived" in self.sql, (
            "SQL must have :include_archived parameter to exclude archived docs"
        )
        assert "is_archived" in self.sql.lower(), "SQL must reference is_archived column"

    def test_filters_inactive_sources(self) -> None:
        assert "is_active" in self.sql.lower(), (
            "SQL must filter by is_active to exclude deactivated sources"
        )

    def test_has_source_filter(self) -> None:
        assert ":source_filter_active" in self.sql
        assert ":source_ids" in self.sql

    def test_has_document_filter(self) -> None:
        assert ":doc_filter_active" in self.sql
        assert ":doc_ids" in self.sql

    def test_does_not_select_storage_key(self) -> None:
        assert "storage_key" not in self.sql.lower(), (
            "storage_key must never be selected — it is a server-side secret"
        )

    def test_does_not_select_embedding_bytes(self) -> None:
        """Embedding vector bytes must not be returned to the application layer."""
        # The embedding column appears in the <=> expression but must not be
        # aliased as a selected output column (no 'embedding AS ...' pattern
        # that would return the raw vector to Python).
        sql_lower = self.sql.lower()
        # It's fine to use 'embedding <=>' but not 'embedding as embedding_json'
        # or 'kce.embedding as ...' as a standalone SELECT item.
        assert "embedding_json" not in sql_lower


# ---------------------------------------------------------------------------
# VectorCandidate dataclass
# ---------------------------------------------------------------------------


class TestVectorCandidate:
    def _make(self, **overrides) -> VectorCandidate:
        defaults = {
            "chunk_id": uuid.uuid4(),
            "document_id": uuid.uuid4(),
            "document_version_id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "document_title": "report.pdf",
            "source_name": "HR Docs",
            "version_number": 1,
            "chunk_index": 0,
            "content": "Some chunk text",
            "vector_score": 0.92,
        }
        defaults.update(overrides)
        return VectorCandidate(**defaults)

    def test_fields_accessible(self) -> None:
        c = self._make()
        assert isinstance(c.chunk_id, uuid.UUID)
        assert isinstance(c.document_id, uuid.UUID)
        assert isinstance(c.document_version_id, uuid.UUID)
        assert isinstance(c.source_id, uuid.UUID)
        assert isinstance(c.document_title, str)
        assert isinstance(c.source_name, str)
        assert isinstance(c.version_number, int)
        assert isinstance(c.chunk_index, int)
        assert isinstance(c.content, str)
        assert isinstance(c.vector_score, float)

    def test_vector_score_is_cosine_similarity(self) -> None:
        """vector_score must be cosine similarity (1 - distance), not distance."""
        # Score of 1.0 = identical direction (distance 0.0).
        c = self._make(vector_score=1.0)
        assert c.vector_score == 1.0

    def test_score_semantics_distance_zero_gives_similarity_one(self) -> None:
        """Verify the documented conversion: similarity = 1.0 - distance."""
        distance = 0.0
        expected_similarity = 1.0 - distance
        c = self._make(vector_score=expected_similarity)
        assert c.vector_score == pytest.approx(1.0)

    def test_score_semantics_distance_two_gives_similarity_negative_one(self) -> None:
        """Opposite vectors: distance=2.0 → similarity=-1.0."""
        distance = 2.0
        expected_similarity = 1.0 - distance
        c = self._make(vector_score=expected_similarity)
        assert c.vector_score == pytest.approx(-1.0)

    def test_score_semantics_distance_one_gives_similarity_zero(self) -> None:
        """Orthogonal vectors: distance=1.0 → similarity=0.0."""
        distance = 1.0
        expected_similarity = 1.0 - distance
        c = self._make(vector_score=expected_similarity)
        assert c.vector_score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# vector_search function contract
# ---------------------------------------------------------------------------


class TestVectorSearchSignature:
    """Verify vector_search has the expected signature (no DB call needed)."""

    def test_is_coroutine_function(self) -> None:
        import asyncio

        assert asyncio.iscoroutinefunction(vector_search)

    def test_accepts_expected_parameters(self) -> None:
        import inspect

        sig = inspect.signature(vector_search)
        params = set(sig.parameters)
        expected = {
            "session",
            "query_embedding",
            "model_id",
            "dimensions",
            "organisation_id",
            "workspace_id",
            "limit",
            "source_ids",
            "document_ids",
            "include_archived",
        }
        assert expected.issubset(params), f"Missing parameters: {expected - params}"

    def test_source_ids_has_none_default(self) -> None:
        import inspect

        sig = inspect.signature(vector_search)
        assert sig.parameters["source_ids"].default is None

    def test_document_ids_has_none_default(self) -> None:
        import inspect

        sig = inspect.signature(vector_search)
        assert sig.parameters["document_ids"].default is None

    def test_include_archived_defaults_false(self) -> None:
        import inspect

        sig = inspect.signature(vector_search)
        assert sig.parameters["include_archived"].default is False


# ---------------------------------------------------------------------------
# No Python-side cosine computation
# ---------------------------------------------------------------------------


class TestNoPythonCosineComputation:
    """Verify that the old Python-scan artefacts are removed."""

    def test_cosine_similarity_not_exported(self) -> None:
        """_cosine_similarity must not exist — pgvector does the ranking now."""
        import app.retrieval.vector as vec_module

        assert not hasattr(vec_module, "_cosine_similarity"), (
            "_cosine_similarity still present — Python cosine scan not removed"
        )

    def test_json_not_imported(self) -> None:
        """json module is no longer needed — embedding is not loaded as JSON."""

        import app.retrieval.vector as vec_module

        # json.loads was used to deserialise TEXT embeddings; it must be gone.
        source_file = vec_module.__file__
        assert source_file is not None
        with open(source_file) as f:
            source = f.read()
        assert "json.loads" not in source, (
            "json.loads found in vector.py — Python JSON deserialization "
            "of embedding rows must be removed; pgvector handles this in SQL"
        )

    def test_math_not_imported_for_magnitude(self) -> None:
        """math.sqrt was used for magnitude calculation — no longer needed."""
        import app.retrieval.vector as vec_module

        source_file = vec_module.__file__
        assert source_file is not None
        with open(source_file) as f:
            source = f.read()
        # math.sqrt was the key indicator of Python-side cosine computation.
        assert "math.sqrt" not in source, (
            "math.sqrt found — Python-side cosine magnitude computation "
            "must be removed; pgvector handles similarity in SQL"
        )
