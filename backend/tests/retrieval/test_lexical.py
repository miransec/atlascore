"""
Unit tests for lexical retrieval helpers (app.retrieval.lexical).

LexicalCandidate dataclass tests — no database required.
lexical_search() itself is covered via integration tests.
"""

import uuid

import pytest

from app.retrieval.lexical import LexicalCandidate


class TestLexicalCandidateDataclass:
    """Structural and type-safety tests for LexicalCandidate."""

    def _make_candidate(self, **kwargs) -> LexicalCandidate:
        defaults = {
            "chunk_id": uuid.uuid4(),
            "document_id": uuid.uuid4(),
            "document_version_id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "document_title": "Test Document.pdf",
            "source_name": "Test Source",
            "version_number": 1,
            "chunk_index": 0,
            "content": "The quick brown fox jumps over the lazy dog.",
            "lexical_score": 0.42,
        }
        defaults.update(kwargs)
        return LexicalCandidate(**defaults)

    def test_construction(self) -> None:
        cand = self._make_candidate()
        assert isinstance(cand, LexicalCandidate)

    def test_chunk_id_is_uuid(self) -> None:
        cid = uuid.uuid4()
        cand = self._make_candidate(chunk_id=cid)
        assert cand.chunk_id == cid

    def test_lexical_score_stored(self) -> None:
        cand = self._make_candidate(lexical_score=0.99)
        assert cand.lexical_score == pytest.approx(0.99)

    def test_content_field_is_string(self) -> None:
        cand = self._make_candidate(content="example text")
        assert cand.content == "example text"

    def test_version_number_stored(self) -> None:
        cand = self._make_candidate(version_number=3)
        assert cand.version_number == 3

    def test_chunk_index_stored(self) -> None:
        cand = self._make_candidate(chunk_index=7)
        assert cand.chunk_index == 7

    def test_source_name_stored(self) -> None:
        cand = self._make_candidate(source_name="My Source")
        assert cand.source_name == "My Source"

    def test_document_title_stored(self) -> None:
        cand = self._make_candidate(document_title="report.pdf")
        assert cand.document_title == "report.pdf"

    def test_all_uuid_fields_are_uuid_type(self) -> None:
        cand = self._make_candidate()
        assert isinstance(cand.chunk_id, uuid.UUID)
        assert isinstance(cand.document_id, uuid.UUID)
        assert isinstance(cand.document_version_id, uuid.UUID)
        assert isinstance(cand.source_id, uuid.UUID)


class TestLexicalCandidateEquality:
    """Dataclass equality semantics."""

    def _make(self) -> LexicalCandidate:
        cid = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        return LexicalCandidate(
            chunk_id=cid,
            document_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            document_version_id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            source_id=uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
            document_title="Doc",
            source_name="Src",
            version_number=1,
            chunk_index=0,
            content="text",
            lexical_score=0.5,
        )

    def test_equal_candidates(self) -> None:
        a = self._make()
        b = self._make()
        assert a == b

    def test_different_score_not_equal(self) -> None:
        a = self._make()
        b = self._make()
        # Modify score on b via direct attribute (dataclass is mutable).
        object.__setattr__(b, "lexical_score", 0.99)
        assert a != b
