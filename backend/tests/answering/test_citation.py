"""
Unit tests for app.answering.citation.

Tests cover:
  - CitationValidator.validate — happy path (single and multiple IDs)
  - Deduplication of repeated IDs
  - Rejection of invalid E{n} pattern
  - Rejection of IDs not in the evidence packet (fabricated / stale)
  - Sorted output order (E1 < E2 < E3)
  - Excerpt generation (_make_excerpt)
  - rewrite_citations_in_answer — [E1] → [1], unknown IDs removed
"""

from __future__ import annotations

import pytest

from app.answering.citation import (
    CitationValidationError,
    CitationValidator,
    rewrite_citations_in_answer,
)
from tests.answering.conftest import make_evidence_item, make_packet


class TestCitationValidator:
    def setup_method(self) -> None:
        self.validator = CitationValidator(max_excerpt_chars=50)

    def test_single_valid_citation(self) -> None:
        packet = make_packet(items=[make_evidence_item("E1")])
        citations = self.validator.validate(["E1"], packet)
        assert len(citations) == 1
        assert citations[0].citation_id == "E1"

    def test_multiple_valid_citations_sorted(self) -> None:
        items = [make_evidence_item("E1"), make_evidence_item("E2"), make_evidence_item("E3")]
        packet = make_packet(items=items)
        # Provide out-of-order.
        citations = self.validator.validate(["E3", "E1", "E2"], packet)
        assert [c.citation_id for c in citations] == ["E1", "E2", "E3"]

    def test_deduplication(self) -> None:
        items = [make_evidence_item("E1")]
        packet = make_packet(items=items)
        citations = self.validator.validate(["E1", "E1", "E1"], packet)
        assert len(citations) == 1

    def test_invalid_pattern_raises(self) -> None:
        packet = make_packet()
        with pytest.raises(CitationValidationError, match="Invalid citation ID format"):
            self.validator.validate(["X1"], packet)

    def test_lowercase_e_rejected(self) -> None:
        packet = make_packet(items=[make_evidence_item("E1")])
        with pytest.raises(CitationValidationError, match="Invalid citation ID format"):
            self.validator.validate(["e1"], packet)

    def test_fabricated_high_id_rejected(self) -> None:
        # Packet only has E1; E999 is fabricated.
        packet = make_packet(items=[make_evidence_item("E1")])
        with pytest.raises(
            CitationValidationError, match="does not exist in the current evidence packet"
        ):
            self.validator.validate(["E999"], packet)

    def test_empty_citation_list_returns_empty(self) -> None:
        packet = make_packet()
        citations = self.validator.validate([], packet)
        assert citations == []

    def test_citation_metadata_from_evidence_item(self) -> None:
        item = make_evidence_item("E1")
        packet = make_packet(items=[item])
        citations = self.validator.validate(["E1"], packet)
        c = citations[0]
        assert c.source_id == item.source_id
        assert c.document_id == item.document_id
        assert c.source_name == item.source_name
        assert c.document_title == item.document_title
        assert c.version_number == item.version_number
        assert c.chunk_index == item.chunk_index

    def test_excerpt_bounded(self) -> None:
        long_content = "A" * 200
        item = make_evidence_item("E1", content=long_content)
        packet = make_packet(items=[item])
        citations = self.validator.validate(["E1"], packet)
        assert citations[0].excerpt is not None
        assert len(citations[0].excerpt) <= 50 + len("…")

    def test_excerpt_ellipsis_added_on_truncation(self) -> None:
        item = make_evidence_item("E1", content="A" * 200)
        packet = make_packet(items=[item])
        citations = self.validator.validate(["E1"], packet)
        assert citations[0].excerpt is not None
        assert citations[0].excerpt.endswith("…")

    def test_excerpt_none_on_empty_content(self) -> None:
        item = make_evidence_item("E1", content="")
        packet = make_packet(items=[item])
        citations = self.validator.validate(["E1"], packet)
        assert citations[0].excerpt is None

    def test_numeric_sorting_not_lexicographic(self) -> None:
        # E10 must sort after E9, not before E2 (lexicographic would put E10 before E2).
        items = [make_evidence_item(f"E{i + 1}") for i in range(10)]
        packet = make_packet(items=items)
        ids = [f"E{i + 1}" for i in range(10)]
        citations = self.validator.validate(ids, packet)
        assert citations[0].citation_id == "E1"
        assert citations[-1].citation_id == "E10"

    def test_non_string_id_raises(self) -> None:
        packet = make_packet()
        with pytest.raises(CitationValidationError):
            self.validator.validate([123], packet)  # type: ignore[list-item]


class TestRewriteCitationsInAnswer:
    def setup_method(self) -> None:
        self.validator = CitationValidator()

    def _make_citations(self, ids: list[str], packet=None) -> list:
        if packet is None:
            items = [make_evidence_item(eid) for eid in ids]
            packet = make_packet(items=items)
        return self.validator.validate(ids, packet)

    def test_single_replacement(self) -> None:
        citations = self._make_citations(["E1"])
        result = rewrite_citations_in_answer("Paris is the capital [E1].", citations)
        assert result == "Paris is the capital [1]."

    def test_multiple_replacements(self) -> None:
        citations = self._make_citations(["E1", "E2"])
        text = "A [E1] and B [E2] and C [E1]."
        result = rewrite_citations_in_answer(text, citations)
        assert result == "A [1] and B [2] and C [1]."

    def test_unknown_citation_removed(self) -> None:
        citations = self._make_citations(["E1"])
        # E5 was not validated — gets removed.
        result = rewrite_citations_in_answer("A [E1] and B [E5].", citations)
        assert result == "A [1] and B ."

    def test_empty_citations_removes_all_markers(self) -> None:
        result = rewrite_citations_in_answer("A [E1] B [E2].", [])
        assert result == "A  B ."

    def test_no_markers_passthrough(self) -> None:
        citations = self._make_citations(["E1"])
        text = "Plain answer with no citations."
        result = rewrite_citations_in_answer(text, citations)
        assert result == text

    def test_numeric_labels_follow_sorted_position(self) -> None:
        # Citations sorted E1 < E2 < E3, so [E3] → [3].
        items = [make_evidence_item(f"E{i + 1}") for i in range(3)]
        packet = make_packet(items=items)
        citations = self.validator.validate(["E1", "E2", "E3"], packet)
        result = rewrite_citations_in_answer("[E3] [E1] [E2]", citations)
        assert result == "[3] [1] [2]"
